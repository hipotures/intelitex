"""Terminology draft operations, review sessions and committed approval."""
from __future__ import annotations

from contextlib import nullcontext
import copy
import json
import threading
from pathlib import Path

from ..review_context import EvidenceReader
from ..util import PipelineError, atomic_json, digest, normalized, read_json
from .commands import ApproveCommand, ReviewSessionCommand
from .ports import ApplicationDependencies, ProgressSink
from .projects import load_valid_book
from .results import ApprovalResult
from .sessions import OperationScope


def _analysis_observations(path: Path, terms: list[dict], files=None) -> dict[str, list[dict]]:
    memory_path = path.with_name("book_memory.json")
    if not (files.is_file(memory_path) if files else memory_path.is_file()):
        return {}
    try:
        memory = files.read_json(memory_path) if files else read_json(memory_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    facts = memory.get("observations", [])
    if not isinstance(facts, list):
        return {}
    names: dict[str, set[str]] = {}
    for term in terms:
        term_id = term.get("id")
        if not isinstance(term_id, str):
            continue
        for value in [term.get("source", ""), *(term.get("aliases") or [])]:
            if isinstance(value, str) and normalized(value):
                names.setdefault(normalized(value), set()).add(term_id)
    attached: dict[str, list[dict]] = {}
    seen: dict[str, set[str]] = {}
    for fact in facts:
        if not isinstance(fact, dict) or not isinstance(fact.get("about", []), list):
            continue
        term_ids: set[str] = set()
        for value in fact["about"]:
            if isinstance(value, str):
                term_ids.update(names.get(normalized(value), ()))
        key = json.dumps(fact, ensure_ascii=False, sort_keys=True)
        for term_id in term_ids:
            if key in seen.setdefault(term_id, set()):
                continue
            seen[term_id].add(key)
            attached.setdefault(term_id, []).append(fact)
    return attached


def ensure_review_state(path: Path, files=None) -> dict:
    """Hydrate legacy draft fields without changing lexical choices."""
    review = files.read_json(path) if files else read_json(path)
    changed = False
    terms = review.get("terms")
    if not isinstance(terms, list):
        raise PipelineError("terms.review.json has no valid terms array.")
    observations = (_analysis_observations(path, terms, files)
                    if any("observations" not in term for term in terms) else {})
    for term in terms:
        if "reviewed" not in term:
            term["reviewed"] = False
            changed = True
        if "user_notes" not in term:
            term["user_notes"] = ""
            changed = True
        if "observations" not in term:
            term["observations"] = observations.get(term.get("id", ""), [])
            changed = True
    if changed:
        (files.write_json(path, review) if files else atomic_json(path, review))
    return review


def review_summary(review: dict) -> dict:
    terms = review.get("terms", [])
    categories: dict[str, int] = {}
    reviewed = uncertain = noted = 0
    for term in terms:
        category = term.get("category", "other")
        categories[category] = categories.get(category, 0) + 1
        reviewed += bool(term.get("reviewed"))
        noted += bool(str(term.get("user_notes", "")).strip())
        confidences = [note.get("confidence") for note in term.get("meaning_notes", [])]
        confidences.extend(candidate.get("confidence") for candidate in term.get("candidates", []))
        confidences.extend(observation.get("confidence") for observation in term.get("observations", [])
                           if isinstance(observation, dict))
        uncertain += any(confidence in {"medium", "low"} for confidence in confidences)
    return {
        "total": len(terms), "reviewed": reviewed, "unreviewed": len(terms) - reviewed,
        "uncertain": uncertain, "noted": noted, "categories": categories,
        "confirmed": review.get("confirmed") is True,
    }


class ReviewConflict(PipelineError):
    """The draft changed since the caller loaded it."""


class ReviewRepository:
    """Session-confined draft service; mutations hold one mutex end to end."""

    def __init__(self, path: Path, files=None, mutation_lock=None):
        self.path = path
        self.lock = threading.Lock()
        self.evidence_reader = EvidenceReader(path.parent)
        self.files = files
        self.mutation_lock = mutation_lock or nullcontext

    def _load_state(self):
        return ensure_review_state(self.path, self.files)

    def _write(self, path, value):
        return self.files.write_json(path, value) if self.files else atomic_json(path, value)

    def load(self) -> dict:
        with self.lock, self.mutation_lock():
            review = self._load_state()
            return copy.deepcopy({**review, "_revision": digest(review)})

    @staticmethod
    def _check_revision(review: dict, expected_revision: str | None) -> None:
        if expected_revision is not None and expected_revision != digest(review):
            raise ReviewConflict(
                "Review data changed in another tab or process. Reload before saving; no changes were written."
            )

    @staticmethod
    def _check_choice(term: dict) -> None:
        if str(term.get("custom", "")).strip():
            return
        selected = term.get("select")
        if type(selected) is not int or not any(
            candidate.get("number") == selected and str(candidate.get("text", "")).strip()
            for candidate in term.get("candidates", [])
        ):
            raise PipelineError(f"No valid selected/custom form for {term['id']}.")

    def evidence(self, term_id: str) -> dict:
        with self.lock, self.mutation_lock():
            review = self._load_state()
            term = next((term for term in review["terms"] if term.get("id") == term_id), None)
            if term is None:
                raise PipelineError(f"Unknown terminology ID: {term_id}.")
            return copy.deepcopy(self.evidence_reader.for_term(term))

    def review_terms(self, term_ids: list[str], expected_revision: str) -> dict:
        if (not isinstance(term_ids, list) or not term_ids or len(term_ids) > 10_000
                or not all(isinstance(term_id, str) for term_id in term_ids)
                or len(set(term_ids)) != len(term_ids)):
            raise PipelineError("term_ids must be a nonempty list of unique term IDs.")
        if not isinstance(expected_revision, str) or not expected_revision:
            raise PipelineError("Bulk review requires the loaded review revision.")
        with self.lock, self.mutation_lock():
            review = self._load_state()
            self._check_revision(review, expected_revision)
            indexed = {term["id"]: term for term in review["terms"]}
            if any(term_id not in indexed for term_id in term_ids):
                raise PipelineError("Bulk review contains an unknown term ID.")
            targets = [indexed[term_id] for term_id in term_ids if not indexed[term_id].get("reviewed")]
            for term in targets:
                self._check_choice(term)
            if targets:
                self._write(
                    self.path.parent / "history" / f"review_before_bulk_{digest(review)[:16]}.json", review,
                )
                for term in targets:
                    term["reviewed"] = True
                    term["review_method"] = "bulk"
                self._write(self.path, review)
            return copy.deepcopy({
                "terms": targets, "changed_count": len(targets),
                "summary": review_summary(review), "revision": digest(review),
            })

    def patch_term(self, term_id: str, patch: dict, expected_revision: str | None = None) -> dict:
        allowed = {"select", "custom", "reviewed", "user_notes"}
        unknown = set(patch) - allowed
        if unknown:
            raise PipelineError(f"Unsupported review field(s): {', '.join(sorted(unknown))}.")
        with self.lock, self.mutation_lock():
            review = self._load_state()
            self._check_revision(review, expected_revision)
            term = next((term for term in review["terms"] if term.get("id") == term_id), None)
            if term is None:
                raise PipelineError(f"Unknown terminology ID: {term_id}.")
            old_select, old_custom = term.get("select"), term.get("custom", "")
            changed_choice = False
            if "select" in patch:
                value = patch["select"]
                if type(value) is not int or not 1 <= value <= len(term.get("candidates", [])):
                    raise PipelineError(f"Invalid candidate number for {term_id}.")
                term["select"] = value
                changed_choice |= value != old_select
            if "custom" in patch:
                value = patch["custom"]
                if not isinstance(value, str):
                    raise PipelineError("custom must be a string.")
                value = value.replace("\r", "")
                term["custom"] = value
                changed_choice |= value != old_custom
            if "user_notes" in patch:
                value = patch["user_notes"]
                if not isinstance(value, str):
                    raise PipelineError("user_notes must be a string.")
                if len(value) > 100_000:
                    raise PipelineError("user_notes is too long.")
                term["user_notes"] = value.replace("\r", "")
            if "reviewed" in patch:
                if type(patch["reviewed"]) is not bool:
                    raise PipelineError("reviewed must be boolean.")
                term["reviewed"] = patch["reviewed"]
                term["review_method"] = "individual"
                if term["reviewed"]:
                    self._check_choice(term)
            elif changed_choice:
                term["reviewed"] = False
            if changed_choice or patch.get("reviewed") is False:
                review["confirmed"] = False
            self._write(self.path, review)
            return copy.deepcopy({
                "term": term, "summary": review_summary(review), "revision": digest(review),
            })

    def set_confirmed(self, confirmed: bool, expected_revision: str | None = None) -> dict:
        if type(confirmed) is not bool:
            raise PipelineError("confirmed must be boolean.")
        with self.lock, self.mutation_lock():
            review = self._load_state()
            self._check_revision(review, expected_revision)
            if confirmed:
                for term in review["terms"]:
                    self._check_choice(term)
                pending = [term["id"] for term in review["terms"] if not term.get("reviewed")]
                if pending:
                    raise PipelineError(
                        f"Cannot confirm: {len(pending)} term(s) are still unreviewed. "
                        "Review them or use approve --accept-defaults intentionally."
                    )
            review["confirmed"] = confirmed
            self._write(self.path, review)
            return copy.deepcopy({"summary": review_summary(review), "revision": digest(review)})


class ReviewSession:
    def __init__(self, dependencies: ApplicationDependencies, command: ReviewSessionCommand,
                 progress: ProgressSink):
        self.dependencies, self.command, self.progress = dependencies, command, progress
        self.project = command.project.resolve()
        self._repository: ReviewRepository | None = None

    def __enter__(self) -> ReviewSession:
        # Initial draft generation is a mutation, but a browser session is not.
        with OperationScope(self.dependencies, self.project, self.progress) as scope:
            book = load_valid_book(
                self.project, self.dependencies.plan_fingerprint, self.dependencies.files,
            )
            if not scope.store.get("analysis_done"):
                raise PipelineError("Analysis has not finished; run analyze to resume it.")
            path = scope.store.write_review(book["source_fingerprint"])
            repository = ReviewRepository(path, self.dependencies.files)
            repository.load()
        repository.mutation_lock = lambda: self.dependencies.project_lock(self.project)
        self._repository = repository
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._repository = None

    @property
    def path(self) -> Path:
        return self._repo.path

    @property
    def _repo(self) -> ReviewRepository:
        if self._repository is None:
            raise RuntimeError("ReviewSession must be entered before use.")
        return self._repository

    def load(self): return self._repo.load()
    def summary(self): return review_summary(self._repo.load())
    def evidence(self, term_id): return self._repo.evidence(term_id)
    def patch_term(self, term_id, patch, expected_revision=None):
        return self._repo.patch_term(term_id, patch, expected_revision)
    def review_terms(self, term_ids, expected_revision):
        return self._repo.review_terms(term_ids, expected_revision)
    def set_confirmed(self, confirmed, expected_revision=None):
        return self._repo.set_confirmed(confirmed, expected_revision)


class ReviewService:
    def __init__(self, dependencies: ApplicationDependencies, progress: ProgressSink):
        self.dependencies, self.progress = dependencies, progress

    def open_session(self, command: ReviewSessionCommand | Path) -> ReviewSession:
        if isinstance(command, Path):
            command = ReviewSessionCommand(command)
        return ReviewSession(self.dependencies, command, self.progress)

    def approve(self, command: ApproveCommand) -> ApprovalResult:
        root = command.project.resolve()
        with OperationScope(self.dependencies, root, self.progress) as scope:
            book = load_valid_book(root, self.dependencies.plan_fingerprint, self.dependencies.files)
            if not scope.store.get("analysis_done"):
                raise PipelineError("Finish analysis before approving terminology.")
            return execute_approval(
                scope.store, book["source_fingerprint"], command.accept_defaults,
                self.dependencies.files,
            )


def execute_approval(store, fingerprint: str, accept_defaults: bool, files=None) -> ApprovalResult:
    """Validate and commit the existing draft without merging draft and DB state."""
    path = store.root / "terms.review.json"
    if not (files.exists(path) if files else path.exists()):
        raise PipelineError("Run analyze first; no terms.review.json exists.")
    review = files.read_json(path) if files else read_json(path)
    stored = store.terms()
    if review.get("book_fingerprint") != fingerprint:
        raise PipelineError("Review belongs to another imported book.")
    if review.get("analysis_revision") != digest(stored):
        raise PipelineError("Review is stale after a memory change. Run review to regenerate it safely.")
    if not accept_defaults and review.get("confirmed") is not True:
        raise PipelineError("Set confirmed=true in terms.review.json, or explicitly use approve --accept-defaults.")
    expected = {term["id"] for term in stored}
    entries = review.get("terms", [])
    if len(entries) != len(expected) or {term.get("id") for term in entries} != expected:
        raise PipelineError("Review term IDs are missing, duplicated or unknown. Do not delete entries.")
    indexed = {term["id"]: term for term in stored}
    decisions = []
    for entry in entries:
        old = indexed[entry["id"]]
        custom = entry.get("custom", "")
        if not isinstance(custom, str):
            raise PipelineError("custom must be a string.")
        chosen = custom.strip()
        if not chosen:
            number = entry.get("select")
            if type(number) is not int or not 1 <= number <= len(old["candidates"]):
                raise PipelineError(f"Invalid candidate number for {entry['id']}.")
            chosen = old["candidates"][number - 1]["text"]
        decisions.append((old, chosen))
    changed = {old["id"] for old, choice in decisions if old["choice"] and old["choice"] != choice}
    store.backup_approval(review)
    stale = store.commit_approval(decisions, changed)
    store.export_memory()
    review["analysis_revision"] = digest(store.terms())
    review["confirmed"] = True
    write = files.write_json if files else atomic_json
    write(path, review)
    write(store.root / "lexicon.approved.json", {"terms": [
        {"id": term["id"], "source": term["source"], "aliases": term["aliases"],
         "polish": term["choice"]}
        for term in store.terms()
    ]})
    return ApprovalResult(len(decisions), stale)
