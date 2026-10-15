import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.LinkedHashSet;
import java.util.Map;

/**
 * Measure one k-core index construction in a fresh JVM. Running TCP-style and
 * EquiTruss in separate processes gives symmetric timing and heap state.
 */
public class MainIndexCompare {
    public static void main(String[] args) throws IOException {
        if (args.length < 3 || !(args[2].equals("tcp") || args[2].equals("equitruss"))) {
            System.out.println("usage: MainIndexCompare <graphFile> <outDir> <tcp|equitruss> [runLabel]");
            System.exit(1);
        }
        String graphFile = args[0];
        String outDir = args[1];
        String method = args[2];
        String runLabel = args.length > 3 ? args[3] : "run";
        new File(outDir).mkdirs();

        MyGraph graph = new MyGraph();
        graph.read_GraphEdgelist(graphFile);
        long started = System.nanoTime();
        Map<Integer, Integer> core = CoreDecomposition.coreNumbers(graph);
        Map<MyEdge, Integer> weights = CoreDecomposition.coreWeights(graph, core);
        double decompositionSeconds = (System.nanoTime() - started) / 1e9;

        double constructionSeconds;
        double estimatedMb;
        long structureNodes;
        long structureEdges;
        if (method.equals("tcp")) {
            TcpIndex tcp = new TcpIndex();
            started = System.nanoTime();
            tcp.constructIndex(graph, weights);
            constructionSeconds = (System.nanoTime() - started) / 1e9;
            estimatedMb = tcp.getEstimatedSizeBytes() / 1048576.0;
            structureNodes = weights.size() * 2L;
            structureEdges = tcp.getForestEdges();
        } else {
            TecIndexSB equi = new TecIndexSB();
            equi.dropLevel2 = false;
            started = System.nanoTime();
            Map<Integer, LinkedHashSet<MyEdge>> levels = MyGraph.createKedgeList(weights);
            equi.constructIndex(levels, weights, graph);
            constructionSeconds = (System.nanoTime() - started) / 1e9;
            equi.computeSize();
            // TecIndexG.computeSize counts edge IDs but not their endpoint table.
            // Add two endpoint integers per original edge for the common compact model.
            estimatedMb = (equi.getSize() + 8L * weights.size()) / 1048576.0;
            structureNodes = equi.idSGN.size();
            long ends = 0;
            for (Integer id : equi.SG.keySet()) {
                ends += equi.SG.get(id).size();
            }
            structureEdges = ends / 2;
        }

        System.out.printf("graph,%s%n", graphFile);
        System.out.printf("vertices,%d%n", core.size());
        System.out.printf("edges,%d%n", weights.size());
        System.out.printf("max_core,%d%n", max(core));
        System.out.printf("run,%s%n", runLabel);
        System.out.printf("method,%s%n", method);
        System.out.printf("core_decomposition_s,%.6f%n", decompositionSeconds);
        System.out.printf("construction_s,%.6f%n", constructionSeconds);
        System.out.printf("estimated_mb,%.6f%n", estimatedMb);
        System.out.printf("structure_nodes,%d%n", structureNodes);
        System.out.printf("structure_edges,%d%n", structureEdges);

        File csv = new File(outDir, "index_compare.csv");
        boolean writeHeader = !csv.exists() || csv.length() == 0;
        BufferedWriter writer = new BufferedWriter(new FileWriter(csv, true));
        if (writeHeader) {
            writer.write("graph,run,method,vertices,edges,max_core,core_decomposition_s,construction_s,estimated_mb,structure_nodes,structure_edges\n");
        }
        writer.write(String.format("%s,%s,%s,%d,%d,%d,%.6f,%.6f,%.6f,%d,%d%n", graphFile, runLabel, method,
                core.size(), weights.size(), max(core), decompositionSeconds, constructionSeconds, estimatedMb,
                structureNodes, structureEdges));
        writer.close();
    }

    private static int max(Map<Integer, Integer> values) {
        int result = 0;
        for (Integer value : values.values()) {
            result = Math.max(result, value);
        }
        return result;
    }
}
