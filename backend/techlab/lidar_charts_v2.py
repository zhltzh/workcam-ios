"""B02-W016 - LiDAR analysis charts (matplotlib, Agg backend).

Four figures rendered from the v2 analysis:
1. signed error vs baseline distance, per mode (one subplot each)
2. absolute-error histogram, per mode
3. success rate per condition, grouped bars per mode
4. cell-level repeatability stddev vs cell MAE (per mode colors)

Every figure footer carries the data label: DESKTOP_SIMULATION for
synthetic batches (never mistakable for device results), otherwise
MAC_EXPORT_UNVERIFIED. matplotlib is optional: import failure returns
an empty list instead of crashing the analyzer.
"""

from __future__ import annotations

from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _CHARTS_OK = True
except Exception:  # pragma: no cover - optional dependency
    _CHARTS_OK = False

from techlab.lidar_stats_v2 import Record  # noqa: F401 (type only)

MODE_COLORS = {
    "raycast": "#1f77b4",
    "scene_depth": "#2ca02c",
    "smoothed_depth": "#d62728",
}
MODE_ORDER = ["raycast", "scene_depth", "smoothed_depth"]


def charts_available() -> bool:
    return _CHARTS_OK


def _footer(fig, label: str) -> None:
    fig.text(0.5, 0.012,
             f"B02-W016 | {label}"
             + (" - synthetic data, NOT device results"
                if label == "DESKTOP_SIMULATION" else " - data origin not verified"),
             ha="center", fontsize=8, color="#666666")


def _modes_of(records) -> list[str]:
    present = {m.mode for m in records}
    return [m for m in MODE_ORDER if m in present] or sorted(present)


def _save(fig, out_dir: Path, name: str) -> Path:
    path = out_dir / name
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def render_charts(records, analysis: dict, out_dir: Path,
                   prefix: str = "") -> list[Path]:
    """Render the four figures; returns the written PNG paths."""
    if not _CHARTS_OK:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    label = analysis.get("data_label", "UNKNOWN")
    modes = _modes_of(records)
    written: list[Path] = []

    ok = [m for m in records if m.failure == "" and m.distance_measured_m is not None]
    # --- fig 1: signed error vs distance, per mode ---
    fig, axes = plt.subplots(1, max(len(modes), 1), figsize=(4.6 * max(len(modes), 1), 3.6),
                             squeeze=False)
    for ax, mode in zip(axes[0], modes):
        pts = [m for m in ok if m.mode == mode]
        fails = sum(1 for m in records if m.mode == mode and (m.failure or m.distance_measured_m is None))
        if pts:
            xs = [m.distance_true_m for m in pts]
            ys = [m.distance_measured_m - m.distance_true_m for m in pts]
            ax.scatter(xs, ys, s=18, color=MODE_COLORS.get(mode, "#333333"), alpha=0.75)
        s = analysis["by_mode"].get(mode, {})
        mae = s.get("mean_abs_error_m")
        ax.axhline(0, color="#999999", lw=0.8)
        if mae is not None:
            ax.axhline(mae, color="#cc7700", lw=1, ls="--", label=f"MAE {mae:.3f} m")
            ax.axhline(-mae, color="#cc7700", lw=1, ls="--")
        if s.get("mean_signed_error_m") is not None:
            ax.set_title(f"{mode}\n({fails} failure rows not plotted)", fontsize=9)
        else:
            ax.set_title(f"{mode} (no valid samples)", fontsize=9)
        ax.set_xlabel("baseline distance (m)")
        ax.set_ylabel("signed error (m)")
        ax.legend(fontsize=7)
    _footer(fig, label)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    written.append(_save(fig, out_dir, f"{prefix}fig1_signed_error_by_distance.png"))

    # --- fig 2: abs error histogram, per mode ---
    fig, axes = plt.subplots(1, max(len(modes), 1), figsize=(4.6 * max(len(modes), 1), 3.6),
                             squeeze=False)
    for ax, mode in zip(axes[0], modes):
        errs = [abs(m.distance_measured_m - m.distance_true_m)
                for m in ok if m.mode == mode]
        if errs:
            ax.hist(errs, bins=12, color=MODE_COLORS.get(mode, "#333333"), alpha=0.8)
            s = analysis["by_mode"].get(mode, {})
            if s.get("median_abs_error_m") is not None:
                ax.axvline(s["median_abs_error_m"], color="black", ls=":", lw=1,
                           label=f"median {s['median_abs_error_m']:.3f} m")
                ax.legend(fontsize=7)
        else:
            ax.text(0.5, 0.5, "no valid samples", ha="center", va="center")
        ax.set_title(mode, fontsize=9)
        ax.set_xlabel("|error| (m)")
        ax.set_ylabel("count")
    _footer(fig, label)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    written.append(_save(fig, out_dir, f"{prefix}fig2_abs_error_hist.png"))

    # --- fig 3: success rate per condition, grouped bars ---
    fig, ax = plt.subplots(figsize=(8, 4))
    conditions = sorted({m.condition for m in records})
    width = 0.8 / max(len(modes), 1)
    for i, mode in enumerate(modes):
        rates = []
        for cond in conditions:
            grp = [m for m in records if m.mode == mode and m.condition == cond]
            rates.append((sum(1 for m in grp if not m.failure and m.distance_measured_m is not None)
                          / len(grp)) if grp else 0.0)
        xs = [c + (i - (len(modes) - 1) / 2) * width for c in range(len(conditions))]
        ax.bar(xs, rates, width=width, label=mode,
               color=MODE_COLORS.get(mode, "#333333"), alpha=0.85)
    ax.set_xticks(range(len(conditions)))
    ax.set_xticklabels(conditions, fontsize=8)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("success rate")
    ax.set_title("Measurement success rate by condition (per mode)", fontsize=10)
    ax.legend(fontsize=8)
    _footer(fig, label)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    written.append(_save(fig, out_dir, f"{prefix}fig3_success_rate_by_condition.png"))

    # --- fig 4: cell repeatability vs cell MAE ---
    fig, ax = plt.subplots(figsize=(6, 4))
    for mode in modes:
        xs, ys = [], []
        for s in analysis["cell_repeatability"]:
            if not s["cell"].startswith(mode + "/"):
                continue
            cell_stats = analysis["by_cell"].get(s["cell"], {})
            if cell_stats.get("mean_abs_error_m") is None or s["repeatability_stddev_m"] is None:
                continue
            xs.append(cell_stats["mean_abs_error_m"])
            ys.append(s["repeatability_stddev_m"])
        if xs:
            ax.scatter(xs, ys, s=28, color=MODE_COLORS.get(mode, "#333333"),
                       alpha=0.8, label=mode)
    ax.set_xlabel("cell MAE (m)")
    ax.set_ylabel("cell repeatability stddev (m)")
    ax.set_title("Repeatability vs MAE at cell level\n(same baseline distance + condition only)",
                 fontsize=10)
    ax.legend(fontsize=8)
    _footer(fig, label)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    written.append(_save(fig, out_dir, f"{prefix}fig4_repeatability_vs_mae.png"))

    return written
