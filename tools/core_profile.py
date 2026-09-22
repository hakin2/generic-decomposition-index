#!/usr/bin/env python3
"""Core-number profile of a graph: how many vertices survive to each level k.

Run this BEFORE choosing the k range for a sweep. Otherwise half the sweep lands in a
region where almost no vertex has a community, and the curve goes flat for reasons that
have nothing to do with the index.

The paper notes the same trap in a footnote: in small and medium graphs, high k yields
very few or no communities, which is why it picks a modest k per graph.

Usage:
    python3 tools/core_profile.py data/com-dblp.ungraph.el
"""

import sys
from collections import Counter

import networkx as nx


def main():
    path = sys.argv[1]
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

    core = nx.core_number(g)
    n = g.number_of_nodes()
    kmax = max(core.values())
    counts = Counter(core.values())

    print(f"{path}: {n} vertices, {g.number_of_edges()} edges, max core {kmax}")
    print()
    print(f"{'k':>5} {'vertices core>=k':>18} {'share':>9}")
    surviving = n
    suggest_lo = suggest_hi = None
    for k in range(1, kmax + 1):
        if k > 1:
            surviving -= counts[k - 1]
        share = surviving / n
        # Only print decade-ish steps plus the tail, to keep output readable
        if k <= 10 or k % 10 == 0 or k == kmax:
            print(f"{k:>5} {surviving:>18} {share:>8.3%}")
        if suggest_lo is None and share < 0.50:
            suggest_lo = k
        if suggest_hi is None and share < 0.001:
            suggest_hi = k

    hi = suggest_hi if suggest_hi else kmax
    print()
    print(f"usable sweep range: k = 2 .. {hi}")
    print(f"  above k = {hi}, fewer than 0.1% of vertices have any community, so a random")
    print(f"  query sample will return empty and the curve flattens for structural reasons.")
    print(f"  to probe k > {hi}, sample the deep core explicitly:")
    print(f"    make_highlow.py ... --min-core {hi}")


if __name__ == "__main__":
    main()
