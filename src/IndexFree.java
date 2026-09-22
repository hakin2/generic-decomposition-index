import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashSet;
import java.util.LinkedList;
import java.util.Map;
import java.util.Set;

/*
 * Index-Free baseline: community search with no index at all.
 *
 * Mirrors the baseline defined in Akbas & Zhao (PVLDB 2017) Section 5: start from
 * each edge incident to q whose weight is >= k, then explore triangle-connected
 * edges breadth-first until every community touching q has been found.
 *
 * Same answers as the index, same definition, but every query pays for graph
 * traversal and triangle enumeration instead of reading precomputed structure.
 * That difference is the entire value of the index, so this is the baseline that
 * makes the indexed numbers mean something.
 *
 * Edge weights are taken as given (precomputed), matching the paper, which writes
 * the baseline's filter as tau(q,ui) >= k. So this isolates the contribution of the
 * INDEX while holding the decomposition constant. To also charge the decomposition
 * to every query, add the reported decomposition time per query; that variant
 * measures the whole pipeline instead.
 *
 * Non-destructive: unlike TecIndexSB.constructIndex, this never calls removeEdge,
 * so it must run BEFORE the index is built if both share one MyGraph instance.
 */
public class IndexFree {

    private final MyGraph mg;
    private final Map<MyEdge, Integer> w;

    public IndexFree(MyGraph mg, Map<MyEdge, Integer> w) {
        this.mg = mg;
        this.w = w;
    }

    public boolean hasVertex(int q) {
        return mg.g.containsKey(q);
    }

    public LinkedList<LinkedList<MyEdge>> findkCommunityForQuery(int q, int k) {
        LinkedList<LinkedList<MyEdge>> out = new LinkedList<LinkedList<MyEdge>>();
        if (!mg.g.containsKey(q)) {
            return out;
        }

        /*
         * MyEdge has no equals()/hashCode(), so HashSet compares by identity. That is
         * what we want here: every edge exists as exactly one canonical instance inside
         * MyGraph.g, shared by both endpoints' adjacency maps.
         */
        Set<MyEdge> visited = new HashSet<MyEdge>();

        for (Integer u : mg.g.get(q).keySet()) {
            MyEdge seed = mg.g.get(q).get(u);
            if (seed == null || w.get(seed) < k || visited.contains(seed)) {
                continue;
            }

            LinkedList<MyEdge> community = new LinkedList<MyEdge>();
            Deque<MyEdge> queue = new ArrayDeque<MyEdge>();
            queue.add(seed);
            visited.add(seed);

            while (!queue.isEmpty()) {
                MyEdge e = queue.poll();
                community.add(e);

                int x = e.s;
                int y = e.t;
                // Enumerate over the lower-degree endpoint, the standard triangle trick.
                if (mg.g.get(x).size() > mg.g.get(y).size()) {
                    int tmp = x;
                    x = y;
                    y = tmp;
                }

                for (Integer ne : mg.g.get(x).keySet()) {
                    MyEdge e2 = mg.g.get(y).get(ne);
                    if (e2 == null) {
                        continue; // ne is not adjacent to y, so no triangle (also skips ne == y)
                    }
                    MyEdge e1 = mg.g.get(x).get(ne);
                    // A triangle only counts if all three of its edges clear the threshold.
                    if (w.get(e1) < k || w.get(e2) < k) {
                        continue;
                    }
                    if (visited.add(e1)) {
                        queue.add(e1);
                    }
                    if (visited.add(e2)) {
                        queue.add(e2);
                    }
                }
            }
            out.add(community);
        }
        return out;
    }
}
