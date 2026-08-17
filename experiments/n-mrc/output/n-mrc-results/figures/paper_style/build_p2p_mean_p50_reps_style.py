#!/usr/bin/env python3
import csv
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
BATCH = ROOT.parents[1]
SCHEMES = ("ecmp", "ops", "reps", "mrc", "sglb", "n-mrc")
STYLE = {
    "ecmp": ("#1b9e77", "o"),
    "ops": ("#d95f02", "s"),
    "reps": ("#e6ab02", "X"),
    "mrc": ("#7570b3", "D"),
    "sglb": ("#2ca25f", "^"),
    "n-mrc": ("#377eb8", "P"),
}


def geomean(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def load(metric):
    with (BATCH / "results.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle)
                if row["kind"] == "point_to_point"]
    grouped = {}
    for row in rows:
        grouped.setdefault((row["scenario"], row["scheme"]), []).append(
            (int(row["seed"]), float(row[metric])))
    values = {}
    seeds = {}
    for key, samples in grouped.items():
        samples.sort()
        if [seed for seed, _ in samples] != [13, 29, 47]:
            continue
        values[key] = geomean([value for _, value in samples])
        seeds[key] = samples
    return values, seeds


def draw(metric, label, stem):
    values, seeds = load(metric)
    workloads = [(traffic, size) for traffic in ("permutation", "tornado")
                 for size in (4, 8, 16)]
    ylabels = ["%s, %d MiB" % (traffic.title(), size)
               for traffic, size in workloads]
    web_loads = (40, 60, 80, 100)
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 7.2),
                             gridspec_kw={"width_ratios": [1.12, 1]})
    source = []

    for row_index, (cohort, baseline) in enumerate(
            (("healthy", "ecmp"), ("asymmetric", "mrc"))):
        ax = axes[row_index, 0]
        allowed = SCHEMES if cohort == "healthy" else SCHEMES[1:]
        for scheme in allowed:
            xs = []
            for traffic, size in workloads:
                scenario = "%s_p2p_%s_%dmib" % (cohort, traffic, size)
                absolute = values[(scenario, scheme)]
                baseline_value = values[(scenario, baseline)]
                speedup = baseline_value / absolute
                xs.append(speedup)
                source.append({
                    "metric": metric, "panel": cohort + "_speedup",
                    "scenario": scenario, "scheme": scheme,
                    "baseline": baseline, "geomean_us": absolute,
                    "baseline_geomean_us": baseline_value, "speedup": speedup,
                    "seed_values_us": ";".join("%d:%.9f" % pair
                                                  for pair in seeds[(scenario, scheme)]),
                })
            color, marker = STYLE[scheme]
            ax.scatter(xs, range(len(workloads)), color=color, marker=marker,
                       s=45, edgecolors="none", label=scheme.upper(), zorder=3)
        ax.axvline(1, color="#777777", linewidth=1)
        ax.set_yticks(range(len(workloads)), ylabels)
        ax.invert_yaxis()
        ax.grid(axis="x", linestyle=":", alpha=.45)
        ax.set_title(("Healthy P2P: ECMP baseline" if cohort == "healthy"
                      else "Asymmetric P2P: MRC baseline"), fontsize=10)
        ax.set_xlabel("%s FCT speedup vs %s (higher is better)" %
                      (label, baseline.upper()))

        ax = axes[row_index, 1]
        for scheme in allowed:
            ys = []
            for offered_load in web_loads:
                scenario = "%s_p2p_websearch_%dpct" % (cohort, offered_load)
                absolute = values[(scenario, scheme)]
                ys.append(absolute)
                source.append({
                    "metric": metric, "panel": cohort + "_websearch",
                    "scenario": scenario, "scheme": scheme,
                    "baseline": "", "geomean_us": absolute,
                    "baseline_geomean_us": "", "speedup": "",
                    "seed_values_us": ";".join("%d:%.9f" % pair
                                                  for pair in seeds[(scenario, scheme)]),
                })
            color, marker = STYLE[scheme]
            ax.plot(web_loads, ys, color=color, marker=marker,
                    linewidth=1.4, markersize=5)
        ax.grid(linestyle=":", alpha=.45)
        ax.set_xticks(web_loads)
        ax.set_title("%s P2P: WebSearch" % cohort.title(), fontsize=10)
        ax.set_xlabel("WebSearch offered load (%)")
        ax.set_ylabel("%s FCT (us; lower is better)" % label)

    handles = [plt.Line2D([], [], linestyle="", marker=STYLE[s][1],
                          color=STYLE[s][0], label=s.upper(), markersize=7)
               for s in SCHEMES]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False,
               bbox_to_anchor=(.5, .005))
    fig.tight_layout(rect=(0, .08, 1, 1))
    for suffix in ("png", "pdf"):
        fig.savefig(ROOT / (stem + "." + suffix), dpi=220 if suffix == "png" else None,
                    bbox_inches="tight")
    plt.close(fig)

    with (ROOT / (stem + ".csv")).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=source[0].keys())
        writer.writeheader(); writer.writerows(source)


def main():
    draw("mean_fct_us", "mean", "p2p_mean_reps_style")
    draw("p50_fct_us", "p50", "p2p_p50_reps_style")


if __name__ == "__main__":
    main()
