"""Diagnostics, historical reports, discovery and explicit live smoke."""
from __future__ import annotations

import copy
import json
import time

from ..catalog import import_catalog, load_catalog, pricing_snapshot
from ..contracts import preflight_measurement, preflight_metadata
from ..evidence import AttemptRecorder
from ..operations import attempt_report, doctor_report, profile_report, usage_report
from ..usage import UsageByUnitResult, usage_by_unit_report
from ..util import PipelineError
from .commands import (
    AttemptsCommand, CatalogImportCommand, DiscoverCommand, DoctorCommand, ProfilesCommand,
    SmokeCommand, UsageByUnitCommand, UsageCommand,
)
from .ports import ApplicationDependencies, ProgressSink
from .projects import effective_settings, load_valid_book
from .results import ReportResult
from .sessions import OperationScope


class OperationsService:
    def __init__(self, dependencies: ApplicationDependencies, progress: ProgressSink):
        self.dependencies, self.progress = dependencies, progress

    def _offline(self, project, operation):
        root = project.resolve()
        with OperationScope(self.dependencies, root, self.progress):
            load_valid_book(root, self.dependencies.plan_fingerprint, self.dependencies.files)
            return ReportResult(copy.deepcopy(operation(root)))

    def profiles(self, command: ProfilesCommand) -> ReportResult:
        return self._offline(command.project, lambda root: profile_report(
            effective_settings(self.dependencies.bundle, root, files=self.dependencies.files), root))

    def doctor(self, command: DoctorCommand) -> ReportResult:
        return self._offline(command.project, lambda root: doctor_report(
            effective_settings(self.dependencies.bundle, root, files=self.dependencies.files), root))

    def attempts(self, command: AttemptsCommand) -> ReportResult:
        return self._offline(command.project, lambda root: attempt_report(root, command.attempt))

    def usage(self, command: UsageCommand) -> ReportResult:
        return self._offline(command.project, usage_report)

    def usage_by_unit(self, command: UsageByUnitCommand) -> UsageByUnitResult:
        root = command.project.resolve()
        with OperationScope(self.dependencies, root, self.progress):
            load_valid_book(root, self.dependencies.plan_fingerprint, self.dependencies.files)
            return usage_by_unit_report(root, command.unit_id)

    def import_catalog(self, command: CatalogImportCommand) -> ReportResult:
        return self._offline(command.project, lambda root: import_catalog(command.source, root))

    def discover(self, command: DiscoverCommand) -> ReportResult:
        if command.pass_no not in range(1, 6) or type(command.pass_no) is not int:
            raise PipelineError("pass_no must be an integer from 1 through 5.")
        root = command.project.resolve()
        with OperationScope(self.dependencies, root, self.progress) as scope:
            load_valid_book(root, self.dependencies.plan_fingerprint, self.dependencies.files)
            settings = effective_settings(self.dependencies.bundle, root, files=self.dependencies.files)
            providers = scope.providers(settings, profile=command.profile)
            return ReportResult(copy.deepcopy(providers.discover(command.pass_no)))

    def smoke(self, command: SmokeCommand) -> ReportResult:
        if not command.live:
            raise PipelineError("smoke requires --live because it starts a billable/model turn.")
        if command.pass_no not in range(1, 6) or type(command.pass_no) is not int:
            raise PipelineError("pass_no must be an integer from 1 through 5.")
        root = command.project.resolve()
        with OperationScope(self.dependencies, root, self.progress) as scope:
            load_valid_book(root, self.dependencies.plan_fingerprint, self.dependencies.files)
            settings = effective_settings(self.dependencies.bundle, root, files=self.dependencies.files)
            provider = scope.providers(settings, profile=command.profile).for_pass(command.pass_no)
            stamp = str(time.time_ns())
            attempt = root / "artifacts" / "smoke" / stamp / "attempt_001"
            schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                      "required": ["ok"], "additionalProperties": False}
            prompt = "Return the requested JSON object and nothing else."
            inputs = {"REQUEST": "Set ok to true."}
            recorder = AttemptRecorder(attempt, {
                "operation": "live_smoke", "provider": provider.provider,
                "profile": provider.profile_name, "requested_model": provider.model,
            })
            recorder.semantic({"trusted_instructions": prompt, "input_payload": inputs,
                               "output_schema": schema, "resolved_profile": provider.resolved_profile}, schema)
            catalog, catalog_path = load_catalog(root)
            recorder.pricing(pricing_snapshot(catalog, catalog_path, provider.provider, provider.model))
            try:
                if provider.provider == "llamacpp" and not provider.identity.get("id"):
                    recorder.event("outbound", "provider_discovery", {"provider": "llamacpp"})
                    recorder.event("inbound", "provider_discovery", provider.discover())
                body = provider.body(prompt, inputs, schema, command.pass_no)
                count = provider.preflight(body, recorder)
                measurement_meta = preflight_metadata(preflight_measurement(provider, count))
                answer, metadata = provider.generate(body, attempt, recorder)
                if json.loads(answer) != {"ok": True}:
                    raise PipelineError("Smoke response did not match the requested structured value.")
                recorder.finish(generation="completed", validation="passed",
                                metadata={**metadata, **measurement_meta})
                return ReportResult({"status": "passed", "attempt": str(attempt.relative_to(root)), **metadata})
            except BaseException as exc:
                recorder.finish(generation="failed", validation="failed", metadata={"status": "failed"},
                                error={"type": type(exc).__name__, "message": str(exc)})
                raise
