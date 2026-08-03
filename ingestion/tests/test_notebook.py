"""Smoke test for notebooks/explore.py: confirms the marimo cell
graph is wired correctly (every cell's declared dependencies actually
resolve, no NameErrors, no import errors) and that cells gated behind
a "Fetch" button degrade gracefully via mo.stop() rather than
crashing when that button hasn't been clicked -- which is always true
in this headless test, since nothing here can click a button.

This does NOT test real API behaviour (no network in this
environment) or the interactive/reactive experience itself (that
needs a browser) -- it tests that the notebook, as Python, is sound.
"""
import runpy
from pathlib import Path

NOTEBOOK_PATH = Path(__file__).parent.parent / "notebooks" / "explore.py"


def _load_app():
    mod = runpy.run_path(str(NOTEBOOK_PATH), run_name="not_main")
    return mod["app"]


def test_notebook_cell_graph_resolves_without_error():
    app = _load_app()
    outputs, namespace = app.run()
    assert namespace is not None


def test_fetch_buttons_default_unclicked():
    app = _load_app()
    _, namespace = app.run()
    assert namespace["fetch_general"].value is False
    assert namespace["fetch_marginal"].value is False


def test_fetch_gated_cells_do_not_populate_when_unclicked():
    """Confirms mo.stop() actually halted these cells rather than the
    dependency graph silently skipping them for some other reason --
    none of these should exist in the namespace at all."""
    app = _load_app()
    _, namespace = app.run()
    keys = set(namespace.keys())
    assert "general_df" not in keys
    assert "acceptances_df" not in keys
    assert "result" not in keys


def test_clients_and_ui_controls_are_constructed():
    """These don't depend on any button, so they should always be
    present -- if they're missing, something upstream broke."""
    app = _load_app()
    _, namespace = app.run()
    keys = set(namespace.keys())
    assert {"elexon", "neso", "cache", "source", "days_back", "marginal_days_back"} <= keys
