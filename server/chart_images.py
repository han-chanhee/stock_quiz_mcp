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

    fig, ax = plt.subplots(figsize=(5.2, 2.5), dpi=160)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")
    ax.plot(x_values, points, color=color, linewidth=3.2, solid_capstyle="round")
    ax.fill_between(x_values, points, min(points) - 0.08, color=fill_color, alpha=0.95)
    ax.scatter(x_values[-1], points[-1], s=42, color=color, zorder=3)
    ax.set_xlim(x_values[0], x_values[-1])
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.tight_layout(pad=0.08)

    output = BytesIO()
    fig.savefig(output, format="png", transparent=False)
    plt.close(fig)
    return output.getvalue()
