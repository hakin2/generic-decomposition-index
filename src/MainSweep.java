import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;

/*
 * Vary-k sweep: the paper's second query experiment, over k-core weights.
 *
 * Two fixed query sets (H = high degree, L = low degree) are reused for every value of k,
 * so k is the only variable. Both the indexed path and the Index-Free baseline are
 * measured, giving the four series the paper plots: indexed-H, indexed-L, free-H, free-L.
 *
 * Every (k, set, method) cell is repeated and the MEDIAN of the per-repetition means is
 * reported. Single runs are not trustworthy here: indexed queries take microseconds, where
 * GC and scheduler noise easily move the mean by 50%.
 *
 * Phase order is forced by TecIndexSB.constructIndex consuming the graph via removeEdge:
 * every Index-Free measurement must finish before the index is built.
 *
 * Usage:
 *   MainSweep <graphFile> <outDir> <querySetFile> <k1,k2,...> [reps]
 *
 * Query set file lines: "vertex,set" where set is H or L.
 */
public class MainSweep {

    private static final int WARMUP_ROUNDS = 2;

    private static class Cell {
        double[] repMeans;
        long answerEdges;
        int queries;
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 4) {
            System.out.println("usage: MainSweep <graphFile> <outDir> <querySetFile> <k1,k2,...> [reps]");
            System.exit(1);
        }
        String graphFile = args[0];
        String outDir = args[1];
        String setFile = args[2];
        int[] ks = parseKs(args[3]);
        int reps = args.length > 4 ? Integer.parseInt(args[4]) : 5;
        new File(outDir).mkdirs();

        MyGraph mg = new MyGraph();
        mg.read_GraphEdgelist(graphFile);

        long t0 = System.nanoTime();
        Map<Integer, Integer> core = CoreDecomposition.coreNumbers(mg);
        Map<MyEdge, Integer> weights = CoreDecomposition.coreWeights(mg, core);
        double decompSec = (System.nanoTime() - t0) / 1e9;

        Map<String, List<Integer>> sets = readSets(setFile);
        int maxCore = 0;
        for (Integer c : core.values()) {
            if (c > maxCore) {
                maxCore = c;
            }
        }

        System.out.printf("graph %s: %d vertices, %d edges, max core %d%n", graphFile, core.size(),
                weights.size(), maxCore);
        System.out.printf("core decomposition %.4f s | k values %s | reps %d%n", decompSec, Arrays.toString(ks), reps);
        for (String s : sets.keySet()) {
            System.out.printf("  set %s: %d vertices%n", s, sets.get(s).size());
        }
        System.out.println();

        Map<String, Cell> results = new LinkedHashMap<String, Cell>();
        Map<String, List<List<String>>> freeAnswers = new LinkedHashMap<String, List<List<String>>>();

        /* ---------------- phase 1: Index-Free, needs the graph alive ---------------- */
        IndexFree free = new IndexFree(mg, weights);
        // Warm up at the largest k, where answers are smallest. Same code path, cheap.
        int warmK = ks[ks.length - 1];
        for (int r = 0; r < WARMUP_ROUNDS; r++) {
            for (List<Integer> vs : sets.values()) {
                for (Integer v : vs) {
                    free.findkCommunityForQuery(v, warmK);
                }
            }
        }
        for (int k : ks) {
            for (String s : sets.keySet()) {
                List<Integer> vs = sets.get(s);
                Cell cell = new Cell();
                cell.repMeans = new double[reps];
                cell.queries = vs.size();
                List<List<String>> answers = new ArrayList<List<String>>();
                for (int r = 0; r < reps; r++) {
                    long tot = 0;
                    for (int i = 0; i < vs.size(); i++) {
                        long s0 = System.nanoTime();
                        LinkedList<LinkedList<MyEdge>> com = free.findkCommunityForQuery(vs.get(i), k);
                        tot += System.nanoTime() - s0;
                        if (r == 0) {
                            answers.add(MainCore.canonicalize(com));
                            for (LinkedList<MyEdge> c : com) {
                                cell.answerEdges += c.size();
                            }
                        }
                    }
                    cell.repMeans[r] = tot / 1e6 / vs.size();
                }
                results.put(cellKey(k, s, "free"), cell);
                freeAnswers.put(k + "|" + s, answers);
                System.out.printf("free    k=%-4d %s median %.4f ms%n", k, s, median(cell.repMeans));
            }
        }

        /* ---------------- phase 2: build the index (consumes the graph) ---------------- */
        Map<Integer, LinkedHashSet<MyEdge>> klistdict = MyGraph.createKedgeList(weights);
        TecIndexSB tec = new TecIndexSB();
        tec.dropLevel2 = false;
        t0 = System.nanoTime();
        tec.constructIndex(klistdict, weights, mg);
        double buildSec = (System.nanoTime() - t0) / 1e9;
        tec.computeSize();
        System.out.printf("%nindex built in %.4f s, %d super-nodes, %.2f MB%n%n", buildSec, tec.idSGN.size(),
                tec.getSize() / 1048576.0);

        /* ---------------- phase 3: indexed ---------------- */
        for (int r = 0; r < WARMUP_ROUNDS; r++) {
            for (List<Integer> vs : sets.values()) {
                for (Integer v : vs) {
                    if (tec.vtoSGN.containsKey(v)) {
                        tec.findkCommunityForQuery(v, warmK);
                    }
                }
            }
        }
        int mismatches = 0;
        for (int k : ks) {
            for (String s : sets.keySet()) {
                List<Integer> vs = sets.get(s);
                Cell cell = new Cell();
                cell.repMeans = new double[reps];
                cell.queries = vs.size();
                for (int r = 0; r < reps; r++) {
                    long tot = 0;
                    for (int i = 0; i < vs.size(); i++) {
                        Integer v = vs.get(i);
                        if (!tec.vtoSGN.containsKey(v)) {
                            if (r == 0 && !freeAnswers.get(k + "|" + s).get(i).isEmpty()) {
                                mismatches++;
                            }
                            continue;
                        }
                        long s0 = System.nanoTime();
                        LinkedList<LinkedList<MyEdge>> com = tec.findkCommunityForQuery(v, k);
                        tot += System.nanoTime() - s0;
                        if (r == 0) {
                            for (LinkedList<MyEdge> c : com) {
                                cell.answerEdges += c.size();
                            }
                            if (!MainCore.canonicalize(com).equals(freeAnswers.get(k + "|" + s).get(i))) {
                                mismatches++;
                            }
                        }
                    }
                    cell.repMeans[r] = tot / 1e6 / vs.size();
                }
                results.put(cellKey(k, s, "indexed"), cell);
                System.out.printf("indexed k=%-4d %s median %.4f ms%n", k, s, median(cell.repMeans));
            }
        }

        /* ---------------- output ---------------- */
        BufferedWriter bw = new BufferedWriter(new FileWriter(outDir + "/sweep.csv"));
        bw.write("k,set,method,median_ms,min_ms,max_ms,mean_answer_edges,queries,reps\n");
        for (int k : ks) {
            for (String s : sets.keySet()) {
                for (String m : new String[] { "indexed", "free" }) {
                    Cell c = results.get(cellKey(k, s, m));
                    double[] sorted = c.repMeans.clone();
                    Arrays.sort(sorted);
                    bw.write(String.format("%d,%s,%s,%.6f,%.6f,%.6f,%d,%d,%d%n", k, s, m, median(c.repMeans),
                            sorted[0], sorted[sorted.length - 1], c.answerEdges / c.queries, c.queries, reps));
                }
            }
        }
        bw.close();

        System.out.println();
        System.out.printf("%-6s %-4s %14s %14s %10s %14s%n", "k", "set", "indexed(ms)", "free(ms)", "speedup",
                "answer edges");
        for (int k : ks) {
            for (String s : sets.keySet()) {
                double mi = median(results.get(cellKey(k, s, "indexed")).repMeans);
                double mf = median(results.get(cellKey(k, s, "free")).repMeans);
                Cell c = results.get(cellKey(k, s, "indexed"));
                System.out.printf("%-6d %-4s %14.5f %14.5f %9.1fx %14d%n", k, s, mi, mf, mi == 0 ? 0 : mf / mi,
                        c.answerEdges / c.queries);
            }
        }
        System.out.println();
        System.out.printf("agreement: %s (%d mismatches)%n", mismatches == 0 ? "IDENTICAL" : "FAILED", mismatches);
        System.out.println("CSV: " + outDir + "/sweep.csv");
        if (mismatches > 0) {
            System.exit(1);
        }
    }

    private static String cellKey(int k, String set, String method) {
        return k + "|" + set + "|" + method;
    }

    private static double median(double[] a) {
        double[] s = a.clone();
        Arrays.sort(s);
        int n = s.length;
        return n % 2 == 1 ? s[n / 2] : (s[n / 2 - 1] + s[n / 2]) / 2.0;
    }

    private static int[] parseKs(String spec) {
        String[] p = spec.split(",");
        int[] ks = new int[p.length];
        for (int i = 0; i < p.length; i++) {
            ks[i] = Integer.parseInt(p[i].trim());
        }
        Arrays.sort(ks);
        return ks;
    }

    private static Map<String, List<Integer>> readSets(String path) throws IOException {
        Map<String, List<Integer>> sets = new LinkedHashMap<String, List<Integer>>();
        BufferedReader br = new BufferedReader(new FileReader(path));
        String line;
        while ((line = br.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            String[] p = line.split("[,\\s]+");
            String set = p.length > 1 ? p[1] : "all";
            sets.computeIfAbsent(set, z -> new ArrayList<Integer>()).add(Integer.parseInt(p[0]));
        }
        br.close();
        return sets;
    }
}
