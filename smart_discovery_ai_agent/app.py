"""Streamlit chat UI for the demo:  streamlit run app.py

The UI is only a thin layer: every decision is made by DiscoveryAgent, the
same object the CLI and the tests use.
"""
from __future__ import annotations

import streamlit as st

from agent import ConversationState, DiscoveryAgent
from config import get_settings

st.set_page_config(page_title="Pune Restaurant Discovery Agent", page_icon="🍽️", layout="centered")

EXAMPLES = [
    "Veg birthday dinner for 4 in Wakad under 3000",
    "Italian date night in Akurdi under 1500",
    "Find me a good place to eat for my family",
    "Jain Japanese food in Koregaon Park for 6 under 1000",
]


@st.cache_resource
def load_agent(force_demo: bool) -> DiscoveryAgent:
    return DiscoveryAgent(get_settings(force_demo=force_demo))


with st.sidebar:
    st.header("Settings")
    force_demo = st.toggle("Force demo mode (no LLM)", value=False)
    agent = load_agent(force_demo)
    st.caption(agent.settings.mode_label)
    show_trace = st.toggle("Show agent trace", value=True)
    if st.button("New conversation"):
        st.session_state.pop("history", None)
        st.session_state.pop("state", None)
    st.divider()
    st.caption(
        "Sample data only: restaurant names, prices and ratings are fictional. "
        "Area names are real Pune localities."
    )

st.title("Pune Restaurant Discovery Agent")
st.write("Tell me your budget, area, group size and any preferences. I'll shortlist, rank and explain.")

history = st.session_state.setdefault("history", [])
state: ConversationState = st.session_state.setdefault("state", ConversationState())

cols = st.columns(2)
clicked = None
for i, example in enumerate(EXAMPLES):
    if cols[i % 2].button(example, key=f"ex{i}", use_container_width=True):
        clicked = example


def show(resp) -> None:
    st.markdown(resp.message)
    for i, rec in enumerate(resp.recommendations, 1):
        r = rec.restaurant
        with st.container(border=True):
            st.markdown(f"**{i}. {r.name}** · {r.area} · rated {r.rating} · est. ₹{rec.estimated_total:,}")
            st.caption(", ".join(c.replace("_", " ") for c in r.cuisines) + f" · score {rec.score}/100")
            for reason in rec.reasons:
                st.markdown(f"✅ {reason}")
            for t in rec.trade_offs:
                st.markdown(f"⚠️ {t}")
            with st.expander("Score breakdown"):
                st.json(rec.score_breakdown)
    for note in resp.assumptions:
        st.info(note)
    if show_trace and resp.trace:
        with st.expander("Agent trace (how I decided)"):
            st.code("\n".join(f"[{t.stage}] {t.detail}" for t in resp.trace), language=None, wrap_lines=True)


for role, payload in history:
    with st.chat_message(role):
        if role == "user":
            st.markdown(payload)
        else:
            show(payload)

prompt = st.chat_input("e.g. veg dinner for 2 in Baner under ₹1200") or clicked
if prompt:
    history.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)
    response = agent.run(prompt, state)
    history.append(("assistant", response))
    with st.chat_message("assistant"):
        show(response)
