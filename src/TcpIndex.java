import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.IdentityHashMap;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;

/**
 * TCP-Index-style maximum-spanning-forest index adapted to arbitrary integer
 * edge levels.
 *
 * Based on Algorithms 3 and 4 in Huang et al., "Querying K-Truss Community in
 * Large and Dynamic Graphs" (SIGMOD 2014). This is an independent
 * implementation, not source code from that project.
 *
 * For each center vertex x, the local graph has one node for every incident
 * edge (x,y). A triangle (x,y,z) adds a local link between (x,y) and (x,z)
 * with level min(w(x,y), w(x,z), w(y,z)). A maximum spanning forest preserves
 * every threshold-connected component of that local graph. Query processing
 * traverses those forest links and reverses each discovered directed edge to
 * continue expansion around its other endpoint.
 *
 * The class is intentionally metric-agnostic. In this project w(e) is the
 * k-core-derived level min(c(u), c(v)).
 */
public class TcpIndex {

    private static final class Candidate {
        final int localA;
        final int localB;
        final int directedA;
        final int directedB;
        final int level;

        Candidate(int localA, int localB, int directedA, int directedB, int level) {
            this.localA = localA;
            this.localB = localB;
            this.directedA = directedA;
            this.directedB = directedB;
            this.level = level;
        }
    }

    private static final class Dsu {
        final int[] parent;
        final byte[] rank;

        Dsu(int n) {
            parent = new int[n];
            rank = new byte[n];
            for (int i = 0; i < n; i++) {
                parent[i] = i;
            }
        }

        int find(int x) {
            int root = x;
            while (parent[root] != root) {
                root = parent[root];
            }
            while (parent[x] != x) {
                int next = parent[x];
                parent[x] = root;
                x = next;
            }
            return root;
        }

        boolean union(int a, int b) {
            int ra = find(a);
            int rb = find(b);
            if (ra == rb) {
                return false;
            }
            if (rank[ra] < rank[rb]) {
                parent[ra] = rb;
            } else if (rank[ra] > rank[rb]) {
                parent[rb] = ra;
            } else {
                parent[rb] = ra;
                rank[ra]++;
            }
            return true;
        }
    }

    private static final class IntVector {
        int[] values = new int[16];
        int size;

        void add(int value) {
            if (size == values.length) {
                values = Arrays.copyOf(values, values.length * 2);
            }
            values[size++] = value;
        }

        int get(int index) {
            return values[index];
        }
    }

    private static final class IntQueue {
        int[] values = new int[64];
        int head;
        int tail;

        void clear() {
            head = 0;
            tail = 0;
        }

        void add(int value) {
            if (tail == values.length) {
                if (head > 0) {
                    int remaining = tail - head;
                    System.arraycopy(values, head, values, 0, remaining);
                    head = 0;
                    tail = remaining;
                } else {
                    values = Arrays.copyOf(values, values.length * 2);
                }
            }
            values[tail++] = value;
        }

        boolean isEmpty() {
            return head == tail;
        }

        int remove() {
            return values[head++];
        }
    }

    private final Map<Integer, int[]> seedsByVertex = new HashMap<Integer, int[]>();
    private MyEdge[] edges;
    private int[] edgeLevels;
    private int[] offsets;
    private int[] neighbors;
    private int[] linkLevels;
    private int[] directedMarks;
    private int[] edgeMarks;
    private int queryMark;
    private int forestEdges;
    private long estimatedSizeBytes;

    public void constructIndex(MyGraph graph, Map<MyEdge, Integer> weights) {
        IdentityHashMap<MyEdge, Integer> edgeIds = new IdentityHashMap<MyEdge, Integer>();
        List<MyEdge> edgeList = new ArrayList<MyEdge>(weights.size());

        for (Integer x : graph.g.keySet()) {
            for (MyEdge edge : graph.g.get(x).values()) {
                if (!edgeIds.containsKey(edge)) {
                    edgeIds.put(edge, edgeList.size());
                    edgeList.add(edge);
                }
            }
        }

        edges = edgeList.toArray(new MyEdge[edgeList.size()]);
        edgeLevels = new int[edges.length];
        for (int id = 0; id < edges.length; id++) {
            Integer level = weights.get(edges[id]);
            if (level == null) {
                throw new IllegalArgumentException("missing level for a graph edge");
            }
            edgeLevels[id] = level;
        }

        for (Integer x : graph.g.keySet()) {
            int[] seeds = new int[graph.g.get(x).size()];
            int p = 0;
            for (MyEdge edge : graph.g.get(x).values()) {
                seeds[p++] = directedId(x, edgeIds.get(edge), edge);
            }
            seedsByVertex.put(x, seeds);
        }

        IntVector linkA = new IntVector();
        IntVector linkB = new IntVector();
        IntVector linkWeight = new IntVector();

        for (Map.Entry<Integer, int[]> entry : seedsByVertex.entrySet()) {
            int center = entry.getKey();
            int[] localNodes = entry.getValue();
            if (localNodes.length < 2) {
                continue;
            }

            List<Candidate> candidates = new ArrayList<Candidate>();
            for (int i = 0; i < localNodes.length; i++) {
                int edgeAId = localNodes[i] >>> 1;
                int y = otherEndpoint(center, edges[edgeAId]);
                for (int j = i + 1; j < localNodes.length; j++) {
                    int edgeBId = localNodes[j] >>> 1;
                    int z = otherEndpoint(center, edges[edgeBId]);
                    MyEdge yz = graph.g.get(y).get(z);
                    if (yz == null) {
                        continue;
                    }
                    Integer yzId = edgeIds.get(yz);
                    if (yzId == null) {
                        throw new IllegalStateException("triangle edge is not canonical");
                    }
                    int level = Math.min(edgeLevels[edgeAId], Math.min(edgeLevels[edgeBId], edgeLevels[yzId]));
                    candidates.add(new Candidate(i, j, localNodes[i], localNodes[j], level));
                }
            }

            candidates.sort(Comparator.comparingInt((Candidate c) -> c.level).reversed());
            Dsu dsu = new Dsu(localNodes.length);
            for (Candidate candidate : candidates) {
                if (dsu.union(candidate.localA, candidate.localB)) {
                    linkA.add(candidate.directedA);
                    linkB.add(candidate.directedB);
                    linkWeight.add(candidate.level);
                }
            }
        }

        forestEdges = linkA.size;
        buildCsr(edges.length * 2, linkA, linkB, linkWeight);
        directedMarks = new int[edges.length * 2];
        edgeMarks = new int[edges.length];

        // Common compact logical model used in the report:
        //   edge table (two endpoints + level)       : 12 bytes per edge
        //   per-vertex incidence lists               :  8 bytes per edge + 8 per vertex
        //   CSR offsets for two directed incidences  :  8 bytes per edge + 4
        //   bidirectional forest adjacency           : 16 bytes per forest edge
        // Query-local mark arrays are workspace, not retained index data.
        estimatedSizeBytes = 28L * edges.length + 16L * forestEdges + 8L * seedsByVertex.size() + 4L;
    }

    private void buildCsr(int directedNodes, IntVector linkA, IntVector linkB, IntVector weights) {
        int[] degree = new int[directedNodes];
        for (int i = 0; i < linkA.size; i++) {
            degree[linkA.get(i)]++;
            degree[linkB.get(i)]++;
        }

        offsets = new int[directedNodes + 1];
        for (int i = 0; i < directedNodes; i++) {
            offsets[i + 1] = offsets[i] + degree[i];
        }
        neighbors = new int[offsets[directedNodes]];
        linkLevels = new int[offsets[directedNodes]];
        int[] cursor = offsets.clone();
        for (int i = 0; i < linkA.size; i++) {
            int a = linkA.get(i);
            int b = linkB.get(i);
            int level = weights.get(i);
            int pa = cursor[a]++;
            int pb = cursor[b]++;
            neighbors[pa] = b;
            linkLevels[pa] = level;
            neighbors[pb] = a;
            linkLevels[pb] = level;
        }
    }

    public boolean hasVertex(int query) {
        return seedsByVertex.containsKey(query);
    }

    public LinkedList<LinkedList<MyEdge>> findkCommunityForQuery(int query, int k) {
        LinkedList<LinkedList<MyEdge>> result = new LinkedList<LinkedList<MyEdge>>();
        int[] seeds = seedsByVertex.get(query);
        if (seeds == null) {
            return result;
        }

        int mark = nextQueryMark();
        IntQueue queue = new IntQueue();
        for (int seed : seeds) {
            int edgeId = seed >>> 1;
            if (edgeLevels[edgeId] < k || directedMarks[seed] == mark) {
                continue;
            }

            LinkedList<MyEdge> community = new LinkedList<MyEdge>();
            queue.clear();
            queue.add(seed);
            while (!queue.isEmpty()) {
                int directed = queue.remove();
                if (directedMarks[directed] == mark) {
                    continue;
                }
                directedMarks[directed] = mark;

                int currentEdge = directed >>> 1;
                if (edgeLevels[currentEdge] < k) {
                    continue;
                }
                if (edgeMarks[currentEdge] != mark) {
                    edgeMarks[currentEdge] = mark;
                    community.add(edges[currentEdge]);
                }

                int reverse = directed ^ 1;
                if (directedMarks[reverse] != mark) {
                    queue.add(reverse);
                }
                for (int p = offsets[directed]; p < offsets[directed + 1]; p++) {
                    if (linkLevels[p] >= k && directedMarks[neighbors[p]] != mark) {
                        queue.add(neighbors[p]);
                    }
                }
            }
            if (!community.isEmpty()) {
                result.add(community);
            }
        }
        return result;
    }

    private int nextQueryMark() {
        queryMark++;
        if (queryMark == 0) {
            Arrays.fill(directedMarks, 0);
            Arrays.fill(edgeMarks, 0);
            queryMark = 1;
        }
        return queryMark;
    }

    private int directedId(int center, int edgeId, MyEdge edge) {
        if (edge.s == center) {
            return edgeId << 1;
        }
        if (edge.t == center) {
            return (edgeId << 1) | 1;
        }
        throw new IllegalArgumentException("edge is not incident to center");
    }

    private int otherEndpoint(int center, MyEdge edge) {
        if (edge.s == center) {
            return edge.t;
        }
        if (edge.t == center) {
            return edge.s;
        }
        throw new IllegalArgumentException("edge is not incident to center");
    }

    public int getForestEdges() {
        return forestEdges;
    }

    public long getEstimatedSizeBytes() {
        return estimatedSizeBytes;
    }
}
