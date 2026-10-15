#!/usr/bin/env python3
"""Plot a sweep CSV produced by MainSweep or MainTcpSweep.

The left panel shows query time against k. The right panel shows speedup and
answer size. TCP series are included when present; pass --without-tcp to create
the matching EquiTruss-versus-Index-Free figure.

Usage:
    python3 tools/plot_sweep.py input.csv output.png "Dataset"
    python3 tools/plot_sweep.py input.csv output.png "Dataset" --without-tcp
"""

import csv
import sys
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    csv_path, png_path = sys.argv[1], sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else csv_path
    include_tcp = "--without-tcp" not in sys.argv[3:]

    rows = list(csv.DictReader(open(csv_path)))
    series = defaultdict(dict)
    edges = defaultdict(dict)
    for row in rows:
        k = int(row["k"])
        method = "equitruss" if row["method"] == "indexed" else row["method"]
        series[(method, row["set"])][k] = float(row["median_ms"])
        if method == "equitruss":
            edges[row["set"]][k] = int(row["mean_answer_edges"])

    ks = sorted({int(row["k"]) for row in rows})
    fig, (time_axis, speedup_axis) = plt.subplots(1, 2, figsize=(13, 5))

    styles = {
        ("equitruss", "H"): ("o-", "tab:blue", "EquiTruss, high degree"),
        ("equitruss", "L"): ("s-", "tab:cyan", "EquiTruss, low degree"),
        ("tcp", "H"): ("^-", "tab:green", "TCP-style, high degree"),
        ("tcp", "L"): ("v-", "tab:olive", "TCP-style, low degree"),
        ("free", "H"): ("o--", "tab:red", "Index-Free, high degree"),
        ("free", "L"): ("s--", "tab:orange", "Index-Free, low degree"),
    }
    for key, (marker, colour, label) in styles.items():
        if key[0] == "tcp" and not include_tcp:
            continue
        if key not in series:
            continue
        time_axis.plot(ks, [series[key].get(k, float("nan")) for k in ks], marker,
                           color=colour, label=label, markersize=5)

    time_axis.set_yscale("log")
    time_axis.set_xlabel("k")
    time_axis.set_ylabel("query time (ms, median of repetitions)")
    time_axis.set_title(f"{title}: query time vs k")
    time_axis.grid(True, which="both", alpha=0.3)
    time_axis.legend(fontsize=8)

    for query_set, colour, marker in (("H", "tab:blue", "o-"), ("L", "tab:cyan", "s-")):
        if ("equitruss", query_set) not in series or ("free", query_set) not in series:
            continue
        free_speedup = []
        for k in ks:
            equi = series[("equitruss", query_set)].get(k, 0)
            free = series[("free", query_set)].get(k, 0)
            free_speedup.append(free / equi if equi else float("nan"))
        speedup_axis.plot(ks, free_speedup, marker, color=colour,
                          label=f"Index-Free / EquiTruss, {query_set}", markersize=5)

        if include_tcp and ("tcp", query_set) in series:
            tcp_speedup = []
            for k in ks:
                equi = series[("equitruss", query_set)].get(k, 0)
                tcp = series[("tcp", query_set)].get(k, 0)
                tcp_speedup.append(tcp / equi if equi else float("nan"))
            speedup_axis.plot(ks, tcp_speedup, marker.replace("-", ":"), color="tab:green",
                              label=f"TCP-style / EquiTruss, {query_set}", markersize=5)

    speedup_axis.set_xlabel("k")
    speedup_axis.set_ylabel("relative query time")
    speedup_axis.set_yscale("log")
    speedup_axis.set_title(f"{title}: speedup vs k")
    speedup_axis.grid(True, which="both", alpha=0.3)
    speedup_axis.legend(fontsize=8, loc="upper left")

    answer_axis = speedup_axis.twinx()
    if "H" in edges:
        answer_axis.plot(ks, [edges["H"].get(k, 0) for k in ks], "^:", color="grey", markersize=4,
                         label="answer size, high degree")
        answer_axis.set_yscale("symlog")
        answer_axis.set_ylabel("mean answer edges (grey, symlog)", color="grey")
        answer_axis.legend(fontsize=8, loc="upper right")

    plt.tight_layout()
    plt.savefig(png_path, dpi=150)
    print(f"wrote {png_path}")


if __name__ == "__main__":
    main()
