"""Command-line interface.

Examples
    python main.py                                   # interactive chat
    python main.py -q "veg dinner for 4 in Wakad under 2500"
    python main.py -q "..." --trace                  # show every agent step
    python main.py -q "..." --json                   # machine-readable output
    python main.py --demo                            # force demo mode (no LLM)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from agent import ConversationState, DiscoveryAgent
from agent.models import AgentResponse
from agent.response_generator import format_recommendations
from config import LOG_DIR, get_settings


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        filename=LOG_DIR / "agent.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )


def render(resp: AgentResponse, show_trace: bool) -> str:
    out = []
    if show_trace:
        out.append("--- agent trace ---")
        out.extend(f"[{t.stage}] {t.detail}" for t in resp.trace)
        out.append("-------------------")
    out.append(f"Agent ({resp.status}): {resp.message}")
    if resp.recommendations:
        out.append(format_recommendations(resp.recommendations))
    for a in resp.assumptions:
        out.append(f"Note: {a}")
    if resp.recommendations:
        out.append("(Sample data: restaurant names, prices and ratings are fictional.)")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Budget-aware restaurant discovery agent (Pune)")
    parser.add_argument("-q", "--query", help="run one request and exit")
    parser.add_argument("--trace", action="store_true", help="print the agent's step-by-step trace")
    parser.add_argument("--json", action="store_true", help="print the full response as JSON")
    parser.add_argument("--demo", action="store_true", help="force demo mode even if an API key is set")
    args = parser.parse_args(argv)

    # Windows consoles may not default to UTF-8; make sure "₹" can always be printed.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    setup_logging()
    settings = get_settings(force_demo=args.demo)
    agent = DiscoveryAgent(settings)

    if args.query is not None:
        resp = agent.run(args.query)
        print(json.dumps(resp.to_dict(), indent=2, ensure_ascii=False) if args.json else render(resp, args.trace))
        return 0

    print(f"Pune Restaurant Discovery Agent - {settings.mode_label}")
    print("Describe what you want (e.g. 'veg birthday dinner for 4 in Wakad under 2500'). Type 'quit' to exit.\n")
    state = ConversationState()
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text.lower() in {"quit", "exit", "q"}:
            return 0
        resp = agent.run(text, state)
        print(render(resp, args.trace) + "\n")


if __name__ == "__main__":
    sys.exit(main())
