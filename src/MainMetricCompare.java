import java.io.File;
import java.io.IOException;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.TreeMap;

/*
 * Builds the k-truss index and the k-core index from the same graph, with the same
 * code, in the same JVM, and prints the metrics side by side.
 *
 * This is the apples-to-apples comparison. The paper's published truss numbers were
 * measured on 2017 server hardware, so they cannot be compared directly against k-core
 * numbers measured here. Both indexes have to be built locally to say anything.
 *
 * Note the graph is loaded twice on purpose: constructIndex consumes it via removeEdge,
 * so the second metric needs a fresh copy. Reload time is reported but excluded from
 * the construction figures.
 *
 * Truss side runs entirely on her original defaults, including dropLevel2 = true.
 *
 * Usage:
 *   MainMetricCompare <graphFile> <outDir>
 */
public class MainMetricCompare {

    private static class Result {
        String metric;
        double decompSec;
        double buildSec;
        int superNodes;
        long superEdges;
        double sizeMB;
        int edges;
        int maxLevel;
        /*
         * Levels 1-2 are not comparable between the two metrics. Trussness never takes
         * the value 1, and her code discards trussness 2 (edges in no triangle) as
         * trivial. Core weights span 1..kmax with every level meaningful. So the totals
         * above are confounded, and these level >= 3 figures are the fair comparison.
         */
        int superNodesL3;
        int edgesL3;
        TreeMap<Integer, Integer> nodesPerLevel = new TreeMap<Integer, Integer>();
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 2) {
            System.out.println("usage: MainMetricCompare <graphFile> <outDir>");
            System.exit(1);
        }
        String graphFile = args[0];
        String outDir = args[1];
        new File(outDir).mkdirs();

        Result truss = run(graphFile, outDir, true);
        Result core = run(graphFile, outDir, false);

        System.out.println();
        System.out.println("================ k-truss vs k-core, same graph, same code, same machine ================");
        System.out.printf("%-30s %18s %18s%n", "", "k-truss", "k-core");
        row("edges", truss.edges, core.edges);
        row("max level (kmax)", truss.maxLevel, core.maxLevel);
        rowd("decomposition (s)", truss.decompSec, core.decompSec);
        rowd("index construction (s)", truss.buildSec, core.buildSec);
        rowd("total offline (s)", truss.decompSec + truss.buildSec, core.decompSec + core.buildSec);
        row("super-nodes", truss.superNodes, core.superNodes);
        System.out.printf("%-30s %18d %18d%n", "super-edges", truss.superEdges, core.superEdges);
        rowd("index size (MB)", truss.sizeMB, core.sizeMB);
        rowd("compression (edges/node)", truss.edges / (double) truss.superNodes,
                core.edges / (double) core.superNodes);
        System.out.println("-- level >= 3 only, the fair comparison (see Result.superNodesL3) --");
        row("super-nodes, level >= 3", truss.superNodesL3, core.superNodesL3);
        row("edges indexed, level >= 3", truss.edgesL3, core.edgesL3);
        rowd("compression, level >= 3", truss.superNodesL3 == 0 ? 0 : truss.edgesL3 / (double) truss.superNodesL3,
                core.superNodesL3 == 0 ? 0 : core.edgesL3 / (double) core.superNodesL3);
        System.out.println();
        System.out.println("super-nodes per level (level: truss / core)");
        TreeMap<Integer, int[]> merged = new TreeMap<Integer, int[]>();
        for (Integer l : truss.nodesPerLevel.keySet()) {
            merged.computeIfAbsent(l, z -> new int[2])[0] = truss.nodesPerLevel.get(l);
        }
        for (Integer l : core.nodesPerLevel.keySet()) {
            merged.computeIfAbsent(l, z -> new int[2])[1] = core.nodesPerLevel.get(l);
        }
        for (Integer l : merged.keySet()) {
            System.out.printf("  %-5d %10d / %-10d%n", l, merged.get(l)[0], merged.get(l)[1]);
        }
    }

    private static void row(String label, long a, long b) {
        System.out.printf("%-30s %18d %18d%n", label, a, b);
    }

    private static void rowd(String label, double a, double b) {
        System.out.printf("%-30s %18.4f %18.4f%n", label, a, b);
    }

    private static Result run(String graphFile, String outDir, boolean trussMode) throws IOException {
        Result r = new Result();
        r.metric = trussMode ? "truss" : "core";

        MyGraph mg = new MyGraph();
        long t0 = System.nanoTime();
        mg.read_GraphEdgelist(graphFile);
        double readSec = (System.nanoTime() - t0) / 1e9;

        Map<MyEdge, Integer> weights;
        Map<Integer, LinkedHashSet<MyEdge>> klistdict;

        t0 = System.nanoTime();
        if (trussMode) {
            weights = new HashMap<MyEdge, Integer>();
            klistdict = mg.computeTruss(outDir, weights); // path arg is unused inside
        } else {
            weights = CoreDecomposition.coreWeights(mg);
            klistdict = MyGraph.createKedgeList(weights);
        }
        r.decompSec = (System.nanoTime() - t0) / 1e9;

        r.edges = weights.size();
        for (Integer w : weights.values()) {
            if (w > r.maxLevel) {
                r.maxLevel = w;
            }
        }

        TecIndexSB tec = new TecIndexSB();
        tec.dropLevel2 = trussMode; // her original behaviour for truss, off for core
        t0 = System.nanoTime();
        tec.constructIndex(klistdict, weights, mg);
        r.buildSec = (System.nanoTime() - t0) / 1e9;

        tec.computeSize();
        r.superNodes = tec.idSGN.size();
        long ends = 0;
        for (Integer id : tec.SG.keySet()) {
            ends += tec.SG.get(id).size();
        }
        r.superEdges = ends / 2;
        r.sizeMB = tec.getSize() / 1048576.0;
        for (Integer id : tec.idSGN.keySet()) {
            SGN sn = tec.idSGN.get(id);
            r.nodesPerLevel.merge(sn.truss, 1, Integer::sum);
            if (sn.truss >= 3) {
                r.superNodesL3++;
                r.edgesL3 += sn.edgelist.size();
            }
        }

        System.out.printf("[%s] read %.4f s, decomposition %.4f s, index %.4f s, %d super-nodes, %.2f MB%n",
                r.metric, readSec, r.decompSec, r.buildSec, r.superNodes, r.sizeMB);
        return r;
    }
}
