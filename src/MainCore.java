import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/*
 * Driver: k-core weights -> unmodified EquiTruss index -> community queries.
 *
 * Pipeline
 *   1. read graph (TAB separated edge list, as MyGraph.processLine expects)
 *   2. k-core decomposition, w(u,v) = min(c(u), c(v))
 *   3. MyGraph.createKedgeList  (already generic over any weight map)
 *   4. TecIndexSB.constructIndex  (unmodified apart from the dropLevel2 gate)
 *   5. TecIndexSB.findkCommunityForQuery  (entirely unmodified)
 *
 * Because the triangle-connectivity rule in constructIndex is left in place, a
 * "community" here is a triangle-connected component of the k-core, NOT the
 * textbook k-core community. Communities may overlap on vertices.
 *
 * Usage:
 *   MainCore <graphFile> <outDir> [queryFile]
 *   queryFile lines are "v,k"; blank lines and lines starting with # are skipped.
 */
public class MainCore {

    /* Queries discarded before timing, so the JIT has compiled the query path. */
    private static final int WARMUP_ROUNDS = 3;

    public static void main(String[] args) throws IOException {
        if (args.length < 2) {
            System.out.println("usage: MainCore <graphFile> <outDir> [queryFile]");
            System.exit(1);
        }
        String graphFile = args[0];
        String outDir = args[1];
        String queryFile = args.length > 2 ? args[2] : null;

        new File(outDir).mkdirs();

        MyGraph mg = new MyGraph();

        long t0 = System.nanoTime();
        mg.read_GraphEdgelist(graphFile);
        long tRead = System.nanoTime() - t0;

        /* ---- decomposition: timed separately from index construction ---- */
        t0 = System.nanoTime();
        Map<Integer, Integer> core = CoreDecomposition.coreNumbers(mg);
        Map<MyEdge, Integer> weights = CoreDecomposition.coreWeights(mg, core);
        long tDecomp = System.nanoTime() - t0;

        CoreDecomposition.writeCoreNumbers(outDir + "/corenumbers.txt", core);
        CoreDecomposition.writeWeights(outDir + "/coreweights.txt", weights);

        t0 = System.nanoTime();
        Map<Integer, LinkedHashSet<MyEdge>> klistdict = MyGraph.createKedgeList(weights);
        long tBucket = System.nanoTime() - t0;

        /* ---- index construction on the unmodified index ---- */
        TecIndexSB tec = new TecIndexSB();
        tec.dropLevel2 = false; // level 2 is a real 2-core level, unlike trussness 2
        t0 = System.nanoTime();
        tec.constructIndex(klistdict, weights, mg);
        long tIndex = System.nanoTime() - t0;
        // NOTE: constructIndex consumes mg (it calls removeEdge). mg is unusable now.

        tec.computeSize();
        tec.writeIndex(outDir);

        int superNodes = tec.idSGN.size();
        long superEdgeEnds = 0;
        for (Integer id : tec.SG.keySet()) {
            superEdgeEnds += tec.SG.get(id).size();
        }
        long superEdges = superEdgeEnds / 2;

        int maxCore = 0;
        for (Integer c : core.values()) {
            if (c > maxCore) {
                maxCore = c;
            }
        }

        System.out.println("=== k-core + EquiTruss index ===");
        System.out.printf("graph file                : %s%n", graphFile);
        System.out.printf("vertices                  : %d%n", mgVertexCount(core));
        System.out.printf("edges                     : %d%n", weights.size());
        System.out.printf("max core number           : %d%n", maxCore);
        System.out.println();
        System.out.printf("graph read time      (s)  : %.6f%n", tRead / 1e9);
        System.out.printf("core decomposition   (s)  : %.6f%n", tDecomp / 1e9);
        System.out.printf("weight bucketing     (s)  : %.6f%n", tBucket / 1e9);
        System.out.printf("index construction   (s)  : %.6f%n", tIndex / 1e9);
        System.out.println();
        System.out.printf("super-nodes               : %d%n", superNodes);
        System.out.printf("super-edges               : %d%n", superEdges);
        System.out.printf("compression (edges/node)  : %.3f%n", weights.size() / (double) superNodes);
        System.out.printf("index size          (bytes): %.0f%n", tec.getSize());
        System.out.println();
        System.out.println("super-nodes per level:");
        TreeMap<Integer, Integer> perLevel = new TreeMap<Integer, Integer>();
        TreeMap<Integer, Integer> edgesPerLevel = new TreeMap<Integer, Integer>();
        for (Integer id : tec.idSGN.keySet()) {
            SGN sn = tec.idSGN.get(id);
            perLevel.merge(sn.truss, 1, Integer::sum);
            edgesPerLevel.merge(sn.truss, sn.edgelist.size(), Integer::sum);
        }
        for (Integer lvl : perLevel.keySet()) {
            System.out.printf("  level %-4d nodes %-8d edges %d%n", lvl, perLevel.get(lvl), edgesPerLevel.get(lvl));
        }

        if (queryFile != null) {
            runQueries(tec, queryFile, outDir);
        }
    }

    private static int mgVertexCount(Map<Integer, Integer> core) {
        return core.size();
    }

    private static void runQueries(TecIndexSB tec, String queryFile, String outDir) throws IOException {
        List<int[]> queries = new ArrayList<int[]>();
        BufferedReader br = new BufferedReader(new FileReader(queryFile));
        String line;
        while ((line = br.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            String[] p = line.split("[,\\s]+");
            queries.add(new int[] { Integer.parseInt(p[0]), Integer.parseInt(p[1]) });
        }
        br.close();

        /* JIT warm-up. Without this the first queries are 10-100x slower and the
         * timings measure Java compiling itself rather than the algorithm. */
        for (int r = 0; r < WARMUP_ROUNDS; r++) {
            for (int[] q : queries) {
                if (tec.vtoSGN.containsKey(q[0])) {
                    tec.findkCommunityForQuery(q[0], q[1]);
                }
            }
        }

        System.out.println();
        System.out.println("=== queries ===");
        BufferedWriter bw = new BufferedWriter(new FileWriter(outDir + "/query_results.txt"));
        long total = 0;
        for (int[] q : queries) {
            int v = q[0];
            int k = q[1];
            if (!tec.vtoSGN.containsKey(v)) {
                System.out.printf("q=%d k=%d -> vertex not in index%n", v, k);
                bw.write("QUERY " + v + " " + k + " MISSING\n");
                continue;
            }
            long t0 = System.nanoTime();
            LinkedList<LinkedList<MyEdge>> com = tec.findkCommunityForQuery(v, k);
            long dt = System.nanoTime() - t0;
            total += dt;

            System.out.printf("q=%-4d k=%-3d communities=%-4d time=%.6f ms%n", v, k, com.size(), dt / 1e6);
            bw.write("QUERY " + v + " " + k + "\n");
            for (String c : canonicalize(com)) {
                bw.write("  " + c + "\n");
            }
        }
        bw.close();
        if (!queries.isEmpty()) {
            System.out.printf("mean query time: %.6f ms over %d queries%n", total / 1e6 / queries.size(),
                    queries.size());
        }
        System.out.println("results written to " + outDir + "/query_results.txt");
    }

    /*
     * Canonical text form so results can be diffed against an independent oracle:
     * each edge as "min-max", edges sorted within a community, communities sorted.
     */
    static List<String> canonicalize(LinkedList<LinkedList<MyEdge>> com) {
        List<String> out = new ArrayList<String>();
        for (LinkedList<MyEdge> c : com) {
            List<String> es = new ArrayList<String>();
            for (MyEdge e : c) {
                int a = Math.min(e.s, e.t);
                int b = Math.max(e.s, e.t);
                es.add(a + "-" + b);
            }
            Collections.sort(es);
            out.add(String.join(" ", es));
        }
        Collections.sort(out);
        return out;
    }
}
