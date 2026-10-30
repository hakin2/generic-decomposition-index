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
- `TcpIndex.java`: independent TCP-style maximum-spanning-forest baseline.
- `MainBenchmark.java`, `MainSweep.java`, `MainMetricCompare.java`: experiments without TCP.
- `MainTcpBenchmark.java`, `MainTcpSweep.java`, `MainIndexCompare.java`: TCP comparisons.
- `tools/merge_tcp_results.py`: combines separately timed TCP and Index-Free runs.
- `tools/run_large_experiments.py`: runs and resumes the complete static suite on another computer.
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

### Paper-style static experiments

Amazon comes from the [SNAP Amazon network](https://snap.stanford.edu/data/com-Amazon.html):

```bash
curl -L -o data/com-amazon.ungraph.txt.gz \
  https://snap.stanford.edu/data/bigdata/communities/com-amazon.ungraph.txt.gz
gzip -dc data/com-amazon.ungraph.txt.gz \
  | awk 'BEGIN { OFS="\t" } !/^#/ && NF >= 2 && $1 != $2 { print $1, $2 }' \
  > data/com-amazon.ungraph.el
```

Run the construction comparison, fixed-threshold degree experiment, and H/L threshold sweep:

```bash
$JAVA/java -Xms4g -Xmx4g -cp out MainMetricCompare \
  data/com-amazon.ungraph.el results/amazon_cmp

python3 tools/make_queries.py data/com-amazon.ungraph.el \
  data/amazon_buckets.txt --k 4 --per-bucket 100 --seed 42
$JAVA/java -Xms4g -Xmx4g -cp out MainBenchmark \
  data/com-amazon.ungraph.el results/amazon_bench data/amazon_buckets.txt

python3 tools/make_highlow.py data/com-amazon.ungraph.el \
  data/amazon_highlow.txt --per-set 100 --seed 42 --stratify degree
$JAVA/java -Xms4g -Xmx4g -cp out MainSweep \
  data/com-amazon.ungraph.el results/amazon_sweep \
  data/amazon_highlow.txt 2,3,4,5,6 5
```

The complete DBLP experiment uses `data/dblp_buckets_100.txt` and `data/dblp_highlow_100.txt`. Results and interpretation are in [REPORT.md](REPORT.md).

### TCP-style comparison

`TcpIndex.java` independently implements Algorithms 3 and 4 from Huang et al. It uses the same k-core weights as EquiTruss; no third-party TCP source was copied.

```bash
# TCP-style and EquiTruss construction in separate JVMs, three runs each
rm -f results/amazon_index_compare/index_compare.csv
for run in 1 2 3; do
  $JAVA/java -Xms4g -Xmx4g -cp out MainIndexCompare \
    data/com-amazon.ungraph.el results/amazon_index_compare tcp "$run"
  $JAVA/java -Xms4g -Xmx4g -cp out MainIndexCompare \
    data/com-amazon.ungraph.el results/amazon_index_compare equitruss "$run"
done
python3 tools/summarize_index_compare.py \
  results/summary/amazon_tcp_index.csv \
  results/amazon_index_compare/index_compare.csv

# Fixed-k TCP-style versus EquiTruss queries, five balanced repetitions
$JAVA/java -Xms4g -Xmx4g -cp out MainTcpBenchmark \
  data/com-amazon.ungraph.el results/amazon_tcp_bench \
  data/amazon_buckets.txt 1000 5

# Different-k TCP-style versus EquiTruss queries
$JAVA/java -Xms4g -Xmx4g -cp out MainTcpSweep \
  data/com-amazon.ungraph.el results/amazon_tcp_sweep \
  data/amazon_highlow.txt 2,3,4,5,6 5

# Merge only after ordered query keys, fingerprints, and exact answer totals agree
python3 tools/merge_tcp_results.py fixed \
  results/amazon_bench/bench.csv results/amazon_tcp_bench/bench.csv \
  results/amazon_combined/bench.csv
python3 tools/merge_tcp_results.py sweep \
  results/amazon_sweep/sweep.csv results/amazon_tcp_sweep/sweep.csv \
  results/amazon_combined/sweep.csv
```

Graph files use TAB-separated integer pairs: `u<TAB>v`. Remove SNAP comments first:

```bash
grep -v '^#' input.txt > output.el
```

Python tools require `networkx`; plotting also requires `matplotlib`.

## Run the full suite on a larger computer

`tools/run_large_experiments.py` is the portable, resumable runner for Linux and macOS using Python 3.11 or newer. It captures CPU, RAM, currently available memory, swap, OS, disk, Java, Python dependencies, Git revision, command duration, and peak process memory. It does not publish the hostname, hardware serial numbers, UUIDs, account paths, or Git remote URLs.

First clone the repository and inspect the plan:

```bash
git clone https://github.com/hakin2/generic-decomposition-index.git
cd generic-decomposition-index
python3 tools/run_large_experiments.py --datasets all --heap-gb 512
```

Run all six datasets only on a machine with enough memory and disk. Download the UK-2002 Matrix Market archive first, then run:

```bash
python3 tools/run_large_experiments.py \
  --execute \
  --datasets all \
  --heap-gb 512 \
  --run-id large-server-1 \
  --archive uk2002=/path/to/uk-2002.tar.gz \
  --install-dependencies
```

Conservative guidance for the current object-heavy Java implementation:

- LiveJournal: 128+ GiB RAM
- Orkut: 384+ GiB RAM
- UK-2002: 768+ GiB RAM
- All datasets together: about 336 GiB free disk for downloads, normalized graphs, and sort workspace

If the run stops, repeat the same command with `--resume`:

```bash
python3 tools/run_large_experiments.py \
  --execute --datasets all --heap-gb 512 \
  --run-id large-server-1 \
  --archive uk2002=/path/to/uk-2002.tar.gz \
  --install-dependencies --resume
```

UK-2002 is distributed as a Matrix Market archive. Download it from the [SuiteSparse UK-2002 page](https://sparse.tamu.edu/LAW/uk-2002); the runner requires the local `--archive uk2002=...` argument because the upstream download currently redirects through an endpoint that is not reliably available over HTTPS.

The other large datasets come from [SNAP LiveJournal](https://snap.stanford.edu/data/com-LiveJournal.html) and [SNAP Orkut](https://snap.stanford.edu/data/com-Orkut.html).

The runner executes timed Java jobs sequentially and may take hours or days. It measures the Index-Free fixed workload in the same number of repetitions as the TCP/EquiTruss workload. Working data and raw logs are stored under `runs/<run-id>/` and ignored by Git. Validated method CSVs, exact query manifests, combined CSVs, figures, sanitized command records, source hashes, system information, and a SHA-256 manifest are written to:

```text
remote-results/<run-id>/
```

After completion, follow `remote-results/<run-id>/PUSH_INSTRUCTIONS.txt` to commit and push that bundle. Dynamic updates and the named DBLP case study are not included because they require missing algorithms or metadata rather than additional computer power.

## Attribution

The original EquiTruss files are by Esra Akbas:

> E. Akbas and P. Zhao. “Truss-Based Community Search: A Truss-Equivalence Based Indexing Approach.” PVLDB 10(11), 2017. https://doi.org/10.14778/3137628.3137640

The TCP-style baseline is an independent implementation based on:

> X. Huang et al. “Querying K-Truss Community in Large and Dynamic Graphs.” SIGMOD, 2014. https://doi.org/10.1145/2588555.2610495
