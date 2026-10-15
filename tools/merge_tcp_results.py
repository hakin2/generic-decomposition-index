#!/usr/bin/env python3
"""Merge existing Index-Free results with separate EquiTruss/TCP runs.

The expensive Index-Free phase and the two in-memory indexes are measured in
separate JVM processes to reduce memory/GC interference. Query identity and
answer counts are checked before rows are merged.

Usage:
    merge_tcp_results.py fixed BASE_BENCH TCP_BENCH OUTPUT
    merge_tcp_results.py sweep BASE_SWEEP TCP_SWEEP OUTPUT
"""

import csv
import os
import sys


def read(path):
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def ensure_parent(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def merge_fixed(base_path, tcp_path, output_path):
    base = read(base_path)
    tcp = read(tcp_path)
    base_keys = [(row["bucket"], row["vertex"], row["k"]) for row in base]
    tcp_keys = [(row["bucket"], row["vertex"], row["k"]) for row in tcp]
    if base_keys != tcp_keys:
        raise SystemExit("fixed-query files contain different ordered query keys")
    if len(set(base_keys)) != len(base_keys):
        raise SystemExit("fixed-query files contain duplicate query keys")
    base_by_key = dict(zip(base_keys, base))
    tcp_by_key = dict(zip(tcp_keys, tcp))

    fields = [
        "bucket", "vertex", "k", "communities", "answer_edges",
        "equitruss_ms", "tcp_ms", "indexfree_ms",
        "free_vs_equi", "tcp_vs_equi", "free_vs_tcp",
    ]
    ensure_parent(output_path)
    with open(output_path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for key in base_by_key:
            old = base_by_key[key]
            new = tcp_by_key[key]
            if old["communities"] != new["communities"] or old["answer_edges"] != new["answer_edges"]:
                raise SystemExit(f"answer-count mismatch for query {key}")
            equi = float(new["equitruss_ms"])
            tcp_ms = float(new["tcp_ms"])
            free = float(old["indexfree_ms"])
            writer.writerow({
                "bucket": key[0],
                "vertex": key[1],
                "k": key[2],
                "communities": new["communities"],
                "answer_edges": new["answer_edges"],
                "equitruss_ms": f"{equi:.6f}",
                "tcp_ms": f"{tcp_ms:.6f}",
                "indexfree_ms": f"{free:.6f}",
                "free_vs_equi": f"{free / equi:.2f}" if equi else "0.00",
                "tcp_vs_equi": f"{tcp_ms / equi:.2f}" if equi else "0.00",
                "free_vs_tcp": f"{free / tcp_ms:.2f}" if tcp_ms else "0.00",
            })
    print(f"merged {len(base)} fixed queries -> {output_path}")


def merge_sweep(base_path, tcp_path, output_path):
    base = read(base_path)
    tcp = read(tcp_path)
    base_by_key = {(row["k"], row["set"], row["method"]): row for row in base}
    tcp_by_key = {(row["k"], row["set"], row["method"]): row for row in tcp}
    base_cells = {(row["k"], row["set"]) for row in base}
    tcp_cells = {(row["k"], row["set"]) for row in tcp}
    if base_cells != tcp_cells:
        raise SystemExit("sweep files contain different (k,set) cells")
    cells = sorted(tcp_cells, key=lambda x: (int(x[0]), x[1]))

    fields = [
        "k", "set", "method", "median_ms", "min_ms", "max_ms", "mean_answer_edges",
        "total_answer_edges", "queries", "reps", "query_fingerprint",
    ]
    ensure_parent(output_path)
    with open(output_path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for k, query_set in cells:
            equi = tcp_by_key[(k, query_set, "equitruss")]
            tcp_row = tcp_by_key[(k, query_set, "tcp")]
            base_equi = base_by_key.get((k, query_set, "indexed")) or base_by_key.get((k, query_set, "equitruss"))
            free = base_by_key.get((k, query_set, "free"))
            if base_equi is None or free is None:
                raise SystemExit(f"missing base sweep cell {(k, query_set)}")
            rows = (equi, tcp_row, base_equi, free)
            if len({row["query_fingerprint"] for row in rows}) != 1:
                raise SystemExit(f"query fingerprint mismatch for sweep cell {(k, query_set)}")
            if len({row["queries"] for row in rows}) != 1 or len({row["reps"] for row in rows}) != 1:
                raise SystemExit(f"query-count or repetition mismatch for sweep cell {(k, query_set)}")
            if len({row["total_answer_edges"] for row in rows}) != 1:
                raise SystemExit(f"exact answer-count mismatch for sweep cell {(k, query_set)}")
            for row in (equi, tcp_row, free):
                writer.writerow({field: row[field] for field in fields})
    print(f"merged {len(cells)} sweep cells -> {output_path}")


def main():
    if len(sys.argv) != 5 or sys.argv[1] not in {"fixed", "sweep"}:
        raise SystemExit(__doc__)
    mode, base_path, tcp_path, output_path = sys.argv[1:]
    if mode == "fixed":
        merge_fixed(base_path, tcp_path, output_path)
    else:
        merge_sweep(base_path, tcp_path, output_path)


if __name__ == "__main__":
    main()
