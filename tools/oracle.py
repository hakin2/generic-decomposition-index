#!/usr/bin/env python3
"""Independent brute-force oracle for the k-core + EquiTruss pipeline.

Computes the expected answers straight from the definitions using networkx, with
no shared code with the Java implementation, then diffs against the Java output.

Community definition being checked (see note in MainCore.java): because the
triangle-connectivity rule inside constructIndex is left in place, a community at
threshold k is a *triangle-connected component of the k-core*, not the textbook
k-core community.

    w(u,v) = min(c(u), c(v))
    E_k    = {e : w(e) >= k}
    two edges of E_k are adjacent iff they lie in a common triangle of E_k
    answer(q, k) = every connected component of that adjacency relation
                   containing at least one edge incident to q

Usage:
    python3 tools/oracle.py data/toy.txt data/toy_queries.txt results/toy/query_results.txt
"""

import sys
from itertools import combinations

import networkx as nx


def read_graph(path):
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


def read_queries(path):
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.replace(",", " ").split()
            out.append((int(p[0]), int(p[1])))
    return out


def key(u, v):
    return (u, v) if u < v else (v, u)


class DSU:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def core_weights(g):
    core = nx.core_number(g)
    return core, {key(u, v): min(core[u], core[v]) for u, v in g.edges()}


def check_triangle_consistency(g, weights):
    """Core-derived weights are triangle-consistent: a triangle cannot have two
    edges of weight >= k and a third below k.

    Why: w(x,y) >= k and w(x,z) >= k force c(x), c(y), c(z) >= k, hence
    w(y,z) = min(c(y), c(z)) >= k.

    This matters because constructIndex merges two same-level edges sharing a
    triangle without inspecting the triangle's third edge. That shortcut is safe
    precisely because of this property. Trussness has no such guarantee, so the
    property is specific to min-of-endpoint weights.
    """
    violations = []
    for a, b, c in triangles(g):
        w = [weights[key(a, b)], weights[key(b, c)], weights[key(a, c)]]
        w.sort()
        # the two largest being >= k must force the smallest >= k, for every k
        if w[0] < min(w[1], w[2]):
            violations.append(((a, b, c), w))
    return violations


def triangles(g):
    seen = set()
    for v in g:
        nbrs = list(g[v])
        for a, b in combinations(nbrs, 2):
            if g.has_edge(a, b):
                t = tuple(sorted((v, a, b)))
                if t not in seen:
                    seen.add(t)
                    yield t


def communities_at(g, weights, k, q):
    ek = {e for e, w in weights.items() if w >= k}
    if not ek:
        return []

    gk = nx.Graph()
    gk.add_edges_from(ek)

    dsu = DSU()
    for e in ek:
        dsu.find(e)
    for a, b, c in triangles(gk):
        e1, e2, e3 = key(a, b), key(b, c), key(a, c)
        if e1 in ek and e2 in ek and e3 in ek:
            dsu.union(e1, e2)
            dsu.union(e2, e3)

    comps = {}
    for e in ek:
        comps.setdefault(dsu.find(e), []).append(e)

    out = []
    for edges in comps.values():
        if any(q in e for e in edges):
            out.append(" ".join(sorted(f"{u}-{v}" for u, v in edges)))
    return sorted(out)


def read_java_results(path):
    res = {}
    cur = None
    with open(path) as fh:
        for line in fh:
            if line.startswith("QUERY"):
                p = line.split()
                if len(p) > 3 and p[3] == "MISSING":
                    cur = None
                    continue
                cur = (int(p[1]), int(p[2]))
                res[cur] = []
            elif cur is not None and line.strip():
                res[cur].append(line.strip())
    return {k: sorted(v) for k, v in res.items()}


def main():
    graph_file, query_file, java_file = sys.argv[1], sys.argv[2], sys.argv[3]

    g = read_graph(graph_file)
    core, weights = core_weights(g)

    print(f"graph: {g.number_of_nodes()} vertices, {g.number_of_edges()} edges")
    print(f"max core number: {max(core.values())}")

    bad = check_triangle_consistency(g, weights)
    if bad:
        print(f"FAIL triangle consistency: {len(bad)} violating triangles, e.g. {bad[0]}")
    else:
        print("ok   triangle consistency: every triangle's weights are min-closed")

    java = read_java_results(java_file)
    queries = read_queries(query_file)

    failures = 0
    skipped = 0
    for q, k in queries:
        if q not in g:
            print(f"SKIP q={q} k={k}: vertex not in graph (not a pass)")
            skipped += 1
            continue
        expected = communities_at(g, weights, k, q)
        actual = java.get((q, k))
        if actual is None:
            if expected:
                print(f"FAIL q={q} k={k}: java returned nothing, expected {len(expected)}")
                failures += 1
            continue
        if expected == actual:
            sizes = [len(c.split()) for c in expected]
            print(f"ok   q={q:<3} k={k:<2} {len(expected)} communities, sizes {sizes}")
        else:
            failures += 1
            print(f"FAIL q={q} k={k}")
            print(f"     expected {len(expected)}: {expected}")
            print(f"     actual   {len(actual)}: {actual}")

    print()
    checked = len(queries) - skipped
    if failures or bad:
        print(f"{failures} query mismatch(es), {len(bad)} consistency violation(s), {skipped} skipped")
        sys.exit(1)
    if checked == 0:
        print(f"NOTHING VERIFIED: all {len(queries)} queries were skipped")
        sys.exit(1)
    print(f"all {checked} verified queries match the brute-force oracle ({skipped} skipped)")


if __name__ == "__main__":
    main()
