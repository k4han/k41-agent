import app as legacy_app
import agent.graphs as legacy_graphs
from agent.bootstrap.app import create_app, run
from agent.bootstrap.settings import AppSettings


def test_legacy_app_exports_bootstrap_symbols():
    assert legacy_app.create_app is create_app
    assert legacy_app.run is run
    assert legacy_app.AppSettings is AppSettings


def test_legacy_setup_all_graphs_delegates_to_new_registration(monkeypatch):
    called = {"value": False}

    def fake_register() -> None:
        called["value"] = True

    monkeypatch.setattr(legacy_graphs, "register_builtin_workflows", fake_register)

    legacy_graphs.setup_all_graphs()

    assert called["value"] is True
