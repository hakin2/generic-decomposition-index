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

/** Vary-k H/L comparison between EquiTruss and the TCP-style index. */
public class MainTcpSweep {
    private static final int WARMUP_ROUNDS = 2;

    private static final class Cell {
        double[] repetitionMeans;
        long answerEdges;
        int queries;
    }

    private static final class Pair {
        final Cell equi;
        final Cell tcp;
        final int mismatches;

        Pair(Cell equi, Cell tcp, int mismatches) {
            this.equi = equi;
            this.tcp = tcp;
            this.mismatches = mismatches;
        }
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 4) {
            System.out.println("usage: MainTcpSweep <graphFile> <outDir> <querySetFile> <k1,k2,...> [reps]");
            System.exit(1);
        }
        String graphFile = args[0];
        String outDir = args[1];
        String queryFile = args[2];
        int[] ks = parseKs(args[3]);
        int repetitions = args.length > 4 ? Integer.parseInt(args[4]) : 5;
        new File(outDir).mkdirs();

        MyGraph graph = new MyGraph();
        graph.read_GraphEdgelist(graphFile);
        Map<Integer, Integer> core = CoreDecomposition.coreNumbers(graph);
        Map<MyEdge, Integer> weights = CoreDecomposition.coreWeights(graph, core);
        Map<String, List<Integer>> sets = readSets(queryFile);

        TcpIndex tcp = new TcpIndex();
        long started = System.nanoTime();
        tcp.constructIndex(graph, weights);
        double tcpBuildSeconds = (System.nanoTime() - started) / 1e9;

        TecIndexSB equi = new TecIndexSB();
        equi.dropLevel2 = false;
        started = System.nanoTime();
        Map<Integer, LinkedHashSet<MyEdge>> levels = MyGraph.createKedgeList(weights);
        equi.constructIndex(levels, weights, graph);
        double equiBuildSeconds = (System.nanoTime() - started) / 1e9;
        equi.computeSize();
        double equiSizeMb = (equi.getSize() + 8L * weights.size()) / 1048576.0;
        System.gc();

        int warmK = ks[ks.length - 1];
        for (int round = 0; round < WARMUP_ROUNDS; round++) {
            for (List<Integer> vertices : sets.values()) {
                for (Integer vertex : vertices) {
                    tcp.findkCommunityForQuery(vertex, warmK);
                    if (equi.vtoSGN.containsKey(vertex)) {
                        equi.findkCommunityForQuery(vertex, warmK);
                    }
                }
            }
        }

        Map<String, Cell> results = new LinkedHashMap<String, Cell>();
        int mismatches = 0;
        for (int k : ks) {
            for (Map.Entry<String, List<Integer>> entry : sets.entrySet()) {
                Pair pair = measurePair(tcp, equi, entry.getValue(), k, repetitions);
                results.put(key(k, entry.getKey(), "tcp"), pair.tcp);
                results.put(key(k, entry.getKey(), "equitruss"), pair.equi);
                mismatches += pair.mismatches;
            }
        }

        BufferedWriter writer = new BufferedWriter(new FileWriter(outDir + "/sweep.csv"));
        writer.write("k,set,method,median_ms,min_ms,max_ms,mean_answer_edges,total_answer_edges,queries,reps,query_fingerprint\n");
        for (int k : ks) {
            for (String set : sets.keySet()) {
                String fingerprint = ExperimentFingerprint.forVertices(set, sets.get(set));
                for (String method : new String[] { "equitruss", "tcp" }) {
                    Cell cell = results.get(key(k, set, method));
                    double[] sorted = cell.repetitionMeans.clone();
                    Arrays.sort(sorted);
                    writer.write(String.format("%d,%s,%s,%.6f,%.6f,%.6f,%d,%d,%d,%d,%s%n", k, set, method,
                            median(cell.repetitionMeans), sorted[0], sorted[sorted.length - 1],
                            cell.answerEdges / cell.queries, cell.answerEdges, cell.queries, repetitions, fingerprint));
                }
            }
        }
        writer.close();

        System.out.printf("graph %s: %d vertices, %d edges, max core %d%n", graphFile, core.size(), weights.size(),
                max(core));
        System.out.printf("k values %s | repetitions %d%n", Arrays.toString(ks), repetitions);
        System.out.printf("TCP-style index: %.4f s | %d forest edges | %.2f MB estimated%n", tcpBuildSeconds,
                tcp.getForestEdges(), tcp.getEstimatedSizeBytes() / 1048576.0);
        System.out.printf("EquiTruss index: %.4f s | %d super-nodes | %.2f MB estimated%n%n", equiBuildSeconds,
                equi.idSGN.size(), equiSizeMb);
        System.out.printf("%-5s %-3s %12s %12s %11s %12s%n", "k", "set", "EquiTruss", "TCP", "tcp/equi",
                "answer edges");
        for (int k : ks) {
            for (String set : sets.keySet()) {
                Cell equiCell = results.get(key(k, set, "equitruss"));
                double equiMs = median(equiCell.repetitionMeans);
                double tcpMs = median(results.get(key(k, set, "tcp")).repetitionMeans);
                System.out.printf("%-5d %-3s %12.5f %12.5f %10.1fx %12d%n", k, set, equiMs, tcpMs,
                        ratio(tcpMs, equiMs), equiCell.answerEdges / equiCell.queries);
            }
        }
        System.out.printf("%nagreement: %s (%d mismatches)%n", mismatches == 0 ? "IDENTICAL" : "FAILED",
                mismatches);
        System.out.println("CSV: " + outDir + "/sweep.csv");
        if (mismatches > 0) {
            System.exit(1);
        }
    }

    private static Pair measurePair(TcpIndex tcp, TecIndexSB equi, List<Integer> vertices, int k, int repetitions)
            throws IOException {
        Cell equiCell = new Cell();
        Cell tcpCell = new Cell();
        equiCell.queries = vertices.size();
        tcpCell.queries = vertices.size();
        equiCell.repetitionMeans = new double[repetitions];
        tcpCell.repetitionMeans = new double[repetitions];
        int mismatches = 0;

        for (int round = 0; round < repetitions; round++) {
            long equiTotal = 0;
            long tcpTotal = 0;
            for (int i = 0; i < vertices.size(); i++) {
                int vertex = vertices.get(i);
                LinkedList<LinkedList<MyEdge>> tcpAnswer;
                LinkedList<LinkedList<MyEdge>> equiAnswer;
                if (((round + i) & 1) == 0) {
                    long before = System.nanoTime();
                    tcpAnswer = tcp.findkCommunityForQuery(vertex, k);
                    tcpTotal += System.nanoTime() - before;
                    before = System.nanoTime();
                    equiAnswer = equi.vtoSGN.containsKey(vertex) ? equi.findkCommunityForQuery(vertex, k)
                            : new LinkedList<LinkedList<MyEdge>>();
                    equiTotal += System.nanoTime() - before;
                } else {
                    long before = System.nanoTime();
                    equiAnswer = equi.vtoSGN.containsKey(vertex) ? equi.findkCommunityForQuery(vertex, k)
                            : new LinkedList<LinkedList<MyEdge>>();
                    equiTotal += System.nanoTime() - before;
                    before = System.nanoTime();
                    tcpAnswer = tcp.findkCommunityForQuery(vertex, k);
                    tcpTotal += System.nanoTime() - before;
                }
                if (round == 0) {
                    equiCell.answerEdges += edgeCount(equiAnswer);
                    tcpCell.answerEdges += edgeCount(tcpAnswer);
                    if (!MainCore.canonicalize(tcpAnswer).equals(MainCore.canonicalize(equiAnswer))) {
                        mismatches++;
                    }
                }
            }
            equiCell.repetitionMeans[round] = equiTotal / 1e6 / vertices.size();
            tcpCell.repetitionMeans[round] = tcpTotal / 1e6 / vertices.size();
        }
        return new Pair(equiCell, tcpCell, mismatches);
    }

    private static long edgeCount(LinkedList<LinkedList<MyEdge>> answer) {
        long count = 0;
        for (LinkedList<MyEdge> community : answer) {
            count += community.size();
        }
        return count;
    }

    private static String key(int k, String set, String method) {
        return k + "|" + set + "|" + method;
    }

    private static double ratio(double numerator, double denominator) {
        return denominator == 0 ? 0 : numerator / denominator;
    }

    private static double median(double[] values) {
        double[] sorted = values.clone();
        Arrays.sort(sorted);
        int n = sorted.length;
        return n % 2 == 1 ? sorted[n / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2.0;
    }

    private static int[] parseKs(String value) {
        String[] parts = value.split(",");
        int[] result = new int[parts.length];
        for (int i = 0; i < parts.length; i++) {
            result[i] = Integer.parseInt(parts[i].trim());
        }
        Arrays.sort(result);
        return result;
    }

    private static int max(Map<Integer, Integer> values) {
        int result = 0;
        for (Integer value : values.values()) {
            result = Math.max(result, value);
        }
        return result;
    }

    private static Map<String, List<Integer>> readSets(String path) throws IOException {
        Map<String, List<Integer>> result = new LinkedHashMap<String, List<Integer>>();
        BufferedReader reader = new BufferedReader(new FileReader(path));
        String line;
        while ((line = reader.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            String[] parts = line.split("[,\\s]+");
            String set = parts.length > 1 ? parts[1] : "all";
            result.computeIfAbsent(set, ignored -> new ArrayList<Integer>()).add(Integer.parseInt(parts[0]));
        }
        reader.close();
        return result;
    }
}
