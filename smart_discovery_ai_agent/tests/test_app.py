"""UI smoke test using Streamlit's built-in AppTest (skipped if Streamlit is absent)."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_tc60_streamlit_ui_runs_a_request():
    """TC-60: the UI loads, accepts a request and shows the agent's top pick."""
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    at.chat_input[0].set_value("veg birthday dinner for 4 in Wakad under 3000").run()
    assert not at.exception
    assert any("Rooftop Rasoi" in m.value for m in at.markdown)
