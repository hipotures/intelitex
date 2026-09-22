"""HTTP-independent control/query adapter; no pipeline execution in this process."""
from dataclasses import asdict

from ..application.commands import UsageByUnitCommand
from ..runtime.models import JobSpec


class ServerService:
    def __init__(self, application, workspaces, supervisor):
        self.application = application
        self.workspaces = workspaces
        self.supervisor = supervisor

    def workspace(self, workspace_id: str) -> dict:
        workspace = self.workspaces.status(workspace_id)
        status = asdict(workspace.status)
        status.pop("project")
        status.pop("readable_output")
        # Detailed exception strings belong to project-local evidence.
        if status["publication"]:
            publication = status["publication"]
            for key in ("last_error", "last_failure"):
                if publication[key]:
                    publication[key] = "Publication is not ready or failed; inspect project state locally."
            if publication["output_path"]:
                publication["output_path"] = str(publication["output_path"].relative_to(workspace.status.project))
        active = self.supervisor.active_for_project(workspace.status.project)
        return {"workspace_id": workspace_id, "status": status,
                "active_job": active.public() if active else None}

    def list_workspaces(self) -> list[dict]:
        return [{"workspace_id": ident, "active_job": job.public() if job else None}
                for ident in self.workspaces.list()
                for job in [self.supervisor.active_for_project(self.workspaces.resolve(ident))]]

    def start(self, workspace_id: str, payload: dict):
        if not isinstance(payload, dict) or set(payload) - {"operation", "profile", "chunk_limit", "target_language"}:
            raise ValueError("Unknown job fields.")
        if "operation" not in payload:
            raise ValueError("operation is required.")
        operation = payload["operation"]
        if operation != "translate" and "chunk_limit" in payload:
            raise ValueError("chunk_limit only applies to translate.")
        if operation != "publish" and "target_language" in payload:
            raise ValueError("target_language only applies to publish.")
        if operation == "publish" and "profile" in payload:
            raise ValueError("profile does not apply to publish.")
        spec = JobSpec(str(self.workspaces.root), workspace_id,
                       str(self.workspaces.resolve(workspace_id)), **payload)
        return self.supervisor.start(spec).public()

    def usage(self, workspace_id: str):
        return asdict(self.application.operations.usage_by_unit(
            UsageByUnitCommand(self.workspaces.resolve(workspace_id))))
