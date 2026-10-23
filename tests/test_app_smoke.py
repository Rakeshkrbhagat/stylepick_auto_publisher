"""Smoke test: the Streamlit app boots and shows sample results without errors."""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402


def test_app_sample_results():
    at = AppTest.from_file("app.py", default_timeout=60).run()
    assert not at.exception, at.exception
    next(b for b in at.sidebar.button if b.label == "Load sample data").click().run()
    assert not at.exception, at.exception
    next(b for b in at.button if b.label == "Get videos & captions").click().run()
    assert not at.exception, at.exception
    assert any(s.value == "Results" for s in at.subheader)
    codes = " ".join(c.value for c in at.code)
    assert "फ्लोरल कुर्ती" in codes and "#Shorts" not in codes
    assert "Published" not in " ".join(m.value for m in at.markdown)

