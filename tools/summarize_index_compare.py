#!/usr/bin/env python3
"""Aggregate isolated MainIndexCompare runs by median.

Usage:
    summarize_index_compare.py OUTPUT INPUT.csv [INPUT.csv ...]
"""

import csv
import os
import statistics
import sys
from collections import defaultdict


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    output = sys.argv[1]
    groups = defaultdict(list)
    for path in sys.argv[2:]:
        with open(path, newline="") as handle:
            for row in csv.DictReader(handle):
                groups[(row["graph"], row["method"])].append(row)

    fields = [
        "graph", "method", "repetitions", "vertices", "edges", "max_core",
        "core_decomposition_s", "construction_s", "estimated_mb", "structure_nodes", "structure_edges",
    ]
    parent = os.path.dirname(output)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(output, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for (graph, method), rows in sorted(groups.items()):
            for field in ("vertices", "edges", "max_core", "estimated_mb", "structure_nodes", "structure_edges"):
                if len({row[field] for row in rows}) != 1:
                    raise SystemExit(f"inconsistent {field} for {(graph, method)}")
            writer.writerow({
                "graph": graph,
                "method": method,
                "repetitions": len(rows),
                "vertices": rows[0]["vertices"],
                "edges": rows[0]["edges"],
                "max_core": rows[0]["max_core"],
                "core_decomposition_s": f"{statistics.median(float(row['core_decomposition_s']) for row in rows):.6f}",
                "construction_s": f"{statistics.median(float(row['construction_s']) for row in rows):.6f}",
                "estimated_mb": rows[0]["estimated_mb"],
                "structure_nodes": rows[0]["structure_nodes"],
                "structure_edges": rows[0]["structure_edges"],
            })
    print(f"aggregated {sum(len(rows) for rows in groups.values())} runs -> {output}")


if __name__ == "__main__":
    main()
