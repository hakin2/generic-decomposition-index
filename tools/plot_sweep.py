#!/usr/bin/env python3
"""Plot a sweep.csv produced by MainSweep.

Two panels, mirroring the paper's second query experiment:
  left  - query time against k, log scale, four series (indexed/free x H/L)
  right - speedup against k, plus mean answer size for context

Log scale on time is necessary: the two methods differ by two to three orders of
magnitude, so a linear axis would flatten the indexed series onto zero.

Usage:
    python3 tools/plot_sweep.py results/grqc_sweep/sweep.csv results/grqc_sweep/sweep.png "ca-GrQc"
"""

import csv
import sys
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")  # no display in this environment
import matplotlib.pyplot as plt


def main():
    csv_path, png_path = sys.argv[1], sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else csv_path

    rows = list(csv.DictReader(open(csv_path)))
    series = defaultdict(dict)   # (method, set) -> {k: median_ms}
    edges = defaultdict(dict)    # set -> {k: mean answer edges}
    for r in rows:
        k = int(r["k"])
        series[(r["method"], r["set"])][k] = float(r["median_ms"])
        if r["method"] == "indexed":
            edges[r["set"]][k] = int(r["mean_answer_edges"])

    ks = sorted({int(r["k"]) for r in rows})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    style = {
        ("indexed", "H"): ("o-", "tab:blue", "indexed, high degree"),
        ("indexed", "L"): ("s-", "tab:cyan", "indexed, low degree"),
        ("free", "H"): ("o--", "tab:red", "index-free, high degree"),
        ("free", "L"): ("s--", "tab:orange", "index-free, low degree"),
    }
    for key, (mk, colour, label) in style.items():
        if key not in series:
            continue
        ys = [series[key].get(k, float("nan")) for k in ks]
        ax1.plot(ks, ys, mk, color=colour, label=label, markersize=5)

    ax1.set_yscale("log")
    ax1.set_xlabel("k")
    ax1.set_ylabel("query time (ms, median of repetitions)")
    ax1.set_title(f"{title}: query time vs k")
    ax1.grid(True, which="both", alpha=0.3)
    ax1.legend(fontsize=8)

    for s, colour, mk in (("H", "tab:blue", "o-"), ("L", "tab:cyan", "s-")):
        if ("indexed", s) not in series:
            continue
        sp = []
        for k in ks:
            i = series[("indexed", s)].get(k, 0)
            f = series[("free", s)].get(k, 0)
            sp.append(f / i if i else float("nan"))
        ax2.plot(ks, sp, mk, color=colour, label=f"speedup, {'high' if s == 'H' else 'low'} degree",
                 markersize=5)

    ax2.set_xlabel("k")
    ax2.set_ylabel("speedup (index-free / indexed)")
    ax2.set_title(f"{title}: speedup vs k")
    ax2.grid(True, alpha=0.3)
    ax2.legend(fontsize=8, loc="upper left")

    ax3 = ax2.twinx()
    if "H" in edges:
        ax3.plot(ks, [edges["H"].get(k, 0) for k in ks], "^:", color="grey", markersize=4,
                 label="answer size, high degree")
        ax3.set_yscale("symlog")
        ax3.set_ylabel("mean answer edges (grey, symlog)", color="grey")
        ax3.legend(fontsize=8, loc="upper right")

    plt.tight_layout()
    plt.savefig(png_path, dpi=150)
    print(f"wrote {png_path}")


if __name__ == "__main__":
    main()
