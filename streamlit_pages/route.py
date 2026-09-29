"""Thin Streamlit-native page entry for durable browser history."""

from pathlib import Path
from runpy import run_path
from urllib.parse import urlparse

import streamlit as st


ROUTE_BY_PATH = {
    "": "home",
    "demo": "demo",
    "demo-scenario": "demo_detail",
    "run": "configure",
    "run-review": "review",
    "saved": "saved",
    "saved-view": "saved_view",
    "saved-rejudge": "saved_rejudge",
    "results": "results",
    "scenario": "result_detail",
    "rejudge": "rejudge_configure",
    "rejudge-review": "rejudge_review",
}


path = urlparse(st.context.url or "").path.rstrip("/").rsplit("/", maxsplit=1)[-1]
route = ROUTE_BY_PATH.get(path, "home")
application = run_path(
    Path(__file__).parents[1] / "streamlit_app.py", run_name="streamlit_route",
)
application["main"](route)
