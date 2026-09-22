#!/usr/bin/env python3
"""Generate query sets using the protocol from Akbas & Zhao (PVLDB 2017) Section 5.

Vertices are sorted by degree in non-increasing order and split into ten equal-width
percentile buckets, then a fixed number are sampled at random from each bucket. Bucket
"p0_10" holds the top 10% highest-degree vertices, "p90_100" the lowest.

Community structure varies sharply with degree, so reporting a single average over all
vertices hides the effect. Bucketing is what makes the numbers interpretable.

Usage:
    python3 tools/make_queries.py data/ca-GrQc.el data/grqc_buckets.txt --k 5 --per-bucket 100
    python3 tools/make_queries.py data/com-dblp.ungraph.el data/dblp_buckets.txt --k 5 --per-bucket 20
"""

import argparse
import random

import networkx as nx


def load(path):
    g = nx.Graph()
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            a, b = line.replace(",", "\t").split()[:2]
            a, b = int(a), int(b)
            if a != b:
                g.add_edge(a, b)
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("graph")
    ap.add_argument("out")
    ap.add_argument("--k", type=int, default=5, help="threshold k, fixed across buckets")
    ap.add_argument("--per-bucket", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    g = load(args.graph)
    rng = random.Random(args.seed)

    # non-increasing degree, so bucket 0 is the highest-degree decile
    order = sorted(g.nodes(), key=lambda v: (-g.degree(v), v))
    n = len(order)
    core = nx.core_number(g)

    rows = []
    for i in range(10):
        lo = i * n // 10
        hi = (i + 1) * n // 10
        bucket = order[lo:hi]
        label = f"p{i * 10}_{(i + 1) * 10}"
        take = min(args.per_bucket, len(bucket))
        for v in rng.sample(bucket, take):
            rows.append((v, args.k, label))

    with open(args.out, "w") as fh:
        fh.write(f"# degree-percentile buckets, k={args.k}, {args.per_bucket} per bucket, seed={args.seed}\n")
        fh.write(f"# graph: {n} vertices, {g.number_of_edges()} edges, max core {max(core.values())}\n")
        fh.write("# vertex,k,bucket\n")
        for v, k, label in rows:
            fh.write(f"{v},{k},{label}\n")

    print(f"{len(rows)} queries -> {args.out}")
    print(f"graph: {n} vertices, {g.number_of_edges()} edges, max core {max(core.values())}")


if __name__ == "__main__":
    main()
