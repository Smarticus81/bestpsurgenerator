"""Chart generation for PSUR reports.

Generates:
1. Sales trend line chart
2. Complaint rate trend line chart with UCL/LCL bands

All charts saved as PNG for DOCX embedding.
"""
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from pathlib import Path
from typing import Any, Dict


# CooperSurgical brand-adjacent colors
COLORS = {
    "primary": "#003366",
    "secondary": "#0066CC",
    "accent": "#00A3E0",
    "green": "#28A745",
    "red": "#DC3545",
    "orange": "#FD7E14",
    "gray": "#6C757D",
    "light_gray": "#E9ECEF",
    "ucl_fill": "#FFCCCC",
    "mean_line": "#999999",
}

BAR_PALETTE = [
    "#003366", "#0066CC", "#00A3E0", "#28A745", "#FD7E14",
    "#DC3545", "#6F42C1", "#20C997", "#E83E8C", "#795548",
]


def generate_all_charts(
    statistics: Dict[str, Any],
    output_dir: Path,
    device_name: str = ""
) -> Dict[str, Path]:
    """
    Generate all PSUR charts.

    Args:
        statistics: PSURStatistics as dict (via asdict())
        output_dir: Directory to save chart PNGs
        device_name: Device name for chart titles

    Returns:
        Dict mapping chart name to file path
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    charts = {}

    # 1. Sales trend chart
    sales_path = output_dir / "chart_sales_trend.png"
    generate_sales_chart(statistics, sales_path, device_name)
    charts["sales_trend"] = sales_path

    # 2. Trend chart with UCL
    trend_path = output_dir / "chart_trend_ucl.png"
    generate_trend_chart(statistics, trend_path, device_name)
    charts["trend_ucl"] = trend_path

    return charts


def generate_sales_chart(
    statistics: Dict[str, Any],
    output_path: Path,
    device_name: str = ""
):
    """Generate sales trend line chart from units_by_month."""
    units_by_month = statistics.get("units_by_month", {})
    if not units_by_month:
        _placeholder_chart(output_path, "Insufficient sales data for trend chart")
        return

    months = sorted(units_by_month.keys())
    values = [units_by_month.get(m, 0) for m in months]

    if not months:
        _placeholder_chart(output_path, "Insufficient sales data for trend chart")
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    x = range(len(months))

    ax.plot(x, values, color=COLORS["secondary"], linewidth=2, marker="o",
            markersize=5, markerfacecolor=COLORS["accent"], label="Monthly Units")

    short_labels = []
    for label in months:
        if len(label) >= 7:
            short_labels.append(label[5:7] + "/" + label[2:4])
        else:
            short_labels.append(label)

    ax.set_xticks(list(x))
    ax.set_xticklabels(short_labels, rotation=45, ha="right", fontsize=9)
    ax.set_xlabel("Month", fontsize=10)
    ax.set_ylabel("Units Sold", fontsize=10)
    ax.set_title(f"Sales Trend — {device_name}" if device_name else "Sales Trend",
                 fontsize=13, fontweight="bold", color=COLORS["primary"])
    ax.grid(True, alpha=0.3)
    ax.set_ylim(bottom=0)

    plt.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)


def generate_trend_chart(
    statistics: Dict[str, Any],
    output_path: Path,
    device_name: str = ""
):
    """Generate complaint rate trend line chart with UCL/LCL bands."""
    trend = statistics.get("trend_analysis", {})
    monthly_rates = trend.get("monthly_rates", [])
    monthly_labels = trend.get("monthly_labels", [])
    mean = trend.get("mean", 0)
    ucl = trend.get("ucl_3sigma", 0)
    lcl = trend.get("lcl_3sigma", 0)
    status = trend.get("status", "")

    if not monthly_rates:
        _placeholder_chart(output_path, "Insufficient data for trend chart")
        return

    fig, ax = plt.subplots(figsize=(10, 5))

    x = range(len(monthly_rates))

    # UCL/LCL shaded band
    if ucl > 0:
        ax.fill_between(x, lcl, ucl, alpha=0.1, color=COLORS["red"], label="Control Limits (3-sigma)")

    # UCL and LCL lines — display as percentages
    ax.axhline(y=ucl, color=COLORS["red"], linestyle="--", linewidth=1, alpha=0.7, label=f"UCL = {ucl*100:.2f}%")
    ax.axhline(y=mean, color=COLORS["mean_line"], linestyle="-.", linewidth=1, alpha=0.7, label=f"Mean = {mean*100:.2f}%")
    if lcl > 0:
        ax.axhline(y=lcl, color=COLORS["green"], linestyle="--", linewidth=1, alpha=0.7, label=f"LCL = {lcl*100:.2f}%")

    # Rate line
    ax.plot(x, monthly_rates, color=COLORS["primary"], linewidth=2, marker="o",
            markersize=6, markerfacecolor=COLORS["accent"], label="Monthly Complaint Rate")

    # Highlight points above UCL
    for i, rate in enumerate(monthly_rates):
        if rate > ucl:
            ax.plot(i, rate, "o", color=COLORS["red"], markersize=10, zorder=5)

    # Labels
    short_labels = []
    for label in monthly_labels:
        if len(label) >= 7:
            short_labels.append(label[5:7] + "/" + label[2:4])
        else:
            short_labels.append(label)

    ax.set_xticks(list(x))
    ax.set_xticklabels(short_labels, rotation=45, ha="right", fontsize=9)
    ax.set_xlabel("Month", fontsize=10)
    ax.set_ylabel("Monthly Complaint Rate (%)", fontsize=10)
    ax.set_title(f"Complaint Rate Trend — {device_name}" if device_name else "Complaint Rate Trend",
                 fontsize=13, fontweight="bold", color=COLORS["primary"])

    # Format y-axis ticks as percentages
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda val, pos: f"{val*100:.1f}%"))

    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    ax.grid(True, alpha=0.3)
    ax.set_ylim(bottom=0)

    # Status annotation
    ax.annotate(f"Status: {status}", xy=(0.02, 0.95), xycoords="axes fraction",
                fontsize=10, fontweight="bold",
                color=COLORS["green"] if status == "STABLE" else COLORS["red"],
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor=COLORS["gray"]))

    plt.tight_layout()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)


def _placeholder_chart(output_path: Path, message: str):
    """Generate a placeholder chart with a message."""
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.text(0.5, 0.5, message, ha="center", va="center",
            fontsize=14, color=COLORS["gray"])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
