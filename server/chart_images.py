"""Matplotlib chart rendering for chart-shape stock quizzes."""

from __future__ import annotations

from io import BytesIO

from contracts.schemas import StockSnapshot
from services.quiz_bank import chart_points_for_snapshot


def chart_png(snapshot: StockSnapshot) -> bytes:
    """Render an anonymous mini chart as PNG bytes.

    The chart intentionally omits the stock name and ticker so the image remains
    a clue, not an answer leak.
    """
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    points = chart_points_for_snapshot(snapshot)
    x_values = list(range(len(points)))
    color = "#16a34a" if snapshot.change_pct >= 0 else "#dc2626"
    fill_color = "#dcfce7" if snapshot.change_pct >= 0 else "#fee2e2"

    fig, ax = plt.subplots(figsize=(6.2, 2.9), dpi=160)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")
    ax.grid(axis="y", color="#e5e7eb", linewidth=0.8)
    for boundary in (6.5, 13.5, 20.5, 27.5):
        ax.axvline(boundary, color="#e2e8f0", linewidth=0.8)
    ax.plot(x_values, points, color=color, linewidth=2.6, solid_capstyle="round")
    ax.fill_between(x_values, points, min(points) - 0.08, color=fill_color, alpha=0.95)
    ax.scatter(x_values[-1], points[-1], s=38, color=color, zorder=3)
    ax.text(
        0.015,
        0.93,
        "1W hourly shape",
        transform=ax.transAxes,
        fontsize=8,
        color="#64748b",
        weight="bold",
    )
    ax.set_xticks([3, 10, 17, 24, 31])
    ax.set_xticklabels(["D-4", "D-3", "D-2", "D-1", "Today"], fontsize=7, color="#64748b")
    ax.tick_params(axis="x", length=0, pad=2)
    ax.tick_params(axis="y", left=False, labelleft=False)
    ax.set_xlim(x_values[0], x_values[-1])
    ax.set_ylim(max(0, min(points) - 0.12), min(1, max(points) + 0.12))
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout(pad=0.18)

    output = BytesIO()
    fig.savefig(output, format="png", transparent=False)
    plt.close(fig)
    return output.getvalue()
