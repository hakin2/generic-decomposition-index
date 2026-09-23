# k-Core on the EquiTruss Index

## Purpose

This implementation applies k-core decomposition to the existing EquiTruss index without redesigning its core structure.

## Edge weights

Let `c(v)` be the core number of vertex `v`. Each edge `(u,v)` receives the weight:

```text
w(u,v) = min(c(u), c(v))
```

At threshold `k`, the selected edges are:

```text
E_k = {e : w(e) >= k}
```

This is  the edge set of the k-core. An edge belongs to the k-core only when both endpoints have core number at least `k`.

The complete pipeline is:

```text
graph -> core decomposition -> edge weights -> existing index -> queries
```

## Community definition

The EquiTruss triangle-connectivity rule is retained. Therefore, a result is a triangle-connected component of the k-core.

Two edges belong to the same community when they can be connected through a sequence of triangles whose edges satisfy the threshold. As a result:

- A bridge edge can separate two otherwise connected dense regions.
- Communities can overlap on vertices.
- A k-core can contain multiple triangle-connected communities.

## Triangle consistency

The index can merge two threshold edges that share a triangle without separately checking the triangle's third edge:

```text
w(x,y) >= k and w(x,z) >= k
=> c(x), c(y), c(z) >= k
=> w(y,z) >= k
```

Therefore, a triangle cannot contain two edges at or above `k` and one edge below `k` when core-derived weights are used.

## Implementation

### Core decomposition

`CoreDecomposition.java` computes vertex core numbers using bucket-based peeling in `O(|V| + |E|)` time. It then assigns each graph edge the minimum core number of its endpoints.

### Index construction

The existing index already accepts an edge-to-level map. The core-weight map is passed to the same bucketing and construction procedures used for trussness:

```text
CoreDecomposition.coreNumbers(graph)
CoreDecomposition.coreWeights(graph, coreNumbers)
MyGraph.createKedgeList(weights)
TecIndexSB.constructIndex(levels, weights, graph)
```

## Main files

| File | Purpose |
|---|---|
| `src/CoreDecomposition.java` | Computes core numbers and edge weights |
| `src/MainCore.java` | Builds the core-weighted index and runs queries |
| `src/TecIndexSB.java` | Existing index with the two gated adjustments |
| `src/MyGraph.java` | Reads and stores the graph |
| `src/SGN.java` | Represents index super-nodes |

## Results

The experiments compare k-core with k-truss during index preparation and compare indexed queries with direct graph search. All times were measured on the same machine.

### k-core compared with k-truss

| Metric | GrQc truss | GrQc core | DBLP truss | DBLP core |
|---|---:|---:|---:|---:|
| decomposition (s) | 0.159 | **0.033** | 8.630 | **1.845** |
| index construction (s) | 0.057 | **0.026** | 2.177 | **1.745** |
| total preparation (s) | 0.216 | **0.058** | 10.807 | **3.590** |
| estimated index size (MB) | **0.141** | 0.208 | **11.15** | 15.13 |
| super-nodes | 1,569 | 3,387 | 126,904 | 232,067 |
| compression at level >= 3 | 8.21 | 8.08 | 7.70 | 6.58 |


### Indexed queries compared with Index-Free

The Index-Free method searches triangle-connected edges directly in the graph. The table shows overall results at `k = 5`; percentile rows are omitted.

| Graph | Queries | Non-empty | Indexed mean (ms) | Index-Free mean (ms) | Speedup |
|---|---:|---:|---:|---:|---:|
| ca-GrQc | 1,000 | 179 | 0.00674 | 0.70706 | **104.9x** |
| com-DBLP | 100 | 29 | 5.4506 | 198.4502 | **36.4x** |

### Indexed queries at different k values

The same high-degree query set (`H`) is reused at every `k`. Times are mean milliseconds per query, reported as the median across repeated runs.

#### ca-GrQc

| k | Indexed | Index-Free | Speedup | Mean answer edges |
|---:|---:|---:|---:|---:|
| 2 | 0.0557 | 4.1517 | **74.6x** | 2,212 |
| 3 | 0.0458 | 3.8326 | **83.8x** | 2,121 |
| 5 | 0.0107 | 2.2873 | **213.5x** | 1,013 |
| 8 | 0.0020 | 0.6969 | **347.2x** | 296 |
| 12 | 0.000534 | 0.1939 | **363.2x** | 80 |
| 20 | 0.000474 | 0.1823 | **384.6x** | 68 |
| 30 | 0.000509 | 0.1627 | **319.6x** | 55 |
| 43 | 0.000294 | 0.0335 | **113.9x** | 9 |

#### com-DBLP

| k | Indexed | Index-Free | Speedup | Mean answer edges |
|---:|---:|---:|---:|---:|
| 2 | 45.2486 | 947.8981 | **20.9x** | 408,885 |
| 5 | 23.4039 | 815.5613 | **34.8x** | 300,410 |
| 10 | 0.3275 | 52.2829 | **159.6x** | 12,493 |
| 20 | 0.000496 | 0.001113 | **2.2x** | 0 |

At `k = 20`, the sampled DBLP queries return no communities, so the speedup is not meaningful. To evaluate high `k`, vertices from the deep core were sampled separately.

#### com-DBLP deep-core queries

| k | Indexed | Index-Free | Speedup | Mean answer edges |
|---:|---:|---:|---:|---:|
| 40 | 0.0842 | 65.8459 | **781.9x** | 6,886 |
| 60 | 0.0589 | 73.8700 | **1,253.5x** | 6,098 |
| 80 | 0.0541 | 70.7700 | **1,308.4x** | 6,054 |
| 90 | 0.0524 | 70.1618 | **1,340.2x** | 6,054 |
| 113 | 0.0443 | 52.4444 | **1,183.8x** | 4,508 |


## Attribution

The original EquiTruss implementation is from [esraabil/Equitruss](https://github.com/esraabil/Equitruss), accompanying:

> E. Akbas and P. Zhao. “Truss-Based Community Search: A Truss-Equivalence Based Indexing Approach.” PVLDB 10(11), 1298–1309, 2017. https://doi.org/10.14778/3137628.3137640
