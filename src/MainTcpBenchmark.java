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

/** Fixed-k degree-bucket comparison between EquiTruss and the TCP-style index. */
public class MainTcpBenchmark {
    private static final int WARMUP_ROUNDS = 2;

    private static final class Query {
        int vertex;
        int k;
        String bucket;
    }

    private static final class Stat {
        long equiNs;
        long tcpNs;
        long answerEdges;
        int queries;
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 3) {
            System.out.println("usage: MainTcpBenchmark <graphFile> <outDir> <queryFile> [maxQueries] [reps]");
            System.exit(1);
        }
        String graphFile = args[0];
        String outDir = args[1];
        String queryFile = args[2];
        int maxQueries = args.length > 3 ? Integer.parseInt(args[3]) : Integer.MAX_VALUE;
        int repetitions = args.length > 4 ? Integer.parseInt(args[4]) : 5;
        new File(outDir).mkdirs();

        MyGraph graph = new MyGraph();
        graph.read_GraphEdgelist(graphFile);
        long started = System.nanoTime();
        Map<Integer, Integer> core = CoreDecomposition.coreNumbers(graph);
        Map<MyEdge, Integer> weights = CoreDecomposition.coreWeights(graph, core);
        double decompositionSeconds = (System.nanoTime() - started) / 1e9;
        List<Query> queries = readQueries(queryFile, maxQueries);

        TcpIndex tcp = new TcpIndex();
        started = System.nanoTime();
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

        for (int round = 0; round < WARMUP_ROUNDS; round++) {
            for (Query query : queries) {
                tcp.findkCommunityForQuery(query.vertex, query.k);
                if (equi.vtoSGN.containsKey(query.vertex)) {
                    equi.findkCommunityForQuery(query.vertex, query.k);
                }
            }
        }

        int n = queries.size();
        long[][] equiSamples = new long[n][repetitions];
        long[][] tcpSamples = new long[n][repetitions];
        int[] communities = new int[n];
        int[] answerEdges = new int[n];
        int mismatches = 0;
        for (int round = 0; round < repetitions; round++) {
            for (int i = 0; i < n; i++) {
                Query query = queries.get(i);
                LinkedList<LinkedList<MyEdge>> tcpAnswer;
                LinkedList<LinkedList<MyEdge>> equiAnswer;
                if (((round + i) & 1) == 0) {
                    tcpAnswer = timedTcp(tcp, query, tcpSamples[i], round);
                    equiAnswer = timedEqui(equi, query, equiSamples[i], round);
                } else {
                    equiAnswer = timedEqui(equi, query, equiSamples[i], round);
                    tcpAnswer = timedTcp(tcp, query, tcpSamples[i], round);
                }
                if (round == 0) {
                    communities[i] = equiAnswer.size();
                    answerEdges[i] = edgeCount(equiAnswer);
                    if (!MainCore.canonicalize(tcpAnswer).equals(MainCore.canonicalize(equiAnswer))) {
                        mismatches++;
                        if (mismatches <= 3) {
                            System.out.printf("MISMATCH q=%d k=%d: TCP %d communities, EquiTruss %d%n",
                                    query.vertex, query.k, tcpAnswer.size(), equiAnswer.size());
                        }
                    }
                }
            }
        }

        Map<String, Stat> buckets = new LinkedHashMap<String, Stat>();
        long totalEqui = 0;
        long totalTcp = 0;
        BufferedWriter writer = new BufferedWriter(new FileWriter(outDir + "/bench.csv"));
        writer.write("bucket,vertex,k,communities,answer_edges,equitruss_ms,tcp_ms,tcp_vs_equi,reps\n");
        for (int i = 0; i < n; i++) {
            Query query = queries.get(i);
            long equiNs = median(equiSamples[i]);
            long tcpNs = median(tcpSamples[i]);
            Stat stat = buckets.computeIfAbsent(query.bucket, ignored -> new Stat());
            stat.equiNs += equiNs;
            stat.tcpNs += tcpNs;
            stat.answerEdges += answerEdges[i];
            stat.queries++;
            totalEqui += equiNs;
            totalTcp += tcpNs;
            writer.write(String.format("%s,%d,%d,%d,%d,%.6f,%.6f,%.2f,%d%n", query.bucket, query.vertex,
                    query.k, communities[i], answerEdges[i], equiNs / 1e6, tcpNs / 1e6,
                    ratio(tcpNs, equiNs), repetitions));
        }
        writer.close();

        System.out.printf("graph %s: %d vertices, %d edges, max core %d%n", graphFile, core.size(), weights.size(),
                max(core));
        System.out.printf("queries: %d | repetitions: %d | core decomposition %.4f s%n", n, repetitions,
                decompositionSeconds);
        System.out.println("=== index construction (informational; use MainIndexCompare for isolated timings) ===");
        System.out.printf("TCP-style : %.4f s | %d forest edges | %.2f MB estimated%n", tcpBuildSeconds,
                tcp.getForestEdges(), tcp.getEstimatedSizeBytes() / 1048576.0);
        System.out.printf("EquiTruss : %.4f s | %d super-nodes | %.2f MB estimated%n%n", equiBuildSeconds,
                equi.idSGN.size(), equiSizeMb);
        System.out.println("=== mean of per-query median times by degree bucket (ms) ===");
        System.out.printf("%-14s %6s %12s %12s %11s %12s%n", "bucket", "n", "EquiTruss", "TCP", "tcp/equi",
                "mean edges");
        for (Map.Entry<String, Stat> entry : buckets.entrySet()) {
            Stat stat = entry.getValue();
            double equiMs = stat.equiNs / 1e6 / stat.queries;
            double tcpMs = stat.tcpNs / 1e6 / stat.queries;
            System.out.printf("%-14s %6d %12.5f %12.5f %10.1fx %12d%n", entry.getKey(), stat.queries, equiMs,
                    tcpMs, ratio(tcpMs, equiMs), stat.answerEdges / stat.queries);
        }
        double meanEqui = totalEqui / 1e6 / n;
        double meanTcp = totalTcp / 1e6 / n;
        System.out.printf("%nOVERALL EquiTruss %.6f ms | TCP %.6f ms | TCP/EquiTruss %.1fx%n", meanEqui, meanTcp,
                ratio(meanTcp, meanEqui));
        System.out.printf("agreement: %s (%d mismatches)%n", mismatches == 0 ? "IDENTICAL" : "FAILED", mismatches);
        System.out.println("CSV: " + outDir + "/bench.csv");
        if (mismatches > 0) {
            System.exit(1);
        }
    }

    private static LinkedList<LinkedList<MyEdge>> timedTcp(TcpIndex tcp, Query query, long[] samples, int round) {
        long before = System.nanoTime();
        LinkedList<LinkedList<MyEdge>> answer = tcp.findkCommunityForQuery(query.vertex, query.k);
        samples[round] = System.nanoTime() - before;
        return answer;
    }

    private static LinkedList<LinkedList<MyEdge>> timedEqui(TecIndexSB equi, Query query, long[] samples, int round)
            throws IOException {
        if (!equi.vtoSGN.containsKey(query.vertex)) {
            samples[round] = 0;
            return new LinkedList<LinkedList<MyEdge>>();
        }
        long before = System.nanoTime();
        LinkedList<LinkedList<MyEdge>> answer = equi.findkCommunityForQuery(query.vertex, query.k);
        samples[round] = System.nanoTime() - before;
        return answer;
    }

    private static int edgeCount(LinkedList<LinkedList<MyEdge>> answer) {
        int count = 0;
        for (LinkedList<MyEdge> community : answer) {
            count += community.size();
        }
        return count;
    }

    private static long median(long[] values) {
        long[] sorted = values.clone();
        Arrays.sort(sorted);
        int n = sorted.length;
        return n % 2 == 1 ? sorted[n / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2;
    }

    private static double ratio(double numerator, double denominator) {
        return denominator == 0 ? 0 : numerator / denominator;
    }

    private static int max(Map<Integer, Integer> values) {
        int result = 0;
        for (Integer value : values.values()) {
            result = Math.max(result, value);
        }
        return result;
    }

    private static List<Query> readQueries(String path, int limit) throws IOException {
        List<Query> result = new ArrayList<Query>();
        BufferedReader reader = new BufferedReader(new FileReader(path));
        String line;
        while ((line = reader.readLine()) != null && result.size() < limit) {
            line = line.trim();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            String[] parts = line.split("[,\\s]+");
            Query query = new Query();
            query.vertex = Integer.parseInt(parts[0]);
            query.k = Integer.parseInt(parts[1]);
            query.bucket = parts.length > 2 ? parts[2] : "all";
            result.add(query);
        }
        reader.close();
        return result;
    }
}
