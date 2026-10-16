"""Draws docs/agent_workflow.png. Run: python scripts/make_diagram.py"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent_workflow.png"
LLM, PY, TOOL, END = "#dbe8fb", "#eef0f2", "#e3f3e6", "#fbe9d7"

fig, ax = plt.subplots(figsize=(11, 12.5), dpi=160)
ax.set_xlim(0, 110); ax.set_ylim(0, 125); ax.axis("off")


def box(x, y, w, h, text, color, bold=False):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0.4,rounding_size=1.5",
                                fc=color, ec="#56606b", lw=1.1))
    ax.text(x, y, text, ha="center", va="center", fontsize=9.2, weight="bold" if bold else "normal", wrap=True)


def arrow(x1, y1, x2, y2, label=None, lx=0, ly=0, style="-|>"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style, mutation_scale=12, lw=1.1, color="#3d4650",
                                 connectionstyle="arc3,rad=0"))
    if label:
        ax.text((x1 + x2) / 2 + lx, (y1 + y2) / 2 + ly, label, fontsize=8, color="#3d4650", ha="center", style="italic")


cx = 42
rows = {"req": 119, "val": 109, "ext": 98, "chk": 87, "dec": 76, "plan": 65, "tools": 54, "obs": 43, "rank": 32, "sum": 21, "valid": 11, "resp": 2}
box(cx, rows["req"], 40, 6, "User request (natural language)", END, True)
box(cx, rows["val"], 40, 6, "1. Validate input\n(empty / too long)", PY)
box(cx, rows["ext"], 46, 7, "2. Extract preferences -> JSON\nLLM extractor  |  rule-based fallback", LLM)
box(cx, rows["chk"], 40, 6, "3. Check constraint values\n(budget > 0, party 1-30, rating 0-5)", PY)
box(cx, rows["dec"], 40, 7, "4. Decide next action\nreject | unsupported | clarify | search", PY, True)
box(cx, rows["plan"], 40, 6, "5. Plan tool calls", PY)
box(cx, rows["tools"], 50, 8, "6. Execute tools\nsearch_restaurants -> estimate_cost\n-> filter_candidates (diet & budget are hard)", TOOL)
box(cx, rows["obs"], 40, 6, "7. Observe: enough matches?", PY, True)
box(cx, rows["rank"], 46, 7, "8. Rank (weighted score /100)\n+ explain reasons & trade-offs", PY)
box(cx, rows["sum"], 46, 6, "9. Write summary\ntemplate  |  optional LLM rewrite", LLM)
box(cx, rows["valid"], 46, 6, "10. Validate summary\n(no unknown names or unverified ₹ amounts)", PY)
box(cx, rows["resp"], 40, 5, "Response + visible trace", END, True)

order = list(rows)
for a, b in zip(order, order[1:]):
    arrow(cx, rows[a] - 3.6, cx, rows[b] + 3.8)

# side branches
box(92, rows["val"], 30, 6, "invalid_input\n(clear error message)", END)
arrow(cx + 20.5, rows["val"], 77, rows["val"])
arrow(cx + 20.5, rows["chk"], 77, rows["val"] - 3.2)
ax.text(72, rows["chk"] + 6, "invalid value", fontsize=8, color="#3d4650", style="italic", rotation=52)
box(92, rows["dec"] + 6, 30, 7, "needs_clarification\nask ONE question,\nkeep partial preferences", END)
arrow(cx + 20.5, rows["dec"] + 1, 77, rows["dec"] + 5, "missing area / budget", 1, 1.5)
ax.annotate("", xy=(cx + 23.5, rows["ext"] - 1), xytext=(92, rows["dec"] + 9.8),
            arrowprops=dict(arrowstyle="-|>", color="#8a5a00", lw=1.0, ls="--", connectionstyle="arc3,rad=0.35"))
ax.text(96, rows["ext"] - 1.5, "user's answer is\nmerged (max 3 rounds)", fontsize=8, color="#8a5a00", ha="center")
box(92, rows["dec"] - 6, 30, 5, "unsupported (off-topic)", END)
arrow(cx + 20.5, rows["dec"] - 2, 77, rows["dec"] - 6)
box(92, rows["tools"], 30, 6, "tool_error\n(retry once, then explain)", END)
arrow(cx + 25.5, rows["tools"], 77, rows["tools"])

box(92, rows["obs"] - 1.5, 32, 11, "Relax ONE soft constraint\n1. widen to nearby areas\n2. features  3. rating\n4. cuisine\n(prefs only if 0 matches)", PY)
arrow(cx + 20.5, rows["obs"], 75.8, rows["obs"] - 1, "too few", 2, 1.2)
ax.annotate("", xy=(cx + 25.5, rows["tools"] - 2.5), xytext=(92, rows["obs"] + 4.3),
            arrowprops=dict(arrowstyle="-|>", color="#3d4650", lw=1.1, connectionstyle="arc3,rad=0.25"))
ax.text(73, rows["tools"] - 5.8, "re-run tools", fontsize=8, style="italic", color="#3d4650")
box(92, rows["rank"] - 4, 30, 6, "no_results\nexplain blocker +\nclosest option", END)
arrow(cx + 20.5, rows["obs"] - 2.5, 77, rows["rank"] - 2)
ax.text(79, rows["rank"] + 3.2, "0 matches after\nrelaxing", fontsize=8, color="#3d4650", style="italic", ha="right")

ax.text(8, rows["sum"], "fails\ncheck ->\ntemplate", fontsize=8, ha="center", color="#3d4650")
ax.annotate("", xy=(cx - 23.5, rows["sum"]), xytext=(cx - 23.5, rows["valid"]),
            arrowprops=dict(arrowstyle="-|>", color="#3d4650", lw=1.0, connectionstyle="arc3,rad=-0.6"))

for i, (c, t) in enumerate([(LLM, "LLM-powered (falls back to rules)"), (PY, "Deterministic Python logic"),
                            (TOOL, "Tool calls (data access)"), (END, "Outcomes / user-facing")]):
    ax.add_patch(FancyBboxPatch((76, 16 - i * 3.6 - 1.2), 3, 2.4, boxstyle="round,pad=0.2", fc=c, ec="#56606b", lw=0.8))
    ax.text(80.5, 16 - i * 3.6, t, fontsize=8, va="center")

plt.savefig(OUT, bbox_inches="tight", facecolor="white")
print("wrote", OUT)
