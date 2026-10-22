#!/usr/bin/env python3
"""Create readable query-time and speedup figures from a sweep CSV.

Zero-answer cells are omitted because their timing ratios measure only fixed
call overhead, not community-search performance. TCP series are shown when
present unless --without-tcp is supplied.

Usage:
    plot_sweep.py INPUT.csv OUTPUT.png "Dataset" [--without-tcp]
                  [--set-kind degree|core] [--sample-note TEXT]
"""

import argparse
import csv
import math
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("png_path")
    parser.add_argument("title")
    parser.add_argument("--without-tcp", action="store_true")
    parser.add_argument("--set-kind", choices=["degree", "core"], default="degree")
    parser.add_argument("--sample-note", default="")
    return parser.parse_args()


def set_names(kind):
    if kind == "core":
        return {"H": "higher-core stratum", "L": "lower-core stratum"}
    return {"H": "high degree", "L": "low degree"}


def exact_mean_edges(row):
    if "total_answer_edges" in row and "queries" in row:
        return float(row["total_answer_edges"]) / int(row["queries"])
    return float(row["mean_answer_edges"])


def main():
    args = parse_args()
    include_tcp = not args.without_tcp
    labels = set_names(args.set_kind)

    with open(args.csv_path, newline="") as handle:
        rows = list(csv.DictReader(handle))

    series = defaultdict(dict)
    answer_edges = defaultdict(dict)
    repetitions = set()
    for row in rows:
        k = int(row["k"])
        method = "equitruss" if row["method"] == "indexed" else row["method"]
        series[(method, row["set"])][k] = float(row["median_ms"])
        repetitions.add(int(row["reps"]))
        if method == "equitruss":
            answer_edges[row["set"]][k] = exact_mean_edges(row)

    if len(repetitions) != 1:
        raise SystemExit(f"inconsistent repetition counts: {sorted(repetitions)}")
    reps = next(iter(repetitions))
    ks = sorted({int(row["k"]) for row in rows})
    has_empty = any(value == 0 for values in answer_edges.values() for value in values.values())

    fig, (time_axis, ratio_axis) = plt.subplots(1, 2, figsize=(15, 6.2), constrained_layout=True)
    mode = "TCP included" if include_tcp and any(key[0] == "tcp" for key in series) else "TCP omitted"
    note = f" — {args.sample_note}" if args.sample_note else ""
    fig.suptitle(f"{args.title} ({mode}){note}", fontsize=15, fontweight="bold")

    styles = {
        ("equitruss", "H"): ("o-", "tab:blue", f"EquiTruss — {labels['H']}"),
        ("equitruss", "L"): ("s-", "tab:cyan", f"EquiTruss — {labels['L']}"),
        ("tcp", "H"): ("^-", "tab:green", f"TCP-style — {labels['H']}"),
        ("tcp", "L"): ("v-", "tab:olive", f"TCP-style — {labels['L']}"),
        ("free", "H"): ("o--", "tab:red", f"Index-Free — {labels['H']}"),
        ("free", "L"): ("s--", "tab:orange", f"Index-Free — {labels['L']}"),
    }
    for key, (marker, colour, label) in styles.items():
        if key[0] == "tcp" and not include_tcp:
            continue
        if key not in series:
            continue
        query_set = key[1]
        values = [series[key].get(k, math.nan) if answer_edges[query_set].get(k, 0) > 0 else math.nan for k in ks]
        time_axis.plot(ks, values, marker, color=colour, label=label, markersize=5, linewidth=1.8)

    time_axis.set_yscale("log")
    time_axis.set_xticks(ks)
    time_axis.set_xlabel("k threshold")
    time_axis.set_ylabel(f"mean query time (ms; median over {reps} runs)")
    time_axis.set_title("Query time" + (" — zero-answer cells omitted" if has_empty else ""))
    time_axis.grid(True, which="both", alpha=0.25)
    time_axis.legend(fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=2, frameon=False)

    ratio_styles = {
        ("free", "H"): ("o-", "tab:blue", f"Index-Free / EquiTruss — {labels['H']}"),
        ("free", "L"): ("s-", "tab:cyan", f"Index-Free / EquiTruss — {labels['L']}"),
        ("tcp", "H"): ("o:", "tab:green", f"TCP-style / EquiTruss — {labels['H']}"),
        ("tcp", "L"): ("s:", "tab:olive", f"TCP-style / EquiTruss — {labels['L']}"),
    }
    for (numerator, query_set), (marker, colour, label) in ratio_styles.items():
        if numerator == "tcp" and not include_tcp:
            continue
        if (numerator, query_set) not in series or ("equitruss", query_set) not in series:
            continue
        ratios = []
        for k in ks:
            equi = series[("equitruss", query_set)].get(k, 0)
            other = series[(numerator, query_set)].get(k, 0)
            nonempty = answer_edges[query_set].get(k, 0) > 0
            ratios.append(other / equi if nonempty and equi > 0 else math.nan)
        ratio_axis.plot(ks, ratios, marker, color=colour, label=label, markersize=5, linewidth=1.8)

    ratio_axis.axhline(1, color="black", linewidth=0.8, alpha=0.5)
    ratio_axis.set_yscale("log")
    ratio_axis.set_xticks(ks)
    ratio_axis.set_xlabel("k threshold")
    ratio_axis.set_ylabel("time ratio (×; above 1 favors EquiTruss)")
    ratio_axis.set_title("Speedup and answer size")
    ratio_axis.grid(True, which="both", alpha=0.25)

    answer_axis = ratio_axis.twinx()
    answer_styles = {
        "H": ("^--", "0.35", f"Mean answer edges — {labels['H']}"),
        "L": ("d--", "0.60", f"Mean answer edges — {labels['L']}"),
    }
    for query_set, (marker, colour, label) in answer_styles.items():
        if query_set not in answer_edges:
            continue
        values = [answer_edges[query_set].get(k, math.nan) or math.nan for k in ks]
        answer_axis.plot(ks, values, marker, color=colour, label=label, markersize=4, linewidth=1.3, alpha=0.85)
    answer_axis.set_yscale("log")
    answer_axis.set_ylabel("mean answer edges")

    ratio_handles, ratio_labels = ratio_axis.get_legend_handles_labels()
    answer_handles, answer_labels = answer_axis.get_legend_handles_labels()
    ratio_axis.legend(ratio_handles + answer_handles, ratio_labels + answer_labels, fontsize=8,
                      loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=2, frameon=False)

    fig.savefig(args.png_path, dpi=170, bbox_inches="tight")
    print(f"wrote {args.png_path}")


if __name__ == "__main__":
    main()
