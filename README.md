# k-core weights on the EquiTruss index

> **Looking for the write-up to share?** See **[REPORT.md](REPORT.md)** — a clean summary of
> the implementation and all experimental results. This file is developer notes: layout,
> gotchas, and the exact patch listing.

Task 2 of the generic-indexing-framework project: feed **k-core** derived edge weights
into Esra Akbas's existing k-truss index and check it still produces correct answers.

Baseline code is [esraabil/Equitruss](https://github.com/esraabil/Equitruss), the
implementation of Akbas & Zhao, *Truss-Based Community Search: a Truss-Equivalence
Based Indexing Approach*, PVLDB 10(11), 2017.

## What is being claimed

The index only ever consumes two things from a decomposition:

- **(A)** an integer weight per edge, its level
- **(B)** a rule for when two edges count as adjacent

For k-truss those are trussness and triangle-sharing. Swap (A) for k-core weights and
the index should work untouched.

```
w(u,v) = min(c(u), c(v))        c(v) = core number of v
E_k    = { e : w(e) >= k }      = exactly the edge set of the k-core
```

The `min` is exact: the k-core is the subgraph induced on `{v : c(v) >= k}`, and an
induced subgraph contains an edge precisely when both endpoints are in the vertex set.

### What a "community" means here

Rule (B) is deliberately left as triangle-sharing. So a community at threshold *k* is a
**triangle-connected component of the k-core**, not a textbook k-core community.

This is a design choice, not an oversight. Consequences:

- Answers are **tighter** than real k-core communities. Two dense blobs joined by a
  bridge are one k-core community but two communities here, because the bridge lies in
  no triangle.
- Communities **overlap on vertices**, which plain connectivity would not give. A vertex
  in two triangle-dense regions is returned in both.
- Edges of the k-core that lie in no triangle come back as singleton communities.

Write it up under its own name. Claiming it reproduces k-core communities would be wrong.

## Result: core weights are triangle-consistent

`constructIndex` merges two same-level edges that share a triangle **without checking
the triangle's third edge**. Under core weights that shortcut is provably safe:

> If `w(x,y) >= k` and `w(x,z) >= k` then `c(x), c(y), c(z) >= k`,
> so `w(y,z) = min(c(y), c(z)) >= k`.

A triangle can never have two heavy edges and one light one. Any triangle touching `E_k`
is entirely inside `E_k`.

Trussness carries no such guarantee, because it is assigned per edge rather than derived
from endpoint values by `min`. This is a structural difference between the two weightings
and belongs in the Task 4 analysis. `tools/oracle.py` asserts the property on every graph
it runs against; it held on all of them, including the full 1M-edge DBLP graph.

## Layout

```
src/     Esra's 5 original files + 2 new ones
data/    graphs (TAB separated) and query files (v,k per line)
tools/   oracle.py, independent brute-force verifier
results/ index files, query results
PATCHES.diff   exact diff against the original
```

| file | status |
|---|---|
| `MyGraph.java`, `SGN.java`, `TecIndexG.java`, `MainF.java` | **unchanged** |
| `TecIndexSB.java` | 3 gated patches, defaults preserve original truss behaviour |
| `CoreDecomposition.java` | new: peeling + weights |
| `MainCore.java` | new: driver, timing, batch queries |

### The three patches

All are flag-gated and default to the original behaviour, so truss runs are unaffected.

1. **`dropLevel2` flag.** The original does `if (klistdict.containsKey(2)) klistdict.remove(2);`
   Trussness 2 means "in no triangle", so discarding it is right for truss. Under core
   weights a level-2 edge is a real 2-core member, and dropping it silently truncates
   every answer at k <= 2. `MainCore` sets this false.
2. **The gate itself**, one line.
3. **`verboseQuery` flag.** Two `System.out.print` calls sit inside `findkCommunityForQuery`,
   i.e. inside the timed region. Console I/O dwarfs the traversal. Gating them cut mean
   toy-graph query time from 0.096 ms to 0.023 ms, a 4x measurement artifact. Off by default.

No change to any index logic. Nothing was added to make k-core work; the weight map was
already a parameter of `constructIndex`, and `MyGraph.createKedgeList` already buckets an
arbitrary weight map. **`findkCommunityForQuery` is byte-for-byte unmodified apart from
the gated print**, so query processing is genuinely weight-agnostic.

## Running it

JDK is keg-only under Homebrew, so use the full path:

```bash
JAVA=/opt/homebrew/opt/openjdk/bin
$JAVA/javac -d out src/*.java

# toy graph
$JAVA/java -cp out MainCore data/toy.txt results/toy data/toy_queries.txt
python3 tools/oracle.py data/toy.txt data/toy_queries.txt results/toy/query_results.txt

# 1M edges, pin the heap so GC behaviour does not drift between runs
$JAVA/java -Xms4g -Xmx4g -cp out MainCore data/com-dblp.ungraph.el results/dblp data/dblp_queries.txt
```

Input format is **TAB separated** `u v` (matches `MyGraph.processLine` and SNAP files).
Strip SNAP comment lines first: `grep -v '^#' in.txt > out.el`. The *weighted* reader
`read_GraphEdgelistWt` uses **commas** instead; `CoreDecomposition.writeWeights` emits
that format.

## Verification

`tools/oracle.py` shares no code with the Java side. It recomputes core numbers with
networkx, rebuilds `E_k`, unions edges through triangles with a disjoint-set structure,
and diffs the canonicalised communities.

| graph | edges | queries verified | result |
|---|---|---|---|
| toy | 19 | 8 | all match |
| ca-GrQc | 14,484 | 60 (stratified by core number) | all match |
| com-DBLP | 1,049,866 | 6 (high k, so brute force is tractable) | all match |

The toy graph in `data/toy.txt` is hand-built to exercise the awkward cases: two K4 blobs,
a bridge edge in no triangle, a pendant vertex, a separate triangle sharing one vertex,
and a triangle spanning two levels so at least one super-edge exists. Expected output was
worked out by hand before running anything.

## Result: is the index worth having?

`IndexFree.java` implements the no-index baseline exactly as the paper defines it: start
from each edge incident to q with weight >= k, then BFS over triangle-connected edges
until every community touching q is found. Same definition, same answers, but every query
pays for graph traversal and triangle enumeration instead of reading precomputed
structure. Edge weights are given to the baseline too, so this isolates the contribution
of the **index** with the decomposition held constant.

`MainBenchmark` runs both over identical queries in one JVM and asserts the answers match.
Query sets follow the paper's protocol: sort vertices by degree, ten equal-width
percentile buckets, random sample per bucket (`tools/make_queries.py`).

**ca-GrQc, k=5, 100 queries per bucket:**

| bucket | indexed (ms) | index-free (ms) | speedup | mean answer edges |
|---|---|---|---|---|
| p0_10 (highest degree) | 0.0207 | 3.9051 | **189x** | 1,742 |
| p10_20 | 0.0155 | 2.7421 | 177x | 1,129 |
| p20_30 | 0.0048 | 0.7948 | 164x | 369 |
| p30_40 | 0.0008 | 0.0743 | 97x | 36 |
| p40_100 | 0.0003 | 0.0003 | ~1x | 0 |
| **overall** | **0.0044** | **0.7518** | **173x** | |

**com-DBLP, k=5, 10 queries per bucket:**

| bucket | indexed (ms) | index-free (ms) | speedup | mean answer edges |
|---|---|---|---|---|
| p0_10 | 20.55 | 767.42 | 37x | 320,435 |
| p10_20 | 13.69 | 517.39 | 38x | 200,275 |
| p20_30 | 14.54 | 470.84 | 32x | 200,270 |
| p30_40 | 5.73 | 228.84 | 40x | 80,106 |
| p40_100 | ~0.0006 | ~0.003 | 3-15x | 0 |
| **overall** | **5.45** | **198.45** | **36x** | |

Answers were **identical on all 1,100 queries**, which is a second independent check on
the indexed path (the Python oracle is the first).

**Run-to-run variance is significant and must be reported.** Repeating the GrQc run gave
0.0063 ms indexed instead of 0.0044 ms, so overall speedup moved from 173x to 115x. The
indexed times are single-digit microseconds, where GC and scheduling noise dominate. Any
number you publish needs several repetitions with a median or a confidence interval, not
one run. The index-free side is stable because it is ~1000x slower.

Two further things to note.

**Speedup shrinks as the answer approaches graph size.** 173x on GrQc, 36x on DBLP. At
k=5 a DBLP community is ~320K edges, roughly a third of the graph, and no index can beat
the cost of materialising a third of the graph. The index removes traversal and triangle
enumeration, not output construction.

**Low-degree buckets return nothing at k=5,** so both methods are trivially fast there and
the speedup collapses to ~1x. This matches the paper's observation that runtime drops
sharply for low-degree query vertices. It also means a single overall average is
misleading, which is why the bucketing matters.

**Caveat on the indexed numbers.** 20.5 ms for a 320K-edge answer is about 64 ns per edge,
too slow for what is nominally a bulk copy. The cost is `LinkedList.addAll` allocating a
node per edge inside `findkCommunityForQuery`. So the indexed query time is partly an
artifact of her output data structure, and the real speedup is higher than reported. Left
alone on purpose, since changing it would mean modifying her index.

## Result: sweeping k

The paper's second query experiment. Two fixed query sets, H (high) and L (low), reused
across every k, so k is the only variable. `MainSweep` measures both methods for every
(k, set), repeats each cell, and reports the **median of the per-repetition means**,
because single runs are not trustworthy at microsecond scale.

`tools/make_highlow.py` builds the sets, `tools/plot_sweep.py` draws the two-panel figure
(`results/*/sweep.png`), `tools/core_profile.py` tells you which k range is meaningful
before you start.

**ca-GrQc, 100 per set, 5 repetitions** (`results/grqc_sweep/`):

| k | indexed H (ms) | free H (ms) | speedup H | answer edges |
|---|---|---|---|---|
| 2 | 0.0557 | 4.152 | 75x | 2,212 |
| 5 | 0.0107 | 2.287 | 213x | 1,013 |
| 8 | 0.0020 | 0.697 | 347x | 296 |
| 20 | 0.0005 | 0.182 | **385x** | 68 |
| 43 | 0.0003 | 0.033 | 114x | 9 |

**Speedup is not monotonic in k. It rises, peaks, then falls.** Three regimes:

- **Low k**: answers are enormous, so both methods are dominated by building the output.
  The index cannot beat the cost of materialising 2,212 edges, so speedup bottoms out.
- **Mid k**: answers are moderate. The index is a pure lookup while the baseline still has
  to traverse and enumerate triangles. This is where the index wins biggest.
- **Very high k**: answers are tiny, both finish in microseconds, and fixed per-query
  overheads dominate the ratio.

Reporting a single speedup number hides all of this. The shape is the result.

**com-DBLP, 20 per set, 3 repetitions** (`results/dblp_sweep/`):

| k | indexed H (ms) | free H (ms) | speedup H | answer edges |
|---|---|---|---|---|
| 2 | 45.25 | 947.90 | 21x | 408,885 |
| 5 | 23.40 | 815.56 | 35x | 300,410 |
| 10 | 0.328 | 52.28 | 160x | 12,493 |
| 20+ | 0.0005 | 0.0011 | 2x | 0 |

### A methodology trap, and the fix

Above k = 20 that sweep goes flat, and the reason is not the index. `core_profile.py` shows
why:

| | core >= 5 | core >= 10 | core >= 20 | core >= 40 | core >= 113 |
|---|---|---|---|---|---|
| ca-GrQc | 17.5% | 6.1% | 3.6% | 0.88% | - |
| com-DBLP | 31.2% | 5.6% | **0.97%** | 0.18% | 0.036% |

Only 0.97% of DBLP vertices reach core 20, and 0.036% reach 113. A random sample of 20
vertices will essentially never land there, so every query returns empty and the curve
flattens for structural reasons. The paper flags the same trap in a footnote: high k gives
very few or no communities in small and medium graphs.

Note also that degree and core number are correlated but not the same. A hub attached to
many leaves has high degree and low core number. The paper stratifies by degree, which
suits a truss study; for k-core, `--stratify core` is the better choice. On DBLP it lifted
the H set's median core from 7 to 8 and its ceiling from 13 to 19. Still not enough,
because the deep core is genuinely tiny.

So to measure high k you have to sample the deep core deliberately, with `--min-core`.
Those sets are a **biased best case, not representative**, and must be labelled as such.

**com-DBLP restricted to core >= 40, 20 per set, 5 repetitions** (`results/dblp_deep/`):

| k | indexed H (ms) | free H (ms) | speedup H | answer edges |
|---|---|---|---|---|
| 40 | 0.0842 | 65.85 | 782x | 6,886 |
| 60 | 0.0589 | 73.87 | 1,254x | 6,098 |
| 80 | 0.0541 | 70.77 | 1,308x | 6,054 |
| 90 | 0.0524 | 70.16 | **1,340x** | 6,054 |
| 113 | 0.0443 | 52.44 | 1,184x | 4,508 |

This is the index's best regime: a few thousand result edges buried inside a dense region
of a million-edge graph. The baseline must traverse and enumerate triangles across that
whole region; the index reads a handful of super-nodes. Answers were identical at every k.

So the honest range to quote for k-core on the EquiTruss index is **21x to 1,340x,
depending on how large the answer is relative to the graph**, and the sweep is what
justifies saying that rather than picking one flattering number.

## Result: k-truss vs k-core, same code, same machine

`MainMetricCompare` builds both indexes from the same graph in one JVM. The graph is
loaded twice on purpose, because `constructIndex` consumes it. The truss side runs on her
original defaults, `dropLevel2 = true` included.

| | ca-GrQc truss | ca-GrQc core | DBLP truss | DBLP core |
|---|---|---|---|---|
| max level | 44 | 43 | 114 | 113 |
| decomposition (s) | 0.159 | **0.033** | 8.630 | **1.845** |
| index construction (s) | 0.057 | 0.026 | 2.177 | 1.745 |
| total offline (s) | 0.216 | **0.058** | 10.807 | **3.590** |
| super-nodes | 1,569 | 3,387 | 126,904 | 232,067 |
| super-edges | 889 | 1,075 | 105,409 | 150,130 |
| index size (MB) | **0.141** | 0.208 | **11.15** | 15.13 |
| compression (edges/node) | 9.23 | 4.52 | 8.27 | 4.52 |
| super-nodes, level >= 3 | 1,569 | 1,398 | 126,904 | 134,472 |
| compression, level >= 3 | 8.21 | 8.08 | 7.70 | 6.58 |

**k-core is 3x cheaper to prepare.** Decomposition is 4.9x faster on GrQc and 4.7x faster
on DBLP, which is peeling versus triangle counting exactly as expected.

**But the k-core index is bigger**, 1.5x on GrQc and 1.36x on DBLP, with roughly twice the
super-nodes. Raw compression looks much worse, 4.52 against 8.27.

**That comparison is confounded, and the level >= 3 rows are the honest version.**
Trussness never equals 1, and her code discards trussness 2 as trivial (edges in no
triangle). Core weights span 1..kmax with every level meaningful. On GrQc, 1,989 of core's
3,387 super-nodes sit at levels 1 and 2, which truss does not index at all. Restrict both
to level >= 3 and compression is 8.08 against 8.21, essentially identical.

So the extra size is not core weights fragmenting dense regions. It is core weights
indexing the sparse periphery that truss throws away. On DBLP a gap does survive the
control (6.58 vs 7.70), so there is some genuine extra fragmentation at scale, but it is
far smaller than the raw numbers suggest.

**Harness validation.** The paper reports the DBLP truss index at 9.93 MB built in 2.5 s.
We measure 11.15 MB in 2.18 s. Within ~12% on size, faster on time, consistent with newer
hardware and possibly a different DBLP snapshot. That agreement is evidence the harness is
measuring the right things.

## Measurements so far

macOS arm64, JDK 27, 3 warm-up rounds discarded before timing.

| | ca-GrQc | com-DBLP |
|---|---|---|
| vertices | 5,241 | 317,080 |
| edges | 14,484 | 1,049,866 |
| max core number | 43 | 113 |
| graph read (s) | 0.042 | 0.460 |
| core decomposition (s) | 0.029 | 1.520 |
| index construction (s) | 0.049 | 1.658 |
| super-nodes | 3,387 | 232,067 |
| super-edges | 1,075 | 150,130 |
| compression (edges/super-node) | 4.28 | 4.52 |
| index size (bytes, `computeSize()`) | 218,504 | 15,860,368 |
| peak RSS | - | 1.7 GB |

Query cost tracks answer size, which is the design goal: on DBLP, returning a
7,629-edge community took 0.067 ms and returning a 6,505-edge one took 0.085 ms, from a
1M-edge graph. Low-core vertices at k >= 2 correctly return zero communities.

Decomposition and index construction are reported separately and must stay that way,
since the whole point of Task 4 is that the split differs per metric. Here the two are
comparable (1.5 s vs 1.7 s); for k-truss the decomposition dominates, and for
k-edge-connected it will dominate overwhelmingly.

Peak memory is the real scaling limit, not time. 1.7 GB for 1M edges comes from boxed
`Integer` keys and nested `HashMap`s. Note also that `read_GraphEdgelistWt` (the weighted
reader, unused here) hardcodes a HashMap capacity sized for ~18M vertices regardless of
input, which would distort memory numbers if you switch to it.

## Implementation notes worth keeping

- **`MyEdge` has no `equals()`/`hashCode()`.** `Map<MyEdge,Integer>` is identity-keyed, so
  `weights.get(new MyEdge(u,v))` always returns null. Every weight must be stored against
  the canonical instance in `MyGraph.g`. `CoreDecomposition` only touches those instances.
- **`constructIndex` destroys the graph** via `removeEdge`. Reload before building another
  index, and keep reload time out of the timings.
- Queries still work after construction because `findkCommunityForQuery` reads only the
  index structures, never the graph.
- **Vertex IDs must be integers.** Remap first if a dataset uses strings.
- `getEdge(u,v)` will NPE on a vertex absent from the graph; no null check.

## Not done yet

- A specialised single-metric index to compare against, the role TCP-Index plays for
  truss. Decide with the advisor whether to implement one or cite published numbers. This
  is the last real gap: the generic index beats having no index, but has not been shown to
  match an index purpose-built for k-core.
- Query types 2 and 3 from the WhatsApp discussion: max k subject to a size floor, and
  multi-vertex search. The size floor wants an edge count cached per super-node.
- Larger graphs (com-Amazon, com-Youtube, com-LiveJournal) and the road-network negative
  control. Memory is the limit, not time: 1.7 GB peak for 1M edges.
- k-edge-connected, deferred.
