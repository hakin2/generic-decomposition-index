# k-Core on the EquiTruss Index: Implementation and Evaluation

**Task 2 of the generic indexing framework project.**
Question: does the existing k-truss index work for k-core without redesigning it?

**Answer: yes.** Correct answers on every test, 21x to 1,340x faster than searching
without an index, and 3x cheaper to build than the k-truss version. Four of the five
original source files were not modified at all.

Baseline code: [esraabil/Equitruss](https://github.com/esraabil/Equitruss), the
implementation of Akbas & Zhao, PVLDB 10(11), 2017.

---

## 1. How k-core was implemented

The index needs exactly one thing from a decomposition: **an integer level on every edge.**
For k-truss that level is trussness. For k-core we compute it from vertex core numbers.

```
c(v)    = core number of v          (largest k whose k-core contains v)
w(u,v)  = min( c(u), c(v) )         (the edge level)
E_k     = { e : w(e) >= k }         (exactly the edge set of the k-core)
```

The `min` is exact, not an approximation. The k-core is the subgraph induced on
`{v : c(v) >= k}`, and an induced subgraph contains an edge precisely when both endpoints
are in the vertex set. So `w(e) >= k` holds if and only if the edge is in the k-core.

### Pipeline

```
graph  ->  core decomposition  ->  w(u,v) = min(c(u),c(v))  ->  EquiTruss index  ->  queries
           (new, 132 lines)         (new)                        (UNCHANGED)          (UNCHANGED)
```

Core numbers come from Batagelj & Zaversnik bucket peeling, O(n + m): repeatedly delete
the lowest-degree vertex and record the level at which each one dies.

### What changed in the original code

| file | status |
|---|---|
| `MyGraph.java`, `SGN.java`, `TecIndexG.java`, `MainF.java` | **unmodified** (verified with `diff`) |
| `TecIndexSB.java` | 3 changes, all flag-gated, defaults preserve original truss behaviour |

The weight map was already a parameter of `constructIndex`, and `createKedgeList` already
buckets an arbitrary weight map, so no new plumbing was needed. The query function
`findkCommunityForQuery` is untouched: its only use of the level is the numeric test
`level >= k`. **Query processing is genuinely weight-agnostic.**

The three changes:

1. **`dropLevel2` flag.** The original discards level 2 unconditionally, which is correct
   for truss (trussness 2 means "in no triangle") but wrong for k-core, where level 2 is a
   real 2-core. Left in, it silently truncates every answer at k <= 2.
2. The gate itself, one line.
3. **`verboseQuery` flag.** Two `System.out.print` calls sit inside the timed query path.
   Console I/O dwarfs the traversal; gating them changed measured query time by 4x.

Full diff in `PATCHES.diff`.

### One design note that matters for the write-up

The triangle-connectivity rule was deliberately **kept**. So a community at threshold k is
a **triangle-connected component of the k-core**, not the textbook k-core community.

Consequences:

- Answers are tighter than plain k-core. Two dense blobs joined by a bridge edge form one
  k-core community but two communities here, because the bridge lies in no triangle.
- **Communities overlap on vertices**, which plain connectivity would not give. A vertex in
  two dense regions is returned in both.

This should be named as its own definition in the paper. Claiming it reproduces standard
k-core communities would be incorrect.

### A structural property specific to core weights

`constructIndex` merges two same-level edges sharing a triangle **without checking the
triangle's third edge**. Under core weights that shortcut is provably safe:

> If `w(x,y) >= k` and `w(x,z) >= k` then `c(x), c(y), c(z) >= k`,
> so `w(y,z) = min(c(y), c(z)) >= k`.

A triangle can never have two heavy edges and one light one. Trussness carries no such
guarantee, because it is assigned per edge rather than derived from endpoints by `min`.
The verifier asserts this property on every graph; it held on all of them, including the
full 1M-edge DBLP graph.

---

## 2. Correctness

Two independent checks, no shared code with the index.

**Brute-force oracle** (`tools/oracle.py`, Python + networkx). Recomputes core numbers,
rebuilds `E_k`, unions edges through triangles with a disjoint-set structure, diffs the
canonicalised communities.

| graph | edges | queries | result |
|---|---|---|---|
| toy (hand-built) | 19 | 8 | all match |
| ca-GrQc | 14,484 | 60, stratified by core number | all match |
| com-DBLP | 1,049,866 | 6, high k so brute force is tractable | all match |

**Cross-check against the no-index baseline.** Both methods answer the same question by
the same definition, so their outputs must be identical. Approximately **3,300 queries**
across all experiments below: **0 mismatches**.

The toy graph is hand-built to cover the awkward cases: two 4-cliques, a bridge edge in no
triangle, a pendant vertex, a separate triangle sharing one vertex, and a triangle spanning
two levels so at least one super-edge exists. Expected output was worked out by hand before
running anything.

---

## 3. Experimental setup

**Machine.** Apple M3 Pro, 18 GB RAM, macOS 26.6, JDK 27. Heap pinned with
`-Xms` = `-Xmx` so GC behaviour does not drift between runs.

**Datasets.** SNAP, comment lines stripped.

| graph | vertices | edges | max core | max trussness |
|---|---|---|---|---|
| ca-GrQc | 5,241 | 14,484 | 43 | 44 |
| com-DBLP | 317,080 | 1,049,866 | 113 | 114 |

**Baseline: Index-Free.** No index at all, defined exactly as in the paper's Section 5:
start from each edge incident to q with level >= k, then BFS over triangle-connected edges
until every community touching q is found. Edge weights are given to the baseline too, so
the comparison isolates the contribution of the **index** with the decomposition held
constant.

**Query protocol.** Follows the paper. Vertices sorted by degree into ten equal-width
percentile buckets; `p0_10` is the highest-degree decile. For the vary-k sweep, two fixed
sets are reused across every k: H drawn from the top three buckets, L from the rest.

**Timing discipline.** JIT warm-up rounds discarded before every measurement. Sweep cells
repeated and reported as the median of per-repetition means. Decomposition time and index
construction time reported separately throughout.

---

## 4. Experiment 1: index construction, k-truss vs k-core

Both indexes built from the same graph, by the same code, in the same JVM
(`MainMetricCompare`). The truss side runs on the original defaults.

| | GrQc truss | GrQc core | DBLP truss | DBLP core |
|---|---|---|---|---|
| decomposition (s) | 0.159 | **0.033** | 8.630 | **1.845** |
| index construction (s) | 0.057 | **0.026** | 2.177 | **1.745** |
| **total offline (s)** | 0.216 | **0.058** | 10.807 | **3.590** |
| super-nodes | **1,569** | 3,387 | **126,904** | 232,067 |
| super-edges | **889** | 1,075 | **105,409** | 150,130 |
| index size (MB) | **0.141** | 0.208 | **11.15** | 15.13 |
| compression (edges / super-node) | **9.23** | 4.52 | **8.27** | 4.52 |
| super-nodes, level >= 3 | 1,569 | **1,398** | **126,904** | 134,472 |
| compression, level >= 3 | 8.21 | 8.08 | **7.70** | 6.58 |

**k-core is about 3x cheaper to prepare.** Decomposition is 4.9x faster on GrQc and 4.7x
faster on DBLP. That is peeling versus triangle counting, as expected.

**The k-core index is larger**, 1.5x on GrQc and 1.36x on DBLP, with roughly twice the
super-nodes. Raw compression looks half as good, 4.52 against 8.27.

**That raw comparison is confounded.** Trussness never equals 1, and the original code
discards trussness 2 as trivial. Core weights span 1..kmax with every level meaningful. On
GrQc, 1,989 of core's 3,387 super-nodes sit at levels 1 and 2, which truss does not index
at all. Restricting both to level >= 3 gives compression 8.08 against 8.21, essentially
identical.

So the extra size is **not** core weights fragmenting dense regions. It is core weights
indexing the sparse periphery that truss discards. On DBLP a smaller gap does survive the
control (6.58 vs 7.70), so there is some genuine extra fragmentation at scale.

**Harness validation.** The paper reports the DBLP truss index at 9.93 MB built in 2.5 s.
We measure 11.15 MB in 2.18 s: within about 12% on size, faster on time, consistent with
newer hardware and possibly a different DBLP snapshot. Reasonable evidence the harness
measures the right quantities.

---

## 5. Experiment 2: indexed vs no index, by degree percentile

Fixed k = 5. Identical queries for both methods.

**ca-GrQc**, 100 queries per bucket, 1,000 total:

| bucket | indexed (ms) | index-free (ms) | speedup | mean answer edges |
|---|---|---|---|---|
| p0_10 (highest degree) | 0.0207 | 3.9051 | **189x** | 1,742 |
| p10_20 | 0.0155 | 2.7421 | 177x | 1,129 |
| p20_30 | 0.0048 | 0.7948 | 164x | 369 |
| p30_40 | 0.0008 | 0.0743 | 97x | 36 |
| p40_100 | 0.0003 | 0.0003 | ~1x | 0 |
| **overall** | **0.0044** | **0.7518** | **173x** | |

**com-DBLP**, 10 queries per bucket, 100 total:

| bucket | indexed (ms) | index-free (ms) | speedup | mean answer edges |
|---|---|---|---|---|
| p0_10 | 20.55 | 767.42 | 37x | 320,435 |
| p10_20 | 13.69 | 517.39 | 38x | 200,275 |
| p20_30 | 14.54 | 470.84 | 32x | 200,270 |
| p30_40 | 5.73 | 228.84 | 40x | 80,106 |
| p40_100 | ~0.0006 | ~0.003 | 3-15x | 0 |
| **overall** | **5.45** | **198.45** | **36x** | |

Answers were identical on all 1,100 queries.

Low-degree buckets return nothing at k = 5, so both methods are trivially fast there and
the ratio collapses to ~1x. This matches the paper's observation that runtime falls sharply
for low-degree query vertices, and it is why a single overall average is misleading.

---

## 6. Experiment 3: varying k

Two fixed query sets reused across every k, so k is the only variable
(`MainSweep`, figures in `results/*/sweep.png`).

**ca-GrQc**, high-degree set, 100 queries, 5 repetitions:

| k | indexed (ms) | index-free (ms) | speedup | answer edges |
|---|---|---|---|---|
| 2 | 0.0557 | 4.152 | 75x | 2,212 |
| 3 | 0.0458 | 3.833 | 84x | 2,121 |
| 5 | 0.0107 | 2.287 | 213x | 1,013 |
| 8 | 0.0020 | 0.697 | 347x | 296 |
| 12 | 0.0005 | 0.194 | 363x | 80 |
| 20 | 0.0005 | 0.182 | **385x** | 68 |
| 30 | 0.0005 | 0.163 | 320x | 55 |
| 43 | 0.0003 | 0.033 | 114x | 9 |

**Speedup is not monotonic in k. It rises, peaks, then falls.** Three regimes:

- **Low k.** Answers are enormous, so both methods are dominated by constructing the
  output. No index can beat the cost of materialising a large fraction of the graph.
- **Mid k.** Answers are moderate. The index is a pure lookup while the baseline still has
  to traverse and enumerate triangles. Largest advantage.
- **Very high k.** Answers are tiny, both finish in microseconds, and fixed per-query
  overheads dominate the ratio.

**com-DBLP**, degree-sampled high set, 20 queries, 3 repetitions:

| k | indexed (ms) | index-free (ms) | speedup | answer edges |
|---|---|---|---|---|
| 2 | 45.25 | 947.90 | 21x | 408,885 |
| 5 | 23.40 | 815.56 | 35x | 300,410 |
| 10 | 0.328 | 52.28 | 160x | 12,493 |
| 20 and above | 0.0005 | 0.0011 | 2x | 0 |

### A sampling trap, and the correction

Above k = 20 that sweep flattens, and the cause is not the index. The core-number profile
(`tools/core_profile.py`) shows why:

| share of vertices with | core >= 5 | core >= 10 | core >= 20 | core >= 40 | core >= 113 |
|---|---|---|---|---|---|
| ca-GrQc | 17.5% | 6.1% | 3.6% | 0.88% | - |
| com-DBLP | 31.2% | 5.6% | **0.97%** | 0.18% | 0.036% |

Only 0.97% of DBLP vertices reach core 20 and 0.036% reach 113. A random sample of 20
vertices essentially never lands there, so every query returns empty. The paper flags the
same trap in a footnote: high k yields very few or no communities in small and medium
graphs.

Separately, **degree and core number are correlated but not the same.** A hub attached to
many leaves has high degree and a low core number. The paper stratifies by degree, which
suits a truss study; for k-core, stratifying by core number is more appropriate. On DBLP it
raised the H set's median core from 7 to 8 and its ceiling from 13 to 19 — still not enough,
because the deep core is genuinely tiny.

To measure high k the deep core must be sampled deliberately. Such sets are a **biased best
case, not representative**, and are labelled as such.

**com-DBLP restricted to core >= 40**, 20 queries, 5 repetitions:

| k | indexed (ms) | index-free (ms) | speedup | answer edges |
|---|---|---|---|---|
| 40 | 0.0842 | 65.85 | 782x | 6,886 |
| 50 | 0.0739 | 67.24 | 910x | 6,886 |
| 60 | 0.0589 | 73.87 | 1,254x | 6,098 |
| 70 | 0.0585 | 70.81 | 1,210x | 6,054 |
| 80 | 0.0541 | 70.77 | 1,308x | 6,054 |
| 90 | 0.0524 | 70.16 | **1,340x** | 6,054 |
| 100 | 0.0574 | 73.24 | 1,277x | 6,054 |
| 113 | 0.0443 | 52.44 | 1,184x | 4,508 |

This is the index's best regime: a few thousand result edges buried inside a dense region
of a million-edge graph. The baseline must traverse and enumerate triangles across the
whole region; the index reads a handful of super-nodes. Answers identical at every k.

---

## 7. Summary of findings

1. **The index is not truss-specific.** Swapping trussness for `min(c(u), c(v))` yields
   correct answers with no change to the index logic. Query processing needed no change at
   all.
2. **Speedup over no index ranges from 21x to 1,340x**, determined by answer size relative
   to graph size. Reporting a single figure is not defensible; the curve is the result.
3. **k-core is about 3x cheaper to prepare** than k-truss, driven by peeling versus
   triangle counting.
4. **The k-core index is 1.4x larger**, but almost entirely because it indexes the sparse
   periphery that truss discards. Controlling for that, compression is nearly identical.
5. **Core-derived weights are triangle-consistent**: a triangle cannot have two edges above
   a threshold and one below. This makes a shortcut inside `constructIndex` provably safe
   under core weights. Trussness has no equivalent guarantee.
6. **Two latent measurement bugs in the original code were found and gated**: the
   unconditional level-2 discard, and console output inside the timed query path.
7. **Query-set design matters more than expected.** Degree-stratified sampling cannot reach
   the high-k range on real graphs, because the deep core is a fraction of a percent of the
   vertices.

### Caveats stated plainly

- Two datasets only. More are needed, and memory is the limiting factor rather than time:
  peak 1.7 GB for a 1M-edge graph, from boxed `Integer` keys and nested hash maps.
- Indexed query time is partly an artifact of the original output structure.
  `LinkedList.addAll` allocates a node per edge, which is roughly 64 ns per edge on the
  largest answers. The true index advantage is therefore higher than reported. Left
  unchanged deliberately, since altering it would mean modifying the index.
- Run-to-run variance is significant at microsecond scale. One repeated run moved GrQc
  overall speedup from 173x to 115x. All sweep figures are medians over repetitions; the
  Experiment 2 table is single-run and should be repeated before publication.

---

## 8. Open question

**Should we compare against an index built specifically for k-core?**

Current status: the generic index clearly beats having no index. It has **not** been shown
to match a purpose-built single-metric index, the role TCP-Index plays for truss in the
original paper.

Two options:

- Implement a k-core-specific index (core hierarchy tree) and compare directly. Roughly a
  month.
- Cite published numbers from existing work. Roughly a day, weaker claim.

This decision gates the remaining schedule, so it is worth settling before more datasets
are added.

Also still open, from earlier discussion: the two extra query types (highest k subject to a
minimum community size, and multi-vertex search). The size-floor variant needs an edge count
cached per super-node, which is inexpensive to add.

---

## 9. Reproducing

The graph files are not committed (13 MB). Fetch them first:

```bash
cd data
curl -O https://snap.stanford.edu/data/ca-GrQc.txt.gz
curl -O https://snap.stanford.edu/data/bigdata/communities/com-dblp.ungraph.txt.gz
gunzip -f ca-GrQc.txt.gz com-dblp.ungraph.txt.gz
# strip SNAP comment lines; MyGraph.processLine cannot parse them
grep -v '^#' ca-GrQc.txt > ca-GrQc.el
grep -v '^#' com-dblp.ungraph.txt > com-dblp.ungraph.el
cd ..
```

The hand-built toy graph and all query sets **are** committed, so the reported numbers
reproduce exactly without regenerating any query file.

JDK is keg-only under Homebrew, so the full path is used.

```bash
JAVA=/opt/homebrew/opt/openjdk/bin
$JAVA/javac -d out src/*.java

# correctness
$JAVA/java -cp out MainCore data/toy.txt results/toy data/toy_queries.txt
python3 tools/oracle.py data/toy.txt data/toy_queries.txt results/toy/query_results.txt

# Experiment 1: truss vs core
$JAVA/java -Xms8g -Xmx8g -cp out MainMetricCompare data/com-dblp.ungraph.el results/dblp_cmp

# Experiment 2: indexed vs no index, by degree bucket
python3 tools/make_queries.py data/ca-GrQc.el data/grqc_buckets.txt --k 5 --per-bucket 100
$JAVA/java -Xms2g -Xmx2g -cp out MainBenchmark data/ca-GrQc.el results/grqc_bench data/grqc_buckets.txt

# Experiment 3: vary k
python3 tools/core_profile.py data/com-dblp.ungraph.el          # pick a valid k range first
python3 tools/make_highlow.py data/ca-GrQc.el data/grqc_highlow.txt --per-set 100
$JAVA/java -Xms2g -Xmx2g -cp out MainSweep data/ca-GrQc.el results/grqc_sweep \
    data/grqc_highlow.txt 2,3,5,8,12,20,30,43 5
# plotting needs matplotlib
python3 -m pip install matplotlib
python3 tools/plot_sweep.py results/grqc_sweep/sweep.csv \
    results/grqc_sweep/sweep.png "ca-GrQc"
```

Input format is TAB separated `u v`, matching `MyGraph.processLine` and SNAP files. Strip
SNAP comment lines with `grep -v '^#'`. The `oracle.py`, `make_*.py` and `core_profile.py`
scripts need only networkx and run under system `python3`.

Developer notes, implementation gotchas, and the exact patch listing are in `README.md`
and `PATCHES.diff`.

---

## 10. Attribution and licensing

`original/` and the five corresponding files in `src/` are the work of **Esra Akbas**,
taken from [esraabil/Equitruss](https://github.com/esraabil/Equitruss), the implementation
accompanying:

> E. Akbas and P. Zhao. *Truss-Based Community Search: a Truss-Equivalence Based Indexing
> Approach.* Proceedings of the VLDB Endowment, 10(11), 1298-1309, 2017.
> https://doi.org/10.14778/3137628.3137640

That repository carries **no license file**, so redistribution terms are unstated. This
repository is therefore **private** pending confirmation from the author. Do not make it
public, and do not redistribute `original/` or the unmodified files in `src/`, without her
agreement.

Files authored for this project, and freely usable within it:

- `src/CoreDecomposition.java`
- `src/IndexFree.java`
- `src/MainCore.java`
- `src/MainBenchmark.java`
- `src/MainMetricCompare.java`
- `src/MainSweep.java`
- everything in `tools/`
- `data/toy.txt` and all query sets
- `README.md`, `REPORT.md`, `PATCHES.diff`

`src/TecIndexSB.java` is her file with three small gated modifications; see `PATCHES.diff`
for the exact change set.
