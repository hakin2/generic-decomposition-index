#!/usr/bin/env python3
"""Build the high-degree / low-degree query sets for the vary-k sweep.

Follows the second query experiment in Akbas & Zhao (PVLDB 2017) Section 5: sort
vertices by degree into ten percentile buckets, then draw one set at random from the top
three buckets (H, the highest 30% of degrees) and another from the remaining seven (L).
The same two sets are reused for every value of k, so the only thing changing across the
sweep is k.

Output lines are "vertex,set" with set in {H, L}.

Usage:
    python3 tools/make_highlow.py data/ca-GrQc.el data/grqc_highlow.txt --per-set 100
    python3 tools/make_highlow.py data/com-dblp.ungraph.el data/dblp_highlow.txt --per-set 20
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
    ap.add_argument("--per-set", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--min-core",
        type=int,
        default=0,
        help="restrict sampling to vertices with core number >= this. Needed to probe high "
        "k on real graphs: on com-DBLP only 0.97%% of vertices reach core 20, so a random "
        "sample never lands there and the sweep goes flat. Sets sampled this way are a "
        "deliberately biased best case, not representative, and must be labelled as such.",
    )
    ap.add_argument(
        "--stratify",
        choices=["degree", "core"],
        default="degree",
        help="degree reproduces the paper's protocol. core stratifies by core number "
        "instead, which is the right choice for a k-core study: degree and core number "
        "are correlated but not the same, so a degree-sampled H set can top out at a low "
        "core number and leave the whole high-k range returning empty answers.",
    )
    args = ap.parse_args()

    g = load(args.graph)
    rng = random.Random(args.seed)
    core = nx.core_number(g)

    pool = [v for v in g.nodes() if core[v] >= args.min_core] if args.min_core else list(g.nodes())
    if not pool:
        raise SystemExit(f"no vertex has core number >= {args.min_core} (max is {max(core.values())})")

    if args.stratify == "core":
        order = sorted(pool, key=lambda v: (-core[v], -g.degree(v), v))
    else:
        order = sorted(pool, key=lambda v: (-g.degree(v), v))
    n = len(order)
    split = 3 * n // 10  # top 30% of the chosen ordering

    high = order[:split]
    low = order[split:]

    hs = rng.sample(high, min(args.per_set, len(high)))
    ls = rng.sample(low, min(args.per_set, len(low)))

    with open(args.out, "w") as fh:
        fh.write(f"# high/low query sets stratified by {args.stratify}, "
                 f"{args.per_set} per set, seed={args.seed}\n")
        fh.write(f"# graph: {n} vertices, {g.number_of_edges()} edges, max core {max(core.values())}\n")
        fh.write("# vertex,set\n")
        for v in hs:
            fh.write(f"{v},H\n")
        for v in ls:
            fh.write(f"{v},L\n")

    print(f"H={len(hs)} L={len(ls)} -> {args.out}  (stratified by {args.stratify})")
    print(f"graph: {n} vertices, {g.number_of_edges()} edges, max core {max(core.values())}")

    def rng_str(vs, f):
        d = [f(v) for v in vs]
        return f"{min(d)}..{max(d)} (median {sorted(d)[len(d) // 2]})"

    print(f"H degrees {rng_str(hs, g.degree)} | core {rng_str(hs, lambda v: core[v])}")
    print(f"L degrees {rng_str(ls, g.degree)} | core {rng_str(ls, lambda v: core[v])}")
    key = g.degree if args.stratify == "degree" else (lambda v: core[v])
    print(f"split boundary: top 30% have {args.stratify} >= {key(order[split - 1])}")


if __name__ == "__main__":
    main()
