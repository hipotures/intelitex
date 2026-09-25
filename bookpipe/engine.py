from __future__ import annotations

import copy
import json
import re
from uuid import uuid4
from pathlib import Path
from typing import Any, Callable

import jsonschema

from .client import Client, ContextFull
from .catalog import apply_estimate, load_catalog, pricing_snapshot
from .contracts import InferenceUnitContext, SemanticRequest, preflight_measurement, preflight_metadata
from .evidence import AttemptRecorder, EvidenceError
from .importer import pack_blocks, split_long
from .p1_compact import decode_output as decode_compact_p1
from .progress import ProgressEvent
from .schemas import SCHEMAS
from .store import Store
from .util import PipelineError, atomic_json, atomic_text, digest, dumps, normalized, occurs, read_json


def exact_ids(rows: list[dict], field: str, expected: list[str], *, ordered: bool = False):
    got = [x[field] for x in rows]
    if len(got) != len(expected) or set(got) != set(expected):
        raise PipelineError(f"ID coverage mismatch: missing={sorted(set(expected)-set(got))[:12]}, "
                            f"unexpected={sorted(set(got)-set(expected))[:12]}, duplicates={len(got)-len(set(got))}")
    if ordered and got != expected:
        raise PipelineError("Source block order was changed.")


def _existing_memory_sources(inputs: dict) -> set[str]:
    """Canonical source forms already established by prior P1 sections."""
    memory = inputs.get("EXISTING_MEMORY") or {}
    result = set()
    for bucket in ("matched", "catalogue"):
        for term in memory.get(bucket, []) or []:
            source = term.get("source") if isinstance(term, dict) else None
            if isinstance(source, str) and source.strip():
                result.add(normalized(source))
    return result


def validate_result(pass_no: int, value: dict, inputs: dict):
    jsonschema.Draft202012Validator(SCHEMAS[pass_no]).validate(value)
    blocks = {b["id"]: b for b in inputs["SOURCE_BLOCKS"]}
    if pass_no == 1:
        seen = set()
        section_text = "\n".join(b["text"] for b in inputs["SOURCE_BLOCKS"])
        existing_sources = _existing_memory_sources(inputs)
        for term in value["terms"]:
            key = normalized(term["source"])
            if key in seen:
                raise PipelineError(f"Duplicate term: {term['source']}")
            seen.add(key)
            if not set(term["evidence"]) <= set(blocks):
                raise PipelineError(f"Unknown term evidence for {term['source']}.")
            cited = "\n".join(blocks[b]["text"] for b in term["evidence"])
            if not any(occurs(cited, s) for s in [term["source"]] + term["aliases"]):
                raise PipelineError(f"Term/alias is not attested in its cited source blocks: {term['source']}")
            # aliases are claimed as source variants, so every newly returned
            # alias must actually occur somewhere in this source unit. Existing
            # aliases need not be replayed by P1 and are retained in Store.merge_analysis.
            bad_aliases = [a for a in term["aliases"] if not occurs(section_text, a)]
            if bad_aliases:
                raise PipelineError(f"Unattested alias(es) for {term['source']}: {bad_aliases[:8]}")
            # A canonical source may be absent from the current section only if
            # it is the already-established source form of an existing memory entry
            # and an attested alias is what appears in the current text.
            if not occurs(section_text, term["source"]) and key not in existing_sources:
                raise PipelineError(f"Unattested canonical source form: {term['source']}")
        for fact in value["observations"]:
            unknown = sorted(set(fact["evidence"]) - set(blocks))
            if unknown:
                raise PipelineError(f"Observation has invented evidence IDs: {unknown[:12]}; about={fact.get('about', [])!r}")
    elif pass_no in (2, 4):
        sentences = {s["id"]: s for s in inputs["SOURCE_SENTENCES"]}
        exact_ids(value["checks"], "sid", list(sentences))
        if pass_no == 2:
            records = value["issues"]
        else:
            records = value["corrections"]
        for issue in records:
            if issue["sid"] not in sentences:
                raise PipelineError("Issue refers to an unknown source sentence.")
            span = normalized(issue["source_span"])
            if not span or span not in normalized(sentences[issue["sid"]]["text"]):
                raise PipelineError(f"source_span is not an exact quote for {issue['sid']}.")
            if pass_no == 4:
                drafts = {b["id"]: b["text"] for b in inputs["POLISH_DRAFT"]["translations"]}
                if issue["block_id"] not in drafts:
                    raise PipelineError("Correction refers to unknown draft block.")
                if issue["draft_span"] and normalized(issue["draft_span"]) not in normalized(drafts[issue["block_id"]]):
                    raise PipelineError("draft_span is not found in the specified draft block.")
        if pass_no == 4:
            has_correction = {x["sid"] for x in records}
            if has_correction != {x["sid"] for x in value["checks"] if x["status"] == "needs_correction"}:
                raise PipelineError("Correction ledger and sentence statuses disagree.")
    else:
        exact_ids(value["translations"], "id", list(blocks), ordered=True)
        for result in value["translations"]:
            if not result["text"].strip():
                raise PipelineError("Empty translated block.")
            if re.search(r"<think>|### PASS [1-5]|```json", result["text"]):
                raise PipelineError("Analysis/pipeline markup leaked into a translation block.")

def response_schema(pass_no: int, inputs: dict) -> dict:
    """Return the effective response schema for this exact request.

    Pass 1 evidence and P3/P5 translations are constrained to the block IDs
    that actually exist in the current source unit. Structured-output providers
    can reject incomplete translation arrays during generation; the application
    still validates exact ID coverage and order after generation.
    """
    schema = copy.deepcopy(SCHEMAS[pass_no])
    if pass_no == 1:
        allowed = [b["id"] for b in inputs["SOURCE_BLOCKS"]]
        evidence_item = {"type": "string", "enum": allowed}
        schema["properties"]["terms"]["items"]["properties"]["evidence"]["items"] = copy.deepcopy(evidence_item)
        schema["properties"]["observations"]["items"]["properties"]["evidence"]["items"] = copy.deepcopy(evidence_item)
    elif pass_no in (3, 5):
        allowed = [b["id"] for b in inputs["SOURCE_BLOCKS"]]
        translations = schema["properties"]["translations"]
        translations["minItems"] = len(allowed)
        translations["maxItems"] = len(allowed)
        translations["items"]["properties"]["id"]["enum"] = allowed
    return schema


def _exact_source_span(candidate: str, sentence: str) -> str | None:
    """Ground an elided or markup-stripped quote in one contiguous source span.

    Every word in the model quote must occur in source order. A foreign word,
    empty quote, or weak one-word match remains a validation failure.
    """
    words = [match.group().casefold() for match in re.finditer(r"\w+", candidate)]
    source_words = [(match.group().casefold(), match.start(), match.end())
                    for match in re.finditer(r"\w+", sentence)]
    if len(words) < 2 or not any(len(word) >= 4 for word in words):
        return None
    matches = []
    for start, (word, first, _) in enumerate(source_words):
        if word != words[0]:
            continue
        position = start
        for wanted in words[1:]:
            position = next((index for index in range(position + 1, len(source_words))
                             if source_words[index][0] == wanted), -1)
            if position < 0:
                break
        else:
            matches.append(sentence[first:source_words[position][2]])
    if not matches:
        return None
    exact = min(matches, key=len)
    # A large gap without an explicit ellipsis is likely a wrong citation, not
    # stripped formatting. Leave it for validation and a targeted model retry.
    if "..." not in candidate and "…" not in candidate and len(exact) > 2 * len(candidate) + 8:
        return None
    return exact


def conservative_repair(pass_no: int, value: dict, inputs: dict) -> tuple[dict, list[dict]]:
    """Apply only repairs that cannot add unsupported information.

    P1 repair policy:
    - observations with invented evidence IDs are dropped as a whole;
    - aliases not literally attested in the current source unit are removed;
    - if a term is real but its cited evidence missed the block containing its
      lexical form, an exact-match source block is appended deterministically;
    - if none of the term's source forms occurs anywhere in the current source
      unit, the whole term delta is dropped rather than guessed or regenerated.

    Existing memory is not edited here. Store.merge_analysis preserves already
    established aliases/canonical forms, so pruning an unsupported alias from a
    single P1 delta cannot erase prior knowledge.
    """
    if pass_no in (2, 4) and isinstance(value, dict):
        field = "issues" if pass_no == 2 else "corrections"
        if not isinstance(value.get(field), list):
            return value, []
        sentences = {item.get("id"): item.get("text") for item in inputs.get("SOURCE_SENTENCES", [])
                     if isinstance(item, dict)}
        repaired = copy.deepcopy(value)
        repairs = []
        for index, item in enumerate(repaired[field]):
            if not isinstance(item, dict) or not isinstance(item.get("source_span"), str):
                continue
            sentence = sentences.get(item.get("sid"))
            if not isinstance(sentence, str) or normalized(item["source_span"]) in normalized(sentence):
                continue
            exact = _exact_source_span(item["source_span"], sentence)
            if exact is not None:
                item["source_span"] = exact
                repairs.append({"action": "align_source_span", "index": index, "sid": item["sid"],
                                "source_span": exact})
        return repaired, repairs
    if pass_no != 1 or not isinstance(value, dict):
        return value, []
    if not isinstance(value.get("observations"), list) or not isinstance(value.get("terms"), list):
        return value, []

    repaired = copy.deepcopy(value)
    blocks = inputs.get("SOURCE_BLOCKS") or []
    by_id = {b.get("id"): b for b in blocks if isinstance(b, dict) and isinstance(b.get("id"), str)}
    allowed = set(by_id)
    section_text = "\n".join(str(b.get("text", "")) for b in blocks if isinstance(b, dict))
    existing_sources = _existing_memory_sources(inputs)
    repairs: list[dict] = []

    # First repair lexical term deltas. Unknown evidence IDs remain fatal: the
    # response schema should already make them impossible for new generations.
    kept_terms = []
    for index, term in enumerate(repaired["terms"]):
        if not isinstance(term, dict):
            kept_terms.append(term)
            continue
        evidence = term.get("evidence")
        if not isinstance(evidence, list) or any(bid not in allowed for bid in evidence):
            kept_terms.append(term)
            continue

        source = term.get("source", "")
        aliases = term.get("aliases") if isinstance(term.get("aliases"), list) else []
        forms = [x for x in [source] + aliases if isinstance(x, str) and x.strip()]

        # Remove aliases the model proposed but which are not source-attested in
        # this unit. Existing aliases from memory are preserved by the database.
        attested_aliases = [a for a in aliases if isinstance(a, str) and occurs(section_text, a)]
        removed_aliases = [a for a in aliases if a not in attested_aliases]
        if removed_aliases:
            term["aliases"] = attested_aliases
            aliases = attested_aliases
            forms = [x for x in [source] + aliases if isinstance(x, str) and x.strip()]
            repairs.append({
                "action": "remove_unattested_aliases",
                "index": index,
                "source": source,
                "aliases": removed_aliases,
            })

        # If the model invented an unattested new canonical form but supplied an
        # attested alias, promote that exact source form. Do not do this for a
        # canonical source already established in EXISTING_MEMORY.
        if (isinstance(source, str) and source.strip() and not occurs(section_text, source)
                and normalized(source) not in existing_sources):
            replacement = next((a for a in aliases if occurs(section_text, a)), None)
            if replacement:
                old = source
                term["source"] = replacement
                term["aliases"] = [a for a in aliases if normalized(a) != normalized(replacement)]
                source = replacement
                aliases = term["aliases"]
                forms = [source] + aliases
                repairs.append({
                    "action": "promote_attested_alias_to_source",
                    "index": index,
                    "old_source": old,
                    "new_source": replacement,
                })

        cited_text = "\n".join(str(by_id[bid].get("text", "")) for bid in evidence)
        if not any(occurs(cited_text, form) for form in forms):
            # The lexical form may be present elsewhere in the same section even
            # if the model cited a contextual block. Add the earliest exact-match
            # block; this is deterministic grounding, not an inferred reference.
            matching = []
            for block in blocks:
                text = str(block.get("text", ""))
                matched_form = next((form for form in forms if occurs(text, form)), None)
                if matched_form:
                    matching.append((block["id"], matched_form))
            if matching:
                bid, matched_form = matching[0]
                if bid not in term["evidence"]:
                    term["evidence"].append(bid)
                repairs.append({
                    "action": "add_exact_term_evidence",
                    "index": index,
                    "source": term.get("source", source),
                    "block_id": bid,
                    "matched_form": matched_form,
                })
            else:
                repairs.append({
                    "action": "drop_unattested_term",
                    "index": index,
                    "source": term.get("source", source),
                    "aliases": term.get("aliases", []),
                })
                continue
        kept_terms.append(term)
    repaired["terms"] = kept_terms

    # Observations cannot be repaired by guessing another source block. If an
    # evidence ID is invalid, discard the complete observation.
    kept_obs = []
    for index, fact in enumerate(repaired["observations"]):
        evidence = fact.get("evidence") if isinstance(fact, dict) else None
        if not isinstance(evidence, list):
            kept_obs.append(fact)
            continue
        invalid = [bid for bid in evidence if bid not in allowed]
        if invalid:
            repairs.append({
                "action": "drop_observation",
                "index": index,
                "invalid_evidence_ids": invalid,
                "about": fact.get("about", []),
                "statement": fact.get("statement", ""),
            })
            continue
        kept_obs.append(fact)
    repaired["observations"] = kept_obs
    return repaired, repairs


_RECOVERY_TRANSIENT_INPUT_KEYS = {
    "VALIDATION_ERROR",
    "RETRY_INSTRUCTION",
    "ALLOWED_EVIDENCE_IDS",
}


def _recovery_signature(body: dict) -> dict:
    """Normalize a request for semantic recovery matching.

    Completed retry attempts from older versions may contain validation-only
    fields in the user JSON. Those fields do not change the underlying source
    unit and must not prevent reuse. Transport-only streaming settings and the
    response schema are also deliberately ignored. Everything else, including
    model/settings/request_extra and the actual system+user content, remains
    part of the signature.
    """
    value = copy.deepcopy(body)
    value.pop("response_format", None)
    value.pop("stream", None)
    value.pop("stream_options", None)

    messages = value.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                for key in _RECOVERY_TRANSIENT_INPUT_KEYS:
                    payload.pop(key, None)
                message["content"] = dumps(payload)
    return value


def _semantic_execution_signature(value: dict) -> dict:
    value = copy.deepcopy(value)
    for key in ("attempt_no", "retry_additions", "profile"):
        value.pop(key, None)
    payload = value.get("input_payload")
    if isinstance(payload, dict):
        # These fields describe a physical validation retry, not a different
        # semantic source task. Keep this allowlist deliberately identical to
        # the one used for legacy request-body recovery normalization.
        for key in _RECOVERY_TRANSIENT_INPUT_KEYS:
            payload.pop(key, None)
    resolved = value.get("resolved_profile")
    if isinstance(resolved, dict):
        resolved.pop("selection_provenance", None)
        options = resolved.get("options")
        if isinstance(options, dict):
            options.pop("p1_wire_format", None)
    return value


def _decode_transport_result(value: Any, wire_format: str, inputs: dict, codec_context: dict | None = None) -> dict:
    if wire_format == "canonical":
        if not isinstance(value, dict):
            raise PipelineError("Canonical model result is not an object.")
        return value
    if wire_format == "compact-v1":
        block_ids = (codec_context or {}).get("block_ids")
        if block_ids is None:
            # Recovery reconstructs the deterministic local map from the
            # canonical semantic input retained with the old attempt.
            block_ids = [block["id"] for block in inputs["SOURCE_BLOCKS"]]
        return decode_compact_p1(value, block_ids)
    raise PipelineError(f"Unknown response wire format: {wire_format!r}")


def _vary_local_sampling(provider: Any, body: dict, attempt_no: int) -> dict:
    """Deterministically diversify successive physical local-model attempts.

    The physical attempt number is persisted in the artifact directory, so the
    variation also advances when a later command resumes the same failed task.
    vLLM diffusion models and cloud transports are left alone because these
    sampling parameters are not supported or verified for them.
    """
    kind = getattr(provider, "provider", None)
    if (kind not in {"llamacpp", "vllm"} or attempt_no <= 1 or
            kind == "vllm" and getattr(provider, "diffusion", False)):
        return body

    varied = copy.deepcopy(body)
    offset = attempt_no - 1
    seed = varied.get("seed")
    temperature = varied.get("temperature")
    if isinstance(seed, int) and not isinstance(seed, bool):
        varied["seed"] = seed + offset
    elif kind == "vllm":
        varied["seed"] = 42 + offset
    if isinstance(temperature, (int, float)) and not isinstance(temperature, bool):
        varied["temperature"] = min(2.0, round(float(temperature) + 0.05 * offset, 10))
    return varied


def _compatible_completed_attempts(key_root: Path, body: dict, semantic: dict | None = None) -> list[Path]:
    """Find completed older attempts whose semantic request matches this one.

    v1.2 retry requests carried VALIDATION_ERROR / RETRY_INSTRUCTION and an
    ALLOWED_EVIDENCE_IDS helper in the user payload. v1.3 compared the complete
    messages literally, so a real attempt_002 could not be recovered even
    though the source request was identical. Recovery now strips only those
    transient retry fields before comparison.
    """
    if not key_root.exists():
        return []
    wanted = _recovery_signature(body)
    found = []
    for attempt in key_root.glob("*/attempt_*"):
        answer = attempt / "answer.txt"
        request = attempt / "request.json"
        meta = attempt / "response_meta.json"
        if not (answer.is_file() and request.is_file() and meta.is_file()):
            continue
        try:
            metadata = read_json(meta)
        except Exception:
            continue
        if metadata.get("finish_reason") != "stop":
            continue
        semantic_path = attempt / "request.semantic.json"
        if semantic is not None and semantic_path.is_file():
            try:
                if _semantic_execution_signature(read_json(semantic_path)) != _semantic_execution_signature(semantic):
                    continue
            except Exception:
                continue
        else:
            try:
                old = read_json(request)
            except Exception:
                continue
            if _recovery_signature(old) != wanted:
                continue
        found.append(attempt)
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def _decode_json_document(raw: str, *, diffusion_thought: bool = False) -> Any:
    """Unwrap a complete JSON fence and the vLLM diffusion thought marker."""
    clean = raw.strip()
    if diffusion_thought:
        clean = re.sub(r"\Athought[ \t]*\r?\n", "", clean, count=1, flags=re.I)
    fence = re.fullmatch(r"```(?:json)?[ \t]*\r?\n?([\s\S]*?)\s*```", clean, flags=re.I)
    if fence:
        clean = fence.group(1).strip()
    return json.loads(clean)


class Runner:
    def __init__(self, store: Store, client: Client, settings: dict, ui: Any):
        self.store, self.client, self.settings, self.ui = store, client, settings, ui

    def fingerprint(self, pass_no: int, inputs: dict) -> str:
        prompt = (self.store.root / "prompts" / f"pass{pass_no}.txt").read_text(encoding="utf-8")
        schema = response_schema(pass_no, inputs)
        return digest({"prompt": prompt, "inputs": inputs, "schema": schema})

    def run(self, pass_no: int, key: str, inputs: dict, *,
            unit_context: InferenceUnitContext | None = None,
            force: bool = False, allow_generate: bool = True) -> tuple[dict, str, str]:
        prompt = (self.store.root / "prompts" / f"pass{pass_no}.txt").read_text(encoding="utf-8")
        schema = response_schema(pass_no, inputs)
        base_fingerprint = digest({"prompt": prompt, "inputs": inputs, "schema": schema})
        selected = self.store.get('selected_pass:' + key) if pass_no > 1 else None
        fingerprint = (digest({'base': base_fingerprint, 'rerun': uuid4().hex}) if force else
                       selected['fingerprint'] if isinstance(selected, dict) and
                       selected.get('base_fingerprint') == base_fingerprint else base_fingerprint)
        if not force:
            cached = self.store.job(key, fingerprint)
            if cached:
                validate_result(pass_no, cached["value"], inputs)
                return cached["value"], cached["path"], fingerprint
        if not allow_generate:
            raise PipelineError(f'P{pass_no} has no current saved result for this chunk. Run P{pass_no} first.')
        hold = self.store.get("observability_hold")
        if hold and not self.settings.get("allow_incomplete_observability", False):
            raise PipelineError(
                "New inference is paused because an earlier completed attempt lacked expected usage evidence. "
                f"Inspect {hold.get('attempt')} and explicitly set allow_incomplete_observability=true to continue."
            )
        work = self.store.root / "artifacts" / key / fingerprint[:20]
        work.mkdir(parents=True, exist_ok=True)
        atomic_json(work / "inputs.json", inputs)
        atomic_text(work / "prompt.txt", prompt)
        provider = self.client.for_pass(pass_no) if hasattr(self.client, "for_pass") else self.client
        # Before generating again, recover a completed compatible response from
        # an older fingerprint if possible. This is especially useful after a
        # validation-only failure in a previous program version.
        # Codex request.json contains an app-server transport plan rather than
        # the value returned by body(), so its legacy fallback comparison was
        # never useful. Avoid running the new codec before an attempt evidence
        # boundary exists; canonical request.semantic.json remains authoritative.
        if getattr(provider, "provider", "llamacpp") == "codex" and pass_no == 1:
            recovery_body = {}
        else:
            recovery_body = provider.body(prompt, inputs, schema, pass_no)
        base_semantic = SemanticRequest(
            task_key=key, task_fingerprint=fingerprint, pass_no=pass_no, attempt_no=0,
            trusted_instructions=prompt, input_payload=inputs, output_schema=schema,
            schema_version=1, profile=getattr(provider, "profile_name", "local"),
            provider=getattr(provider, "provider", "llamacpp"), requested_model=getattr(provider, "model", None),
            reasoning_effort=getattr(provider, "resolved_profile", {}).get("reasoning_effort"),
            planning_output_reserve=int(getattr(provider, "resolved_profile", {}).get(
                "planning_output_reserve", self.settings["passes"][str(pass_no)]["max_tokens"])),
            enforced_output_cap=getattr(provider, "resolved_profile", {}).get("max_output_tokens"),
            timeout_seconds=float(getattr(provider, "timeout", self.settings.get("request_timeout", 1200))),
            resolved_profile=getattr(provider, "resolved_profile", {}),
        ).as_dict()
        key_root = self.store.root / "artifacts" / key
        for attempt in ([] if force else _compatible_completed_attempts(key_root, recovery_body, base_semantic)):
            try:
                raw = (attempt / "answer.txt").read_text(encoding="utf-8").strip()
                meta = read_json(attempt / "response_meta.json")
                decoded = _decode_json_document(raw, diffusion_thought=getattr(provider, "provider", None) == "vllm"
                                                and getattr(provider, "diffusion", False))
                value = _decode_transport_result(decoded, meta.get("wire_format", "canonical"), inputs)
                value, repairs = conservative_repair(pass_no, value, inputs)
                validate_result(pass_no, value, inputs)
            except (OSError, json.JSONDecodeError, jsonschema.ValidationError, PipelineError):
                continue
            path = work / "result.json"
            atomic_json(path, value)
            recovery = {
                "source_attempt": str(attempt.relative_to(self.store.root)),
                "repairs": repairs,
            }
            atomic_json(work / "recovery.json", recovery)
            self.store.save_job(key, fingerprint, path, {**meta, "recovered_from": recovery["source_attempt"],
                                                        "validation_repairs": repairs,
                                                        "settings": self.settings["passes"][str(pass_no)]})
            self.ui.emit(ProgressEvent(kind="pass_recovered", values={
                "pass_no": pass_no, "task_key": key, "model_called": False,
            }))
            if repairs:
                self.ui.emit(ProgressEvent(kind="recovery_repaired", values={
                    "task_key": key, "repair_count": len(repairs),
                    "recovery_path": str(work / "recovery.json"),
                }))
            return value, str(path.relative_to(self.store.root)), fingerprint
        last_error = ""
        attempts = max(1, int(self.settings.get("json_retries", 1)) + 1)
        for retry in range(attempts):
            payload = copy.deepcopy(inputs)
            if retry:
                payload["VALIDATION_ERROR"] = last_error[:1800]
                if pass_no in (2, 4) and "source_span is not an exact quote" in last_error:
                    payload["RETRY_INSTRUCTION"] = (
                        "The cited source_span failed validation. For each issue or correction, copy one contiguous "
                        "substring verbatim from the SOURCE_SENTENCES text with the same sid, including any italic "
                        "markup and punctuation. Do not paraphrase, omit words, or join separate fragments. "
                        "Return one complete valid JSON object."
                    )
                    if pass_no == 4:
                        payload["RETRY_INSTRUCTION"] += (
                            " Each nonempty draft_span must also be a contiguous quote from the POLISH_DRAFT "
                            "translation with the same block_id."
                        )
                elif pass_no == 4 and "draft_span is not found in the specified draft block" in last_error:
                    payload["RETRY_INSTRUCTION"] = (
                        "The cited draft_span failed validation. Copy a contiguous substring verbatim from the "
                        "POLISH_DRAFT translation with the same block_id, or use an empty draft_span if no draft "
                        "quote applies. Keep source_span an exact quote from SOURCE_SENTENCES with the same sid. "
                        "Return one complete valid JSON object."
                    )
                elif pass_no in (3, 5) and "ID coverage mismatch" in last_error:
                    block_ids = [block["id"] for block in inputs["SOURCE_BLOCKS"]]
                    payload["RETRY_INSTRUCTION"] = (
                        f"The previous response omitted source blocks. Return exactly {len(block_ids)} "
                        "translations, one for every SOURCE_BLOCKS id in the same order. "
                        f"Start with {block_ids[0]} and finish with {block_ids[-1]}. "
                        "Do not stop after a partial list. Return one complete valid JSON object."
                    )
                else:
                    payload["RETRY_INSTRUCTION"] = "The previous response failed structural validation. Correct this problem and return one complete valid JSON object. Evidence may cite ONLY IDs from ALLOWED_EVIDENCE_IDS; never invent or reuse IDs from another section."
                if pass_no == 1:
                    payload["ALLOWED_EVIDENCE_IDS"] = [b["id"] for b in inputs["SOURCE_BLOCKS"]]
            number = len(list(work.glob("attempt_*"))) + 1
            attempt = work / f"attempt_{number:03d}"
            retry_additions = {name: payload[name] for name in _RECOVERY_TRANSIENT_INPUT_KEYS if name in payload}
            semantic = SemanticRequest(
                task_key=key, task_fingerprint=fingerprint, pass_no=pass_no, attempt_no=number,
                trusted_instructions=prompt, input_payload=payload, output_schema=schema,
                schema_version=1, profile=getattr(provider, "profile_name", "local"),
                provider=getattr(provider, "provider", "llamacpp"), requested_model=getattr(provider, "model", None),
                reasoning_effort=getattr(provider, "resolved_profile", {}).get("reasoning_effort"),
                planning_output_reserve=int(getattr(provider, "resolved_profile", {}).get(
                    "planning_output_reserve", self.settings["passes"][str(pass_no)]["max_tokens"])),
                enforced_output_cap=getattr(provider, "resolved_profile", {}).get("max_output_tokens"),
                timeout_seconds=float(getattr(provider, "timeout", self.settings.get("request_timeout", 1200))),
                resolved_profile=getattr(provider, "resolved_profile", {}), retry_additions=retry_additions,
            )
            unit_identity = unit_context.as_dict() if unit_context is not None else {}
            recorder = AttemptRecorder(attempt, {
                "project": str(self.store.root), "command": "pipeline", "pass": pass_no,
                "pass_no": pass_no,
                "task_key": key, "task_fingerprint": fingerprint, "attempt_id": f"{fingerprint[:20]}-{number:03d}",
                "attempt_number": number, "provider": semantic.provider, "profile": semantic.profile,
                "requested_model": semantic.requested_model, **unit_identity,
            })
            recorder.semantic(semantic.as_dict(), schema)
            catalog, catalog_path = load_catalog(self.store.root)
            recorder.pricing(pricing_snapshot(catalog, catalog_path, semantic.provider, semantic.requested_model))
            try:
                body = provider.body(prompt, payload, schema, pass_no)
                # llama.cpp discovery is required for a null model/context and
                # is recorded inside the already-created attempt boundary.
                if getattr(provider, "provider", "llamacpp") in {"llamacpp", "vllm"} and not getattr(provider, "identity", {}).get("id"):
                    recorder.event("outbound", "provider_discovery", {"provider": provider.provider, "base": getattr(provider, "base", None)})
                    identity = provider.discover()
                    recorder.event("inbound", "provider_discovery", identity)
                    body = provider.body(prompt, payload, schema, pass_no)
                body = _vary_local_sampling(provider, body, number)
                input_count = provider.preflight(body, recorder)
            except BaseException as exc:
                recorder.finish(generation="not_submitted", validation="not_run",
                                metadata={"provider": semantic.provider, "status": "preflight_failed", "usage_status": "unavailable"},
                                error={"type": type(exc).__name__, "message": str(exc)})
                raise
            measurement = preflight_measurement(provider, input_count)
            recorder.preflight(measurement)
            self.ui.emit(ProgressEvent(kind="provider_waiting", values={
                "pass_no": pass_no, "task_key": key, "attempt_number": number,
                "chapter_id": unit_identity.get("chapter_id"),
                "unit_id": unit_identity.get("unit_id"),
                "chunk_id": unit_identity.get("chunk_id"),
                "analysis_unit_id": unit_identity.get("analysis_unit_id"),
                "unit_index": unit_identity.get("unit_index"),
                "provider": semantic.provider, "profile": semantic.profile,
                "requested_model": semantic.requested_model,
                "input_value": measurement["value"], "input_unit": measurement["unit"],
                "input_quality": measurement["quality"], "input_method": measurement["method"],
            }))
            # A failed HTTP or length-limited request is NOT blindly retried; first
            # inspect/save its partial output. Only invalid completed JSON is retried.
            try:
                raw, meta = provider.generate(body, attempt, recorder)
            except BaseException as exc:
                usage_status = "unknown"
                if (attempt / "usage.json").is_file():
                    try:
                        usage_status = read_json(attempt / "usage.json").get("status", "unknown")
                    except Exception:
                        pass
                recorder.finish(generation="failed", validation="not_run",
                                metadata={"provider": semantic.provider, "status": "failed", "usage_status": usage_status,
                                          "partial_answer": (attempt / "answer.partial.txt").exists()},
                                error={"type": type(exc).__name__, "message": str(exc)},
                                evidence_complete=(not isinstance(exc, (OSError, EvidenceError))
                                                   and "rollout" not in str(exc).casefold()))
                raise
            if (attempt / "usage.json").is_file():
                snapshot = read_json(attempt / "pricing.json")
                recorder.pricing(apply_estimate(snapshot, read_json(attempt / "usage.json")))
            try:
                value = _decode_transport_result(
                    _decode_json_document(raw, diffusion_thought=getattr(provider, "provider", None) == "vllm"
                                          and getattr(provider, "diffusion", False)),
                    meta.get("wire_format", "canonical"), inputs, body.get("codec_context")
                )
                if meta.get("wire_format") == "compact-v1":
                    recorder.decoded_canonical(value)
                value, repairs = conservative_repair(pass_no, value, inputs)
                if repairs:
                    atomic_json(attempt / "validation_repairs.json", repairs)
                validate_result(pass_no, value, inputs)
            except EvidenceError as exc:
                # Evidence durability failures are not model-output failures.
                # Never spend another model turn because a local artifact could
                # not be written. Finalization is best-effort because the same
                # storage failure may also prevent response_meta.json updates.
                try:
                    recorder.finish(generation="completed", validation="not_run", metadata={**meta,
                                    "status": "evidence_failed",
                                    "usage_status": meta.get("usage_status", "unknown")},
                                    error={"type": type(exc).__name__, "message": str(exc)},
                                    evidence_complete=False)
                except EvidenceError:
                    pass
                raise
            except (json.JSONDecodeError, jsonschema.ValidationError, PipelineError) as exc:
                last_error = str(exc)
                atomic_text(attempt / "validation_error.txt", last_error + "\n")
                recorder.finish(generation="completed", validation="failed", metadata={**meta,
                                "validation_error": last_error, "usage_status": meta.get("usage_status", "unknown")},
                                error={"type": type(exc).__name__, "message": last_error})
                if retry + 1 == attempts:
                    raise PipelineError(f"P{pass_no} failed validation. Artifacts: {attempt}\n{last_error[:1400]}") from exc
                continue
            path = work / "result.json"
            atomic_json(path, value)
            if pass_no in (3, 5):
                atomic_text(work / "result.txt", "\n\n".join(b["text"] for b in value["translations"]) + "\n")
            measurement_meta = preflight_metadata(measurement)
            recorder.finish(generation="completed", validation="passed", metadata={**meta, **measurement_meta,
                            "execution_signature": digest(_semantic_execution_signature(semantic.as_dict()))})
            self.store.save_job(key, fingerprint, path, {**meta, **measurement_meta,
                                                        "settings": self.settings["passes"][str(pass_no)]})
            recorder.mark_accepted()
            if meta.get("usage_status") == "unavailable":
                with self.store.db:
                    self.store.set("observability_hold", {"attempt": str(attempt.relative_to(self.store.root)),
                                                          "reason": "Provider completed without expected usage telemetry."})
            return value, str(path.relative_to(self.store.root)), fingerprint
        raise PipelineError("No completed result.")


def source_blocks(blocks: list[dict]) -> list[dict]:
    result = []
    for b in blocks:
        item = {"id": b["id"], "kind": b["kind"], "text": b["text"]}
        if b.get("scene_id"):
            item["scene_id"] = b["scene_id"]
        if b.get("scene_start"):
            item["scene_start"] = True
        result.append(item)
    return result


def analysis_plan(store: Store, book: dict, client: Client, settings: dict) -> list[dict]:
    saved = store.root / "analysis_plan.json"
    if saved.exists():
        return read_json(saved)
    source_budget = client.context - settings["memory_tokens"] - settings["passes"]["1"]["max_tokens"] - 6000
    source_budget = int(source_budget * 0.8)
    if settings.get("analysis_source_limit", 0):
        source_budget = min(source_budget, settings["analysis_source_limit"])
    if source_budget < 1024:
        raise PipelineError("Too little context for Pass 1 with configured reserves. Reduce budgets or increase server context.")
    units = []
    for chapter in book["chapters"]:
        # Import already tokenized the complete natural section. Reuse that count.
        # This avoids thousands of synchronous /tokenize calls over individual
        # paragraphs before P1 can even start.
        tokenizer_matches = (chapter.get("source_tokens_quality") != "estimated" and
                             chapter.get("source_tokens_tokenizer") == getattr(client, "tokenizer_identity", None))
        cached_tokens = int(chapter.get("source_tokens") or 0) if tokenizer_matches else 0
        if cached_tokens and cached_tokens <= source_budget:
            groups = [chapter["blocks"]]
        else:
            # Compatibility/fallback for older manifests or exceptionally large
            # sections. Only then do the more expensive block-level tokenization.
            blocks = []
            for block in chapter["blocks"]:
                if client.count(block["text"]) <= source_budget:
                    blocks.append(block)
                    continue
                for idx, (start, end) in enumerate(split_long(block["text"], client.count, source_budget), 1):
                    piece = copy.deepcopy(block)
                    piece.update(parent_id=block["id"], start=start, end=end,
                                 id=f"{block['id']}.a{idx}", text=block["text"][start:end])
                    blocks.append(piece)
            groups = pack_blocks(blocks, client.count, source_budget, source_budget)
        for idx, group in enumerate(groups, 1):
            units.append({"id": f"{chapter['id']}_a{idx:03d}", "chapter_id": chapter["id"],
                          "chapter_number": chapter["number"], "part": idx, "parts": len(groups), "blocks": group})
    atomic_json(saved, units)
    return units


def analyze(store: Store, book: dict, client: Client, settings: dict, ui: Any):
    """Compatibility entry point; orchestration lives in the application layer."""
    from .application.pipeline import execute_analyze
    return execute_analyze(store, book, client, settings, ui)


def tail(text: str, client: Client, maximum: int) -> str:
    if maximum <= 0:
        return ""
    if client.count(text) <= maximum:
        return text
    lo, hi, best = 0, len(text), len(text)
    while lo <= hi:
        mid = (lo+hi)//2
        if client.count(text[mid:]) <= maximum:
            best, hi = mid, mid-1
        else:
            lo = mid+1
    cut = text.find(" ", best)
    return text[cut+1:] if cut >= 0 else text[best:]


def previous_context(store: Store, book: dict, chunk: dict, client: Client, maximum: int) -> dict:
    chapters = {c["id"]: c for c in book["chapters"]}
    current_ch = chapters[chunk["chapter_id"]]
    candidates = []
    for prior in book["chunks"]:
        if prior["number"] >= chunk["number"]:
            break
        same = prior["chapter_id"] == chunk["chapter_id"]
        same_thread = current_ch.get("thread_id") and current_ch["thread_id"] == chapters[prior["chapter_id"]].get("thread_id")
        if same or same_thread:
            candidates.append(prior)
    for prior in reversed(candidates):
        state = store.chunk(prior["id"])
        if state.get("final_path"):
            result = store.checked_result(state["final_path"])
            return {"source_chunk_id": prior["id"],
                    "english": tail("\n\n".join(b["text"] for b in prior["blocks"]), client, maximum),
                    "polish": tail("\n\n".join(b["text"] for b in result["translations"]), client, maximum)}
    return {"source_chunk_id": None, "english": "", "polish": ""}


def translate(store: Store, book: dict, client: Client, settings: dict, ui: Any, limit: int):
    """Compatibility entry point; orchestration lives in the application layer."""
    from .application.pipeline import execute_translate
    return execute_translate(store, book, client, settings, ui, limit)


def export_text(store: Store, book: dict):
    """Render only a contiguous readable prefix. Keep old finals for stale chunks."""
    prefix, chapter_parts, status = [], {}, []
    stopped = False
    last_parent = None
    for chunk in book["chunks"]:
        state = store.chunk(chunk["id"])
        status.append({"chunk": chunk["id"], "status": state["status"]})
        if not state.get("final_path"):
            stopped = True
        if stopped:
            continue
        path = store.root / state["final_path"]
        if not path.exists():
            raise PipelineError(f"Missing final artifact: {path}")
        final = store.checked_result(state["final_path"])
        mapping = {r["id"]: r["text"].strip() for r in final["translations"]}
        parts = chapter_parts.setdefault(chunk["chapter_id"], [])
        for block in chunk["blocks"]:
            value = mapping[block["id"]]
            same_parent = last_parent == block["parent_id"]
            sep = " " if same_parent else "\n\n"
            if not prefix:
                sep = ""
            prefix.append(sep + value)
            parts.append((" " if same_parent else "\n\n") + value)
            last_parent = block["parent_id"]
    atomic_text(store.root / "translation.txt", "".join(prefix).strip() + ("\n" if prefix else ""))
    for cid, parts in chapter_parts.items():
        atomic_text(store.root / "translated_chapters" / f"{cid}.txt", "".join(parts).strip() + "\n")
    atomic_json(store.root / "translation.status.json", {"chunks": status,
                 "note": "Output is a contiguous completed prefix. Old translations of stale chunks remain visible until regenerated."})
