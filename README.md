# Graph Decomposition Index
Continuation of [EquiTruss](https://github.com/esraabil/Equitruss) with **k-core weights**

## Method

For every vertex, compute its core number `c(v)`. For each edge `(u,v)`, assign:

```text
w(u,v) = min(c(u), c(v))
E_k    = {e : w(e) >= k}
```

`E_k` is exactly the edge set of the k-core. The existing index then uses these weights instead of trussness.

### Community definition

The triangle-connectivity rule is used. Therefore, a result is a **triangle-connected component of the k-core**, not a standard connected k-core community. Bridge edges can separate communities, and communities can overlap on vertices.

The index's triangle shortcut is safe for core weights:

```text
w(x,y) >= k and w(x,z) >= k  =>  w(y,z) >= k
```

All three vertices have core number at least `k`, so the third edge also has weight at least `k`.

## Implementation

- `CoreDecomposition.java`: computes core numbers and edge weights.
- `MainCore.java`: builds the index and runs queries.
- `IndexFree.java`: baseline that searches the graph without an index.
- `MainBenchmark.java`, `MainSweep.java`, `MainMetricCompare.java`: experiments.
- `tools/oracle.py`: independent correctness checker.
- `TecIndexSB.java`: keeps the original index logic, with two gated fixes:
  - `dropLevel2 = false` for k-core because level 2 is meaningful.
  - query printing is disabled during timing.

The query algorithm is unchanged apart from the logging gate.

## Run

JDK is installed through Homebrew:

```bash
JAVA=/opt/homebrew/opt/openjdk/bin
$JAVA/javac -d out src/*.java

$JAVA/java -cp out MainCore data/toy.txt results/toy data/toy_queries.txt
python3 tools/oracle.py data/toy.txt data/toy_queries.txt results/toy/query_results.txt
```

Large DBLP run:

```bash
$JAVA/java -Xms4g -Xmx4g -cp out MainCore \
  data/com-dblp.ungraph.el results/dblp data/dblp_queries.txt
```

Graph files use TAB-separated integer pairs: `u<TAB>v`. Remove SNAP comments first:

```bash
grep -v '^#' input.txt > output.el
```

Python tools require `networkx`; plotting also requires `matplotlib`.

## Attribution

The original EquiTruss files are by Esra Akbas:

> E. Akbas and P. Zhao. “Truss-Based Community Search: A Truss-Equivalence Based Indexing Approach.” PVLDB 10(11), 2017. https://doi.org/10.14778/3137628.3137640
