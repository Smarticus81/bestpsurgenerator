"""Chart generation for PSUR reports.

Produces a set of regulator-grade visualisations:

1. ``sales_trend``      — monthly distribution volumes with a 3-month rolling
                          average and a cumulative-volume secondary axis.
2. ``trend_ucl``        — Shewhart p-chart of monthly complaint rates with
                          mean / UCL / LCL guides, out-of-control points
                          annotated, and an inline summary stats panel.
3. ``complaints_region`` — horizontal bar chart of complaints by region with
                           per-region rate annotations.
4. ``harm_distribution`` — donut chart of complaint counts by IMDRF harm
                           category.
5. ``top_mdps``         — horizontal bar chart of the top 10 IMDRF medical
                           device problem codes.
6. ``ract_matrix``      — 5×5 severity × occurrence risk matrix populated
                           from the parsed RACT (initial-state heat map).

All charts saved as PNG (150 DPI) for DOCX embedding.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")  # Non-interactive backend

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.patches import Patch

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Visual identity
# ---------------------------------------------------------------------------

COLORS = {
    "primary":   "#003366",  # navy
    "secondary": "#0066CC",  # mid-blue
    "accent":    "#00A3E0",  # bright blue
    "green":     "#28A745",
    "amber":     "#F0AD4E",
    "red":       "#DC3545",
    "orange":    "#FD7E14",
    "violet":    "#6F42C1",
    "teal":      "#20C997",
    "pink":      "#E83E8C",
    "gray":      "#6C757D",
    "light_gray":"#E9ECEF",
    "ucl_fill":  "#FFE5E5",
    "mean_line": "#999999",
    "bg":        "#FAFBFC",
}

CATEGORICAL_PALETTE = [
    COLORS["primary"], COLORS["secondary"], COLORS["accent"],
    COLORS["teal"],    COLORS["violet"],   COLORS["amber"],
    COLORS["green"],   COLORS["orange"],   COLORS["pink"],
    COLORS["gray"],
]

# Risk-matrix gradient: green (low) → amber → red (high)
RISK_COLORS = ["#1A8754", "#71B340", "#C9C432", "#E89A2C", "#D14A2C", "#A11D1D"]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_all_charts(
    statistics: Dict[str, Any],
    output_dir: Path,
    device_name: str = "",
    ract_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Path]:
    """Generate every available PSUR chart.

    Charts are best-effort: each generator gracefully produces a placeholder
    if its required source data is missing, so the caller always receives a
    full set of file paths.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    _apply_global_style()

    charts: Dict[str, Path] = {}

    chart_specs = [
        ("sales_trend",       "chart_sales_trend.png",       _generate_sales_chart),
        ("trend_ucl",         "chart_trend_ucl.png",         _generate_trend_chart),
        ("complaints_region", "chart_complaints_region.png", _generate_region_chart),
        ("harm_distribution", "chart_harm_distribution.png", _generate_harm_donut),
        ("top_mdps",          "chart_top_mdps.png",          _generate_mdp_chart),
        ("ract_matrix",       "chart_ract_matrix.png",       _generate_ract_matrix),
        ("rate_occurrence",   "chart_rate_occurrence.png",   _generate_rate_occurrence_chart),
        ("harm_trend",        "chart_harm_trend.png",        _generate_harm_trend_chart),
        ("per_period",        "chart_per_period.png",        _generate_per_period_chart),
    ]

    for key, filename, fn in chart_specs:
        path = output_dir / filename
        try:
            if key == "ract_matrix":
                fn(statistics, path, device_name, ract_data)
            else:
                fn(statistics, path, device_name)
            charts[key] = path
        except Exception as exc:  # pragma: no cover — never fail the pipeline
            logger.warning("Chart %s failed: %s", key, exc)
            _placeholder_chart(path, f"Chart unavailable: {key}")
            charts[key] = path

    return charts


# ---------------------------------------------------------------------------
# Global matplotlib styling
# ---------------------------------------------------------------------------


def _apply_global_style() -> None:
    """Apply a consistent, presentation-quality matplotlib rcParams set."""
    plt.rcParams.update({
        "figure.facecolor":   "white",
        "axes.facecolor":     COLORS["bg"],
        "axes.edgecolor":     COLORS["gray"],
        "axes.labelcolor":    COLORS["primary"],
        "axes.titlecolor":    COLORS["primary"],
        "axes.titleweight":   "bold",
        "axes.titlesize":     13,
        "axes.labelsize":     10,
        "axes.spines.top":    False,
        "axes.spines.right":  False,
        "axes.grid":          True,
        "grid.color":         COLORS["light_gray"],
        "grid.linestyle":     "-",
        "grid.linewidth":     0.7,
        "xtick.color":        COLORS["gray"],
        "ytick.color":        COLORS["gray"],
        "xtick.labelsize":    9,
        "ytick.labelsize":    9,
        "legend.frameon":     True,
        "legend.framealpha":  0.92,
        "legend.facecolor":   "white",
        "legend.edgecolor":   COLORS["light_gray"],
        "legend.fontsize":    9,
        "font.family":        "DejaVu Sans",
    })


# ---------------------------------------------------------------------------
# 1. Sales trend
# ---------------------------------------------------------------------------


def _generate_sales_chart(
    statistics: Dict[str, Any],
    output_path: Path,
    device_name: str = "",
) -> None:
    units_by_month = statistics.get("units_by_month", {}) or {}
    months = sorted(units_by_month.keys())
    if not months:
        _placeholder_chart(output_path, "Insufficient sales data for trend chart")
        return

    values = [int(units_by_month.get(m, 0)) for m in months]
    short_labels = [_short_month_label(m) for m in months]

    fig, ax = plt.subplots(figsize=(11, 5.2))
    x = list(range(len(months)))

    # Bars for monthly totals
    bars = ax.bar(
        x, values,
        color=COLORS["secondary"], alpha=0.85,
        edgecolor="white", linewidth=0.6,
        label="Monthly units",
    )

    # Rolling 3-month mean
    rolling = _rolling_mean(values, window=3)
    ax.plot(
        x, rolling,
        color=COLORS["primary"], linewidth=2.2, marker="o",
        markersize=5, markerfacecolor="white",
        markeredgewidth=1.8, label="3-month rolling avg",
    )

    # Cumulative line on a secondary axis
    cumulative = []
    running = 0
    for v in values:
        running += v
        cumulative.append(running)
    ax2 = ax.twinx()
    ax2.plot(
        x, cumulative,
        color=COLORS["orange"], linewidth=1.6, linestyle="--",
        marker=None, label="Cumulative units",
    )
    ax2.set_ylabel("Cumulative units", fontsize=10, color=COLORS["orange"])
    ax2.tick_params(axis="y", colors=COLORS["orange"])
    ax2.yaxis.set_major_formatter(_thousands_formatter())
    ax2.spines["right"].set_visible(True)
    ax2.spines["top"].set_visible(False)
    ax2.grid(False)

    ax.set_xticks(x)
    ax.set_xticklabels(short_labels, rotation=45, ha="right")
    ax.set_xlabel("Month")
    ax.set_ylabel("Units distributed")
    ax.set_title(
        f"Sales / Distribution Trend — {device_name}" if device_name else "Sales / Distribution Trend"
    )
    # Add headroom so annotations don't collide with the title
    if values:
        ax.set_ylim(0, max(values) * 1.18)
    else:
        ax.set_ylim(bottom=0)
    ax.yaxis.set_major_formatter(_thousands_formatter())

    # Annotate peak month
    if values:
        peak_idx = max(range(len(values)), key=lambda i: values[i])
        ax.annotate(
            f"Peak: {values[peak_idx]:,}",
            xy=(peak_idx, values[peak_idx]),
            xytext=(0, 8), textcoords="offset points",
            ha="center", fontsize=8, color=COLORS["primary"], fontweight="bold",
        )

    # Stats banner
    total = sum(values)
    avg = total / len(values) if values else 0
    _stats_banner(ax, [
        f"Total units: {total:,}",
        f"Months: {len(values)}",
        f"Avg / month: {avg:,.0f}",
        f"Peak month: {short_labels[peak_idx]} ({values[peak_idx]:,})" if values else "",
    ])

    # Combined legend (both axes)
    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines + lines2, labels + labels2, loc="upper left")

    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 2. Trend / UCL chart
# ---------------------------------------------------------------------------


def _generate_trend_chart(
    statistics: Dict[str, Any],
    output_path: Path,
    device_name: str = "",
) -> None:
    trend = statistics.get("trend_analysis", {}) or {}
    monthly_rates = trend.get("monthly_rates", []) or []
    monthly_labels = trend.get("monthly_labels", []) or []
    mean = float(trend.get("mean", 0) or 0)
    ucl = float(trend.get("ucl_3sigma", 0) or 0)
    lcl = float(trend.get("lcl_3sigma", 0) or 0)
    status = str(trend.get("status", "")).upper()
    violations = trend.get("western_electric_violations", []) or []

    if not monthly_rates:
        _placeholder_chart(output_path, "Insufficient data for control chart")
        return

    fig, ax = plt.subplots(figsize=(11, 5.4))
    x = list(range(len(monthly_rates)))
    short_labels = [_short_month_label(m) for m in monthly_labels] or [f"M{i+1}" for i in x]

    # Control band fill
    if ucl > 0:
        ax.fill_between(
            x, lcl, ucl,
            color=COLORS["accent"], alpha=0.06, zorder=1,
            label="±3σ control band",
        )

    # Reference lines
    if ucl > 0:
        ax.axhline(ucl, color=COLORS["red"], linestyle="--", linewidth=1.2,
                   alpha=0.85, zorder=2, label=f"UCL = {ucl*100:.3f}%")
    if mean > 0:
        ax.axhline(mean, color=COLORS["mean_line"], linestyle="-.", linewidth=1.2,
                   alpha=0.85, zorder=2, label=f"Mean = {mean*100:.3f}%")
    if lcl > 0:
        ax.axhline(lcl, color=COLORS["green"], linestyle="--", linewidth=1.2,
                   alpha=0.85, zorder=2, label=f"LCL = {lcl*100:.3f}%")

    # In-control vs. out-of-control points
    in_x, in_y, out_x, out_y = [], [], [], []
    for i, rate in enumerate(monthly_rates):
        if ucl > 0 and rate > ucl:
            out_x.append(i)
            out_y.append(rate)
        else:
            in_x.append(i)
            in_y.append(rate)

    ax.plot(
        x, monthly_rates,
        color=COLORS["primary"], linewidth=1.8, zorder=3, alpha=0.85,
    )
    ax.scatter(
        in_x, in_y,
        s=55, color=COLORS["accent"], edgecolor=COLORS["primary"],
        linewidth=1.0, zorder=4, label="In-control point",
    )
    if out_x:
        ax.scatter(
            out_x, out_y,
            s=110, color=COLORS["red"], edgecolor="white",
            linewidth=1.4, zorder=5, label=f"Out-of-control ({len(out_x)})",
            marker="D",
        )
        # Annotate each OOC point with its month label & rate (below the point
        # to avoid colliding with the title for high-rate spikes)
        for xi, yi in zip(out_x, out_y):
            ax.annotate(
                f"{short_labels[xi]}\n{yi*100:.2f}%",
                xy=(xi, yi),
                xytext=(14, -6), textcoords="offset points",
                ha="left", va="top",
                fontsize=8, color=COLORS["red"], fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="white",
                          edgecolor=COLORS["red"], alpha=0.9),
            )

    ax.set_xticks(x)
    ax.set_xticklabels(short_labels, rotation=45, ha="right")
    ax.set_xlabel("Month")
    ax.set_ylabel("Monthly complaint rate")
    ax.set_title(
        f"Monthly Complaint Rate — Shewhart p-chart — {device_name}"
        if device_name else "Monthly Complaint Rate — Shewhart p-chart"
    )
    # Headroom for OOC annotations + status badge above the data
    _ymax_data = max(monthly_rates) if monthly_rates else 0
    _ymax_ref = max(_ymax_data, ucl) if ucl > 0 else _ymax_data
    ax.set_ylim(0, _ymax_ref * 1.22 if _ymax_ref > 0 else 1)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v*100:.2f}%"))

    # Status badge
    badge_color = COLORS["green"] if status == "STABLE" else COLORS["red"]
    ax.text(
        0.985, 0.97,
        f" Trend status: {status or 'N/A'} ",
        transform=ax.transAxes,
        ha="right", va="top",
        fontsize=10, fontweight="bold", color="white",
        bbox=dict(boxstyle="round,pad=0.4", facecolor=badge_color, edgecolor="none"),
    )

    # Inline stats panel
    stats_lines = [
        f"Data points: {len(monthly_rates)}",
        f"Mean: {mean*100:.3f}%",
        f"UCL: {ucl*100:.3f}%",
        f"Out-of-control: {len(out_x)}",
    ]
    if violations:
        # Show first two distinct violation messages
        seen = []
        for v in violations:
            if v not in seen:
                seen.append(v)
            if len(seen) == 2:
                break
        stats_lines.append("WE rules: " + "; ".join(seen))
    _stats_banner(ax, stats_lines, loc="lower left")

    ax.legend(loc="upper left", ncol=2)

    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 3. Complaints by region (with rate annotations)
# ---------------------------------------------------------------------------


def _generate_region_chart(
    statistics: Dict[str, Any],
    output_path: Path,
    device_name: str = "",
) -> None:
    complaints_by_region = statistics.get("complaints_by_region", {}) or {}
    units_by_region = statistics.get("units_by_region", {}) or {}

    if not complaints_by_region:
        _placeholder_chart(output_path, "No complaint data by region")
        return

    # Sort regions by complaint count desc
    items = sorted(complaints_by_region.items(), key=lambda kv: kv[1], reverse=True)
    regions = [str(k) for k, _ in items]
    counts = [int(v) for _, v in items]

    fig, ax = plt.subplots(figsize=(10, max(3.2, 0.55 * len(regions) + 1.2)))
    y = list(range(len(regions)))

    bars = ax.barh(
        y, counts,
        color=[CATEGORICAL_PALETTE[i % len(CATEGORICAL_PALETTE)] for i in range(len(regions))],
        edgecolor="white", linewidth=0.7,
    )
    ax.invert_yaxis()
    ax.set_yticks(y)
    ax.set_yticklabels(regions)
    ax.set_xlabel("Complaint count")
    ax.set_title(
        f"Complaints by Region — {device_name}" if device_name else "Complaints by Region"
    )

    max_count = max(counts) if counts else 1
    ax.set_xlim(0, max_count * 1.25)

    for bar, region, cnt in zip(bars, regions, counts):
        units = units_by_region.get(region, 0) or 0
        rate_pct = (cnt / units * 100) if units else None
        if rate_pct is not None:
            label = f"{cnt:,}  ({rate_pct:.3f}% of {units:,} units)"
        else:
            label = f"{cnt:,}"
        ax.text(
            bar.get_width() + max_count * 0.015,
            bar.get_y() + bar.get_height() / 2,
            label,
            va="center", fontsize=9, color=COLORS["primary"],
        )

    ax.xaxis.set_major_formatter(_thousands_formatter())

    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 4. Harm-distribution donut
# ---------------------------------------------------------------------------


def _generate_harm_donut(
    statistics: Dict[str, Any],
    output_path: Path,
    device_name: str = "",
) -> None:
    by_harm = statistics.get("complaints_by_harm", {}) or {}
    if not by_harm:
        _placeholder_chart(output_path, "No complaint data by harm category")
        return

    items = sorted(by_harm.items(), key=lambda kv: kv[1], reverse=True)
    # Group long tail beyond top 7 into "Other"
    if len(items) > 7:
        head = items[:7]
        tail = items[7:]
        other_count = sum(int(v) for _, v in tail)
        if other_count:
            head.append(("Other", other_count))
        items = head

    labels = [str(_truncate(k, 28)) for k, _ in items]
    counts = [int(v) for _, v in items]
    total = sum(counts) or 1

    colors = [CATEGORICAL_PALETTE[i % len(CATEGORICAL_PALETTE)] for i in range(len(items))]

    fig, ax = plt.subplots(figsize=(9, 5.5))
    wedges, _ = ax.pie(
        counts,
        startangle=90,
        colors=colors,
        wedgeprops=dict(width=0.4, edgecolor="white", linewidth=2),
    )
    ax.text(
        0, 0.06, f"{total:,}",
        ha="center", va="center", fontsize=22, fontweight="bold", color=COLORS["primary"],
    )
    ax.text(
        0, -0.12, "complaints", ha="center", va="center",
        fontsize=10, color=COLORS["gray"],
    )

    # External legend with counts and percentages
    legend_labels = [
        f"{label}  ·  {cnt:,}  ({cnt/total*100:.1f}%)"
        for label, cnt in zip(labels, counts)
    ]
    ax.legend(
        wedges, legend_labels,
        title="IMDRF harm category",
        loc="center left", bbox_to_anchor=(1.02, 0.5),
        frameon=True,
    )
    ax.set_title(
        f"Complaint Harm Distribution — {device_name}"
        if device_name else "Complaint Harm Distribution"
    )

    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 5. Top medical-device problems
# ---------------------------------------------------------------------------


def _generate_mdp_chart(
    statistics: Dict[str, Any],
    output_path: Path,
    device_name: str = "",
) -> None:
    by_imdrf = statistics.get("complaints_by_imdrf", {}) or {}
    if not by_imdrf:
        _placeholder_chart(output_path, "No IMDRF medical-device-problem data")
        return

    items = sorted(by_imdrf.items(), key=lambda kv: kv[1], reverse=True)[:10]
    labels = [str(_truncate(k, 50)) for k, _ in items]
    counts = [int(v) for _, v in items]
    total = sum(int(v) for v in by_imdrf.values()) or 1

    fig, ax = plt.subplots(figsize=(10.5, max(3.2, 0.5 * len(items) + 1.2)))
    y = list(range(len(items)))

    bars = ax.barh(
        y, counts,
        color=COLORS["secondary"], alpha=0.9,
        edgecolor="white", linewidth=0.7,
    )
    ax.invert_yaxis()
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlabel("Complaint count")
    ax.set_title(
        f"Top IMDRF Medical-Device Problems — {device_name}"
        if device_name else "Top IMDRF Medical-Device Problems"
    )

    max_count = max(counts) if counts else 1
    ax.set_xlim(0, max_count * 1.22)

    for bar, cnt in zip(bars, counts):
        share = cnt / total * 100
        ax.text(
            bar.get_width() + max_count * 0.015,
            bar.get_y() + bar.get_height() / 2,
            f"{cnt:,}  ({share:.1f}%)",
            va="center", fontsize=9, color=COLORS["primary"],
        )

    ax.xaxis.set_major_formatter(_thousands_formatter())

    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 6. RACT severity × occurrence matrix
# ---------------------------------------------------------------------------


def _generate_ract_matrix(
    statistics: Dict[str, Any],
    output_path: Path,
    device_name: str = "",
    ract_data: Optional[Dict[str, Any]] = None,
) -> None:
    if not ract_data or not isinstance(ract_data, dict):
        _placeholder_chart(output_path, "No RACT data — risk matrix unavailable")
        return

    rs = ract_data.get("risk_summary", {}) or {}
    initial_matrix = rs.get("initial_matrix", {}) or {}
    final_matrix = rs.get("final_matrix", {}) or {}
    use_final = bool(final_matrix)
    matrix_dict = final_matrix if use_final else initial_matrix
    if not matrix_dict:
        _placeholder_chart(output_path, "RACT contains no severity / occurrence scores")
        return

    # Build a 5x5 grid (severity rows 5..1 top→bottom, occurrence cols 1..5 L→R)
    grid = [[0] * 5 for _ in range(5)]
    for key, count in matrix_dict.items():
        try:
            sev_str, occ_str = key.split("|", 1)
            sev = int(sev_str)
            occ = int(occ_str)
        except (ValueError, AttributeError):
            continue
        if not (1 <= sev <= 5 and 1 <= occ <= 5):
            continue
        # row 0 == severity 5, row 4 == severity 1
        row = 5 - sev
        col = occ - 1
        grid[row][col] += int(count)

    fig, ax = plt.subplots(figsize=(8.8, 6.2))
    # Risk rank in each cell = sev * occ (1..25); we colour by that rank.
    rank_grid = [[(5 - r) * (c + 1) for c in range(5)] for r in range(5)]
    bins = [1, 4, 8, 12, 16, 20, 26]
    cmap = matplotlib.colors.ListedColormap(RISK_COLORS)
    norm = matplotlib.colors.BoundaryNorm(bins, cmap.N)

    ax.imshow(rank_grid, cmap=cmap, norm=norm, aspect="auto")

    for r in range(5):
        for c in range(5):
            count = grid[r][c]
            if count:
                ax.text(
                    c, r, str(count),
                    ha="center", va="center",
                    fontsize=14, fontweight="bold", color="white",
                )
            else:
                ax.text(
                    c, r, "·",
                    ha="center", va="center",
                    fontsize=12, color="white", alpha=0.55,
                )

    ax.set_xticks(range(5))
    ax.set_xticklabels(["1\n(Improbable)", "2\n(Remote)", "3\n(Occasional)",
                        "4\n(Probable)", "5\n(Frequent)"])
    ax.set_yticks(range(5))
    ax.set_yticklabels(["5 (Catastrophic)", "4 (Critical)", "3 (Serious)",
                        "2 (Minor)", "1 (Negligible)"])
    ax.set_xlabel("Occurrence")
    ax.set_ylabel("Severity")

    title_state = "Residual" if use_final else "Initial"
    ax.set_title(
        f"RACT Risk Matrix — {title_state} ({rs.get('total_hazards', 0)} hazards) — {device_name}"
        if device_name else
        f"RACT Risk Matrix — {title_state} ({rs.get('total_hazards', 0)} hazards)"
    )
    ax.grid(False)
    ax.tick_params(length=0)

    legend_handles = [
        Patch(facecolor=RISK_COLORS[0], label="Negligible (1–3)"),
        Patch(facecolor=RISK_COLORS[2], label="Acceptable (4–11)"),
        Patch(facecolor=RISK_COLORS[3], label="ALARP (12–15)"),
        Patch(facecolor=RISK_COLORS[5], label="Unacceptable (16–25)"),
    ]
    ax.legend(
        handles=legend_handles,
        loc="upper center", bbox_to_anchor=(0.5, -0.18),
        ncol=4, frameon=False,
    )

    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# MEDDEV 2.7/1 Rev.4 occurrence band palette (psur-trend-charts skill)
MEDDEV_OCCURRENCE_COLORS = {
    "O1": "#27AE60",  # Improbable    — green
    "O2": "#82E0AA",  # Remote        — light green
    "O3": "#F1C40F",  # Occasional    — yellow
    "O4": "#F39C12",  # Probable      — orange
    "O5": "#E74C3C",  # Frequent      — red
}

# Harm-category palette (psur-trend-charts skill)
HARM_PALETTE = {
    "Death":                       "#7B1818",  # dark red
    "Injury (serious)":            "#DC3545",  # red
    "Serious Injury":              "#DC3545",
    "Injury (non-serious)":        "#FD7E14",  # orange
    "Non-Serious Injury":          "#FD7E14",
    "No Health Consequence":       "#4682B4",  # steel blue
    "No Health Consequence or Impact": "#4682B4",
    "No Harm":                     "#4682B4",
    "Other":                       "#6C757D",  # gray
    "Unclassified":                "#6C757D",
    "Unknown":                     "#6C757D",
}


def _harm_color(label: str) -> str:
    """Return the consistent harm-category colour, falling back to gray."""
    if not label:
        return HARM_PALETTE["Other"]
    if label in HARM_PALETTE:
        return HARM_PALETTE[label]
    lo = label.lower()
    if "death" in lo:
        return HARM_PALETTE["Death"]
    if "serious" in lo and "non" not in lo:
        return HARM_PALETTE["Injury (serious)"]
    if "non" in lo and ("serious" in lo or "injury" in lo):
        return HARM_PALETTE["Injury (non-serious)"]
    if "no harm" in lo or "no health" in lo or "no consequence" in lo:
        return HARM_PALETTE["No Health Consequence"]
    return HARM_PALETTE["Other"]


# ---------------------------------------------------------------------------
# 7. Complaint rate with MEDDEV O1–O5 reference bands (psur-trend-charts)
# ---------------------------------------------------------------------------


def _generate_rate_occurrence_chart(
    statistics: Dict[str, Any],
    output_path: Path,
    device_name: str = "",
) -> None:
    """Monthly complaint-rate (%) line with MEDDEV 2.7/1 occurrence bands."""
    trend = statistics.get("trend_analysis") or {}
    monthly_pct = list(trend.get("monthly_rates_pct") or [])
    monthly_labels = list(trend.get("monthly_labels") or [])

    if not monthly_pct or not monthly_labels:
        _placeholder_chart(output_path, "Insufficient rate data for occurrence chart")
        return

    fig, ax = plt.subplots(figsize=(11, 5.6))
    x = list(range(len(monthly_labels)))

    # MEDDEV occurrence bands (background shading) — clipped to data range
    max_y = max(monthly_pct + [0.5])  # always show at least up to 0.5%
    ax.axhspan(0, 0.01, alpha=0.18, color=MEDDEV_OCCURRENCE_COLORS["O1"])
    ax.axhspan(0.01, 0.1, alpha=0.18, color=MEDDEV_OCCURRENCE_COLORS["O2"])
    ax.axhspan(0.1, 1.0, alpha=0.18, color=MEDDEV_OCCURRENCE_COLORS["O3"])
    ax.axhspan(1.0, 10.0, alpha=0.18, color=MEDDEV_OCCURRENCE_COLORS["O4"])
    ax.axhspan(10.0, max(100.0, max_y * 1.2), alpha=0.18, color=MEDDEV_OCCURRENCE_COLORS["O5"])

    ax.plot(
        x, monthly_pct,
        color=COLORS["primary"], linewidth=2.0, marker="o",
        markersize=4.5, markerfacecolor="white", markeredgewidth=1.6,
        label="Monthly complaint rate (%)",
    )

    # Period boundary markers (psur-trend-charts spec)
    period_labels = list(statistics.get("section_c_period_labels") or [])
    aggregates = list(statistics.get("per_period_aggregates") or [])
    for agg in aggregates:
        p_end = (agg.get("end") or "")[:7]
        if not p_end:
            continue
        for i, m in enumerate(monthly_labels):
            if m == p_end and 0 < i < len(monthly_labels) - 1:
                ax.axvline(x=i, color=COLORS["gray"], linestyle="--",
                           linewidth=0.8, alpha=0.65)
                break

    ax.set_xticks(x[::max(1, len(x) // 18)])
    ax.set_xticklabels(
        [_short_month_label(monthly_labels[i]) for i in x[::max(1, len(x) // 18)]],
        rotation=45, ha="right",
    )
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.3f}%"))
    upper = max(0.05, max_y * 1.25)
    ax.set_ylim(0, upper)

    ax.set_ylabel("Complaint rate (%)")
    title = "Monthly Complaint Rate vs MEDDEV 2.7/1 Occurrence Classification"
    if device_name:
        title = f"{title} — {device_name}"
    ax.set_title(title)

    legend_handles = [
        Patch(facecolor=MEDDEV_OCCURRENCE_COLORS["O1"], alpha=0.55, label="O1 Improbable (≤0.01%)"),
        Patch(facecolor=MEDDEV_OCCURRENCE_COLORS["O2"], alpha=0.55, label="O2 Remote (0.01–0.1%)"),
        Patch(facecolor=MEDDEV_OCCURRENCE_COLORS["O3"], alpha=0.55, label="O3 Occasional (0.1–1%)"),
        Patch(facecolor=MEDDEV_OCCURRENCE_COLORS["O4"], alpha=0.55, label="O4 Probable (1–10%)"),
        Patch(facecolor=MEDDEV_OCCURRENCE_COLORS["O5"], alpha=0.55, label="O5 Frequent (>10%)"),
    ]
    ax.legend(handles=legend_handles, loc="upper left",
              bbox_to_anchor=(1.01, 1.0), borderaxespad=0)

    if period_labels:
        _stats_banner(ax, [
            f"Periods: {' | '.join(period_labels)}",
            f"Months observed: {len(monthly_labels)}",
        ], loc="lower left")

    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 8. Stacked harm-category trend by month (psur-trend-charts)
# ---------------------------------------------------------------------------


def _generate_harm_trend_chart(
    statistics: Dict[str, Any],
    output_path: Path,
    device_name: str = "",
) -> None:
    """Stacked-bar chart of complaint counts by IMDRF harm category over months."""
    harm_by_month = statistics.get("harm_by_month") or {}
    if not harm_by_month:
        _placeholder_chart(output_path, "Insufficient harm-by-month data for trend chart")
        return

    months = sorted(harm_by_month.keys())
    if not months:
        _placeholder_chart(output_path, "No months in harm-by-month data")
        return

    # Stable harm ordering: dominant categories first, "Other"-ish last
    all_harms: Dict[str, int] = {}
    for m in months:
        for h, c in (harm_by_month.get(m) or {}).items():
            all_harms[h] = all_harms.get(h, 0) + int(c or 0)
    if not all_harms:
        _placeholder_chart(output_path, "No complaints classified by harm")
        return
    ordered_harms = sorted(all_harms.keys(), key=lambda h: -all_harms[h])

    fig, ax = plt.subplots(figsize=(11, 5.6))
    x = list(range(len(months)))
    bottom = [0] * len(months)
    for harm in ordered_harms:
        values = [int((harm_by_month.get(m) or {}).get(harm, 0)) for m in months]
        ax.bar(
            x, values, bottom=bottom,
            color=_harm_color(harm), edgecolor="white", linewidth=0.4,
            label=_truncate(harm, 32),
        )
        bottom = [b + v for b, v in zip(bottom, values)]

    # Period boundaries
    aggregates = list(statistics.get("per_period_aggregates") or [])
    for agg in aggregates:
        p_end = (agg.get("end") or "")[:7]
        if not p_end:
            continue
        for i, m in enumerate(months):
            if m == p_end and 0 < i < len(months) - 1:
                ax.axvline(x=i + 0.5, color=COLORS["gray"], linestyle="--",
                           linewidth=0.8, alpha=0.65)
                break

    step = max(1, len(months) // 18)
    ax.set_xticks(x[::step])
    ax.set_xticklabels([_short_month_label(months[i]) for i in x[::step]],
                       rotation=45, ha="right")
    ax.yaxis.set_major_formatter(_thousands_formatter())
    ax.set_ylabel("Complaints")
    title = "Monthly Complaint Distribution by Harm Category"
    if device_name:
        title = f"{title} — {device_name}"
    ax.set_title(title)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0),
              borderaxespad=0, title="Harm category")

    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 9. Per-period count + rate bar chart (psur-trend-charts)
# ---------------------------------------------------------------------------


def _generate_per_period_chart(
    statistics: Dict[str, Any],
    output_path: Path,
    device_name: str = "",
) -> None:
    """Per-12-month-period complaint counts (bars) with rate / occurrence labels."""
    aggregates = list(statistics.get("per_period_aggregates") or [])
    if not aggregates:
        _placeholder_chart(output_path, "Insufficient per-period data for trend chart")
        return

    labels = [a.get("label", "") for a in aggregates]
    counts = [int(a.get("complaints", 0) or 0) for a in aggregates]
    rates_pct = [float(a.get("rate_pct", 0.0) or 0.0) for a in aggregates]
    occ_codes = [a.get("occurrence_code", "") for a in aggregates]

    bar_colors = []
    for code in occ_codes:
        bar_colors.append(MEDDEV_OCCURRENCE_COLORS.get(code, COLORS["secondary"]))
    # Highlight the current period (last bar) with a darker edge so it reads
    # as "the period under review" without losing the O-code colour mapping.
    edge_colors = [COLORS["gray"]] * len(labels)
    if edge_colors:
        edge_colors[-1] = COLORS["primary"]

    fig, ax = plt.subplots(figsize=(9.5, 5.4))
    x = list(range(len(labels)))
    bars = ax.bar(
        x, counts, color=bar_colors, alpha=0.92,
        edgecolor=edge_colors, linewidth=1.6,
    )
    ax.set_xticks(x)
    ax.set_xticklabels([_truncate(l, 24) for l in labels], rotation=15, ha="right")
    ax.yaxis.set_major_formatter(_thousands_formatter())
    ax.set_ylabel("Complaints")
    title = "Complaint Count by 12-Month Period (with MEDDEV occurrence)"
    if device_name:
        title = f"{title} — {device_name}"
    ax.set_title(title)

    # Annotate each bar with rate + O-code
    upper = max(counts + [1]) * 1.18
    ax.set_ylim(0, upper)
    for i, b in enumerate(bars):
        h = b.get_height()
        annotation = f"{counts[i]:,}\n{rates_pct[i]:.4f}%"
        if occ_codes[i]:
            annotation += f" ({occ_codes[i]})"
        ax.text(
            b.get_x() + b.get_width() / 2, h + upper * 0.01,
            annotation, ha="center", va="bottom",
            fontsize=8.5, color=COLORS["primary"], fontweight="bold",
        )

    legend_handles = [
        Patch(facecolor=MEDDEV_OCCURRENCE_COLORS[k], alpha=0.92,
              label=f"{k} {desc}")
        for k, desc in [
            ("O1", "Improbable"), ("O2", "Remote"), ("O3", "Occasional"),
            ("O4", "Probable"),   ("O5", "Frequent"),
        ]
    ]
    ax.legend(handles=legend_handles, loc="upper left",
              bbox_to_anchor=(1.01, 1.0), borderaxespad=0,
              title="Occurrence (MEDDEV 2.7/1)")

    fig.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)


def _short_month_label(label: str) -> str:
    if not isinstance(label, str):
        return str(label)
    if len(label) >= 7 and label[4] == "-":
        return f"{label[5:7]}/{label[2:4]}"
    return label


def _rolling_mean(values: List[float], window: int = 3) -> List[float]:
    if window < 2 or not values:
        return list(values)
    out = []
    for i in range(len(values)):
        lo = max(0, i - window + 1)
        chunk = values[lo:i + 1]
        out.append(sum(chunk) / len(chunk) if chunk else 0)
    return out


def _thousands_formatter():
    return mticker.FuncFormatter(lambda v, _: f"{int(v):,}")


def _truncate(text: str, max_len: int) -> str:
    s = str(text).strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _stats_banner(ax, lines, loc: str = "upper right") -> None:
    """Draw a small inline stats panel on the axes."""
    text = "\n".join(line for line in lines if line)
    if not text:
        return
    if loc == "lower left":
        xy = (0.02, 0.04)
        ha, va = "left", "bottom"
    else:
        xy = (0.98, 0.95)
        ha, va = "right", "top"
    ax.text(
        xy[0], xy[1], text,
        transform=ax.transAxes,
        ha=ha, va=va,
        fontsize=8.5, color=COLORS["primary"],
        bbox=dict(
            boxstyle="round,pad=0.45",
            facecolor="white", edgecolor=COLORS["light_gray"],
        ),
    )


def _placeholder_chart(output_path: Path, message: str) -> None:
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.text(
        0.5, 0.5, message,
        ha="center", va="center",
        fontsize=13, color=COLORS["gray"],
    )
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
