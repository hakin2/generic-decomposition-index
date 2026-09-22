import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;

/*
 * Head-to-head: indexed community search vs the Index-Free baseline, on identical
 * queries, in a single JVM, over k-core weights.
 *
 * Both methods answer the same question by the same definition, so their outputs are
 * cross-checked for equality. Any mismatch is a bug in one of them, and since the
 * Python oracle already agrees with the indexed path, a mismatch points at the baseline.
 *
 * Order matters: IndexFree needs a live graph, and TecIndexSB.constructIndex consumes
 * the graph via removeEdge. So the baseline runs first.
 *
 * Usage:
 *   MainBenchmark <graphFile> <outDir> <queryFile> [maxQueries]
 *
 * Query file lines: "v,k" or "v,k,bucketLabel". Bucket labels are aggregated in the
 * summary, which reproduces the per-degree-percentile breakdown from the paper.
 */
public class MainBenchmark {

    private static final int WARMUP_ROUNDS = 2;

    private static class Q {
        int v;
        int k;
        String bucket;
    }

    private static class Stat {
        long indexedNs;
        long freeNs;
        int n;
        long answeredEdges;
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 3) {
            System.out.println("usage: MainBenchmark <graphFile> <outDir> <queryFile> [maxQueries]");
            System.exit(1);
        }
        String graphFile = args[0];
        String outDir = args[1];
        String queryFile = args[2];
        int maxQueries = args.length > 3 ? Integer.parseInt(args[3]) : Integer.MAX_VALUE;
        new File(outDir).mkdirs();

        MyGraph mg = new MyGraph();
        long t0 = System.nanoTime();
        mg.read_GraphEdgelist(graphFile);
        long tRead = System.nanoTime() - t0;

        t0 = System.nanoTime();
        Map<Integer, Integer> core = CoreDecomposition.coreNumbers(mg);
        Map<MyEdge, Integer> weights = CoreDecomposition.coreWeights(mg, core);
        long tDecomp = System.nanoTime() - t0;

        List<Q> queries = readQueries(queryFile, maxQueries);
        System.out.printf("graph %s: %d vertices, %d edges, max core %d%n", graphFile, core.size(),
                weights.size(), max(core));
        System.out.printf("queries: %d%n", queries.size());
        System.out.printf("graph read %.4f s | core decomposition %.4f s%n%n", tRead / 1e9, tDecomp / 1e9);

        /* ---------- baseline first: it needs the graph intact ---------- */
        IndexFree free = new IndexFree(mg, weights);
        System.out.println("running Index-Free baseline (warm-up + timed)...");
        for (int r = 0; r < WARMUP_ROUNDS; r++) {
            for (Q q : queries) {
                free.findkCommunityForQuery(q.v, q.k);
            }
        }
        Map<Integer, List<String>> freeAnswers = new LinkedHashMap<Integer, List<String>>();
        long[] freeNs = new long[queries.size()];
        for (int i = 0; i < queries.size(); i++) {
            Q q = queries.get(i);
            long s = System.nanoTime();
            LinkedList<LinkedList<MyEdge>> com = free.findkCommunityForQuery(q.v, q.k);
            freeNs[i] = System.nanoTime() - s;
            freeAnswers.put(i, MainCore.canonicalize(com));
        }

        /* ---------- then the index: construction consumes the graph ---------- */
        Map<Integer, LinkedHashSet<MyEdge>> klistdict = MyGraph.createKedgeList(weights);
        TecIndexSB tec = new TecIndexSB();
        tec.dropLevel2 = false;
        t0 = System.nanoTime();
        tec.constructIndex(klistdict, weights, mg);
        long tIndex = System.nanoTime() - t0;
        tec.computeSize();

        System.out.println("running indexed search (warm-up + timed)...");
        for (int r = 0; r < WARMUP_ROUNDS; r++) {
            for (Q q : queries) {
                if (tec.vtoSGN.containsKey(q.v)) {
                    tec.findkCommunityForQuery(q.v, q.k);
                }
            }
        }
        long[] idxNs = new long[queries.size()];
        Map<Integer, List<String>> idxAnswers = new LinkedHashMap<Integer, List<String>>();
        for (int i = 0; i < queries.size(); i++) {
            Q q = queries.get(i);
            if (!tec.vtoSGN.containsKey(q.v)) {
                idxNs[i] = 0;
                idxAnswers.put(i, new ArrayList<String>());
                continue;
            }
            long s = System.nanoTime();
            LinkedList<LinkedList<MyEdge>> com = tec.findkCommunityForQuery(q.v, q.k);
            idxNs[i] = System.nanoTime() - s;
            idxAnswers.put(i, MainCore.canonicalize(com));
        }

        /* ---------- agreement check ---------- */
        int mismatches = 0;
        for (int i = 0; i < queries.size(); i++) {
            if (!freeAnswers.get(i).equals(idxAnswers.get(i))) {
                mismatches++;
                if (mismatches <= 3) {
                    Q q = queries.get(i);
                    System.out.printf("MISMATCH q=%d k=%d: indexed %d communities, free %d%n", q.v, q.k,
                            idxAnswers.get(i).size(), freeAnswers.get(i).size());
                }
            }
        }

        /* ---------- report ---------- */
        BufferedWriter bw = new BufferedWriter(new FileWriter(outDir + "/bench.csv"));
        bw.write("bucket,vertex,k,communities,answer_edges,indexed_ms,indexfree_ms,speedup\n");
        Map<String, Stat> byBucket = new LinkedHashMap<String, Stat>();
        long totIdx = 0;
        long totFree = 0;
        for (int i = 0; i < queries.size(); i++) {
            Q q = queries.get(i);
            int edges = 0;
            for (String c : idxAnswers.get(i)) {
                edges += c.isEmpty() ? 0 : c.split(" ").length;
            }
            totIdx += idxNs[i];
            totFree += freeNs[i];
            Stat st = byBucket.get(q.bucket);
            if (st == null) {
                st = new Stat();
                byBucket.put(q.bucket, st);
            }
            st.indexedNs += idxNs[i];
            st.freeNs += freeNs[i];
            st.answeredEdges += edges;
            st.n++;
            bw.write(String.format("%s,%d,%d,%d,%d,%.6f,%.6f,%.2f%n", q.bucket, q.v, q.k,
                    idxAnswers.get(i).size(), edges, idxNs[i] / 1e6, freeNs[i] / 1e6,
                    idxNs[i] == 0 ? 0.0 : freeNs[i] / (double) idxNs[i]));
        }
        bw.close();

        System.out.println();
        System.out.println("=== index ===");
        System.out.printf("construction        : %.4f s%n", tIndex / 1e9);
        System.out.printf("super-nodes         : %d%n", tec.idSGN.size());
        System.out.printf("size                : %.2f MB%n", tec.getSize() / 1048576.0);
        System.out.println();
        System.out.println("=== query time, mean ms per bucket ===");
        System.out.printf("%-14s %7s %14s %14s %9s %12s%n", "bucket", "n", "indexed", "index-free", "speedup",
                "mean edges");
        for (String b : byBucket.keySet()) {
            Stat st = byBucket.get(b);
            double mi = st.indexedNs / 1e6 / st.n;
            double mf = st.freeNs / 1e6 / st.n;
            System.out.printf("%-14s %7d %14.4f %14.4f %9.1fx %12d%n", b, st.n, mi, mf,
                    mi == 0 ? 0 : mf / mi, st.answeredEdges / st.n);
        }
        System.out.println();
        System.out.printf("OVERALL  indexed %.4f ms | index-free %.4f ms | speedup %.1fx%n",
                totIdx / 1e6 / queries.size(), totFree / 1e6 / queries.size(),
                totIdx == 0 ? 0 : totFree / (double) totIdx);
        System.out.printf("agreement: %s (%d mismatches over %d queries)%n",
                mismatches == 0 ? "IDENTICAL" : "FAILED", mismatches, queries.size());
        System.out.println("per-query CSV: " + outDir + "/bench.csv");

        if (mismatches > 0) {
            System.exit(1);
        }
    }

    private static int max(Map<Integer, Integer> m) {
        int r = 0;
        for (Integer v : m.values()) {
            if (v > r) {
                r = v;
            }
        }
        return r;
    }

    private static List<Q> readQueries(String path, int limit) throws IOException {
        List<Q> out = new ArrayList<Q>();
        BufferedReader br = new BufferedReader(new FileReader(path));
        String line;
        while ((line = br.readLine()) != null && out.size() < limit) {
            line = line.trim();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            String[] p = line.split("[,\\s]+");
            Q q = new Q();
            q.v = Integer.parseInt(p[0]);
            q.k = Integer.parseInt(p[1]);
            q.bucket = p.length > 2 ? p[2] : "all";
            out.add(q);
        }
        br.close();
        return out;
    }
}
