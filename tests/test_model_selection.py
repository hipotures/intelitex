"""Model selection is free; saved work and independent requests remain intact."""
from dataclasses import fields
from types import SimpleNamespace

import pytest

from bookpipe.application.commands import AnalyzeCommand, TranslateCommand
from bookpipe.application.pipeline import report_model_change
from bookpipe.application.results import PipelineResult
from bookpipe.bootstrap import create_application
from bookpipe.cli import _model_options, parser
from bookpipe.provider_registry import ProviderPool
from bookpipe.util import atomic_json, digest, read_json
from test_server_api import api, analyze, approve, translate
from test_runtime import request


@pytest.mark.parametrize("operation", ["import", "analyze", "translate"])
def test_cli_selects_models_without_legacy_permission_option(tmp_path, operation):
    args = [operation, "--project", str(tmp_path), "--profile", "codex-sol-high",
            "--pass-profile", "3=codex-astra-medium"]
    if operation == "import":
        args.append(str(tmp_path / "source"))
    parsed = parser().parse_args(args)
    options = _model_options(parsed)
    assert options["profile"] == "codex-sol-high"
    assert options["pass_profiles"] == {3: "codex-astra-medium"}
    assert "allow_model_change" not in options
    choices = next(a for a in parser()._actions if a.dest == "command").choices
    assert "--allow-model-change" not in choices[operation]._option_string_actions
    assert "allow_model_change" not in {field.name for field in fields(TranslateCommand)}


@pytest.mark.parametrize("section", [False, True])
@pytest.mark.parametrize("legacy_flag", [None, False, True])
def test_prepared_profile_changes_need_no_confirmation_and_preserve_checkpoints(api, section, legacy_flag):
    app, root, service, server = api
    analyze(root)
    approve(service)
    translate(root)
    book = read_json(root / "book.json")
    config = {"sections": {}, "pass_profiles": {}, "accepted_settings_digest": "old-receipt"}
    atomic_json(root / "web.config.json", config)
    protected = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
                 and (p.name in {"book.json", "terms.review.json", "lexicon.approved.json"}
                      or p.is_relative_to(root / "artifacts") or p.is_relative_to(root / "state"))}
    path = (f"sections/{book['chapters'][0]['id']}" if section else "settings")
    key = "profiles" if section else "pass_profiles"
    payload = {"revision": digest(config), key: {"2": "codex-sol-high", "3": "codex-astra-medium"}}
    if legacy_flag is not None:
        payload["allow_model_change"] = legacy_flag
    code, changed = request(server, "PATCH", f"/api/workspaces/book/{path}", payload)
    assert code == 200
    assignments = changed["sections"][book['chapters'][0]['id']][key] if section else changed[key]
    assert assignments["2"] == "codex-sol-high" and assignments["3"] == "codex-astra-medium"
    # A historical receipt is tolerated but never consulted or rewritten.
    assert changed["accepted_settings_digest"] == "old-receipt"
    assert all((root / name).read_bytes() == value for name, value in protected.items())
    code, stale = request(server, "PATCH", f"/api/workspaces/book/{path}", payload)
    assert code == 409 and stale["error"]["code"] == "config_revision_conflict"


@pytest.mark.parametrize("operation", ["analyze", "translate"])
@pytest.mark.parametrize("receipt", [None, "stale-receipt"])
def test_pipeline_start_accepts_selected_model_without_receipt_or_inference(api, monkeypatch, operation, receipt):
    _, root, service, _ = api
    if operation == "translate":
        analyze(root)
        approve(service)
        translate(root)
    config = {"sections": {}, "pass_profiles": {"1": "codex-sol-high", "2": "codex-sol-high"}}
    if receipt is not None:
        config["accepted_settings_digest"] = receipt
    atomic_json(root / "web.config.json", config)
    original = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    observed = []

    def no_inference(store, book, client, settings, progress, *args):
        selected = client.for_pass(1 if operation == "analyze" else 2)
        observed.append((selected.model, selected.settings["reasoning_effort"]))
        return PipelineResult(root, completed_units=0)

    monkeypatch.setattr(f"bookpipe.application.pipeline.execute_{operation}", no_inference)
    app = create_application(provider_factory=ProviderPool)
    command = AnalyzeCommand(root) if operation == "analyze" else TranslateCommand(root)
    getattr(app.pipeline, operation)(command)
    assert observed == [("gpt-6.1-sol", "high")]
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()} == original


def test_report_model_change_is_informational_and_does_not_mutate_data():
    identity = {"provider": "codex", "requested_model": "gpt-6.1-sol"}
    book = {"model_identity": {"provider": "llamacpp", "id": "import-model"},
            "chunks": [{"id": "fixed-chunk"}]}
    before = digest(book)
    events = []
    report_model_change(SimpleNamespace(identity=identity), book, SimpleNamespace(emit=events.append))
    assert digest(book) == before
    assert events[0].values["replace_successful_outputs"] is False
    assert events[0].values["fixed_chunk_boundaries"] is True
