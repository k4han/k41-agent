import app as legacy_app
from agent.bootstrap.app import create_app, run
from agent.bootstrap.settings import BootstrapConfig, load_bootstrap_config
from agent.modules.workflows import public as workflow_public


def test_legacy_app_exports_bootstrap_symbols():
    assert legacy_app.create_app is create_app
    assert legacy_app.run is run
    assert legacy_app.BootstrapConfig is BootstrapConfig
    assert legacy_app.load_bootstrap_config is load_bootstrap_config


def test_legacy_setup_all_graphs_delegates_to_new_registration(monkeypatch):
    called = {"value": False}

    def fake_register() -> None:
        called["value"] = True

    monkeypatch.setattr(workflow_public, "register_builtin_workflows", fake_register)

    workflow_public.register_builtin_workflows()

    assert called["value"] is True
