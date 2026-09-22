import java.io.BufferedWriter;
import java.io.FileWriter;
import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;

/*
 * k-core decomposition and core-based edge weights.
 *
 * Produces the same kind of Map<MyEdge,Integer> that MyGraph.computeTruss produces
 * for trussness, so it can be fed straight into TecIndexSB.constructIndex without
 * touching the index itself.
 *
 *   core number c(v) : largest k such that v survives in the k-core
 *   edge weight      : w(u,v) = min(c(u), c(v))
 *
 * w(u,v) = min(...) is exact for k-core: the k-core is the subgraph induced on
 * {v : c(v) >= k}, and an induced subgraph contains edge (u,v) exactly when both
 * endpoints are in the vertex set.
 *
 * IMPORTANT: MyEdge does not override equals()/hashCode(), so Map<MyEdge,Integer>
 * is identity-keyed. Every weight must be stored against the canonical MyEdge
 * instance held in MyGraph.g. This class only ever uses those instances; never
 * construct a new MyEdge to look a weight up.
 */
public class CoreDecomposition {

    /*
     * Batagelj & Zaversnik bucket peeling, O(n + m + maxDegree).
     *
     * Vertices are drained in increasing current-degree order. Removing a vertex
     * decrements its surviving neighbours, which may move them into a lower bucket.
     * The running maximum k never decreases, which is what makes core[] correct.
     */
    public static Map<Integer, Integer> coreNumbers(MyGraph mg) {
        Map<Integer, Integer> deg = new HashMap<Integer, Integer>(mg.g.size() * 2);
        int maxDeg = 0;
        for (Integer v : mg.g.keySet()) {
            int d = mg.g.get(v).size();
            deg.put(v, d);
            if (d > maxDeg) {
                maxDeg = d;
            }
        }

        List<LinkedHashSet<Integer>> bins = new ArrayList<LinkedHashSet<Integer>>(maxDeg + 1);
        for (int d = 0; d <= maxDeg; d++) {
            bins.add(new LinkedHashSet<Integer>());
        }
        for (Integer v : mg.g.keySet()) {
            bins.get(deg.get(v)).add(v);
        }

        Map<Integer, Integer> core = new HashMap<Integer, Integer>(mg.g.size() * 2);
        int k = 0;
        for (int d = 0; d <= maxDeg; d++) {
            LinkedHashSet<Integer> bin = bins.get(d);
            while (!bin.isEmpty()) {
                Integer v = bin.iterator().next();
                bin.remove(v);
                if (d > k) {
                    k = d;
                }
                core.put(v, k);

                for (Integer u : mg.g.get(v).keySet()) {
                    if (core.containsKey(u)) {
                        continue; // already peeled
                    }
                    int du = deg.get(u);
                    // Guarded so a neighbour never drops below the level being drained.
                    if (du > d) {
                        bins.get(du).remove(u);
                        deg.put(u, du - 1);
                        bins.get(du - 1).add(u);
                    }
                }
            }
        }
        return core;
    }

    /*
     * Edge weights w(u,v) = min(c(u), c(v)), keyed by the graph's own MyEdge objects.
     *
     * Each undirected edge appears in both endpoints' adjacency maps as the SAME
     * MyEdge instance, so writing it twice is idempotent under identity hashing.
     */
    public static Map<MyEdge, Integer> coreWeights(MyGraph mg, Map<Integer, Integer> core) {
        Map<MyEdge, Integer> w = new HashMap<MyEdge, Integer>();
        for (Integer u : mg.g.keySet()) {
            int cu = core.get(u);
            for (Map.Entry<Integer, MyEdge> ent : mg.g.get(u).entrySet()) {
                int cv = core.get(ent.getKey());
                w.put(ent.getValue(), cu < cv ? cu : cv);
            }
        }
        return w;
    }

    public static Map<MyEdge, Integer> coreWeights(MyGraph mg) {
        return coreWeights(mg, coreNumbers(mg));
    }

    /* Comma separated "u,v,w", the format MyGraph.processLineWT expects. */
    public static void writeWeights(String path, Map<MyEdge, Integer> w) throws IOException {
        BufferedWriter bw = new BufferedWriter(new FileWriter(path));
        for (Map.Entry<MyEdge, Integer> e : w.entrySet()) {
            bw.write(Integer.toString(e.getKey().s));
            bw.write(",");
            bw.write(Integer.toString(e.getKey().t));
            bw.write(",");
            bw.write(Integer.toString(e.getValue()));
            bw.write("\n");
        }
        bw.close();
    }

    public static void writeCoreNumbers(String path, Map<Integer, Integer> core) throws IOException {
        BufferedWriter bw = new BufferedWriter(new FileWriter(path));
        for (Map.Entry<Integer, Integer> e : core.entrySet()) {
            bw.write(Integer.toString(e.getKey()));
            bw.write(",");
            bw.write(Integer.toString(e.getValue()));
            bw.write("\n");
        }
        bw.close();
    }
}
