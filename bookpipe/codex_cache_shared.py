"""Opt-in P1-P5 physical prefixes. No session execution or cache routing here."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

from . import codex_cache_v2 as v2, p1_compact as p1
from .codex_cache import RETRY_FIELDS, STAGE_FIELDS, CacheLayout, encode_value
from .util import PipelineError, digest

WIRE_FORMAT = "cache-shared-v1"
MAP_VERSION = 1
COMMON_ORDER = ("HISTORICAL_LOOKUP", "APPROVED_LEXICON", "OBSERVATIONS", "PREVIOUS_CONTEXT", "CHUNK_ID")

# Reuse the proven record forms; no per-request bounds or canonical schema.
TRANSPORT_SCHEMA = v2._object({
    "p": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
    "a": copy.deepcopy(p1.COMPACT_SCHEMA["properties"]["t"]),
    "o": copy.deepcopy(p1.COMPACT_SCHEMA["properties"]["o"]),
    **{k: copy.deepcopy(v2.TRANSPORT_SCHEMA["properties"][k]) for k in ("c", "i", "e", "t")},
})


@dataclass(frozen=True)
class CodecContext(v2.CodecContext):
    """Separate decoder domains; retry diagnostics never enter the source map."""

    map_version: int = MAP_VERSION

    def as_dict(self):
        return {**super().as_dict(), "wire_format": WIRE_FORMAT}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, dict) or value.get("wire_format") != WIRE_FORMAT
                or type(value.get("map_version")) is not int or value["map_version"] != MAP_VERSION):
            raise PipelineError("Unsupported cache-shared-v1 codec/map version; preserve original attempt evidence.")
        old = v2.CodecContext.from_dict({**value, "wire_format": v2.WIRE_FORMAT})
        return cls(**vars(old))


@dataclass(frozen=True)
class SharedLayout:
    source_prefix: str
    translation_common_prefix: str | None
    draft_prefix: str | None
    dynamic_suffix: str

    @property
    def common_prefix(self):
        return self.translation_common_prefix or self.source_prefix

    @property
    def text(self):
        return self.common_prefix + self.dynamic_suffix


def _field(name, value):
    return encode_value(name) + ":" + encode_value(value) + ","


def encode_source(blocks: list[dict]) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """The same lossless source-only prefix for all passes; maps stay local."""
    if not isinstance(blocks, list) or not blocks:
        raise PipelineError("cache-shared-v1 requires nonempty SOURCE_BLOCKS.")
    kinds, scenes, bids, rows = [], [], [], []
    for i, block in enumerate(blocks):
        v2._record(block, ("id", "kind", "scene_id", "scene_start", "text"), ("id", "kind", "text"), "source block")
        if any(not isinstance(block[k], str) for k in ("id", "kind", "text")):
            raise PipelineError("cache-shared-v1 source id/kind/text must be strings.")
        if "scene_id" in block and (not isinstance(block["scene_id"], str) or not block["scene_id"]):
            raise PipelineError("cache-shared-v1 scene_id must be a nonempty string.")
        if "scene_start" in block and type(block["scene_start"]) is not bool:
            raise PipelineError("cache-shared-v1 scene_start must be boolean.")
        if block["kind"] not in kinds:
            kinds.append(block["kind"])
        if "scene_id" in block and block["scene_id"] not in scenes:
            scenes.append(block["scene_id"])
        bids.append(block["id"])
        rows.append([i, kinds.index(block["kind"]), scenes.index(block["scene_id"]) if "scene_id" in block else -1,
                     (2 if block["scene_start"] else 1) if "scene_start" in block else 0, block["text"]])
    v2._strings(bids, "block")
    prefix = '{"CACHE_SHARED_V1":1,' + _field("SOURCE_BLOCKS", rows) + _field("SOURCE_LOOKUP", {"k": kinds})
    return prefix, tuple(bids), tuple(scenes)


def context_for(inputs: dict, pass_no: int) -> CodecContext:
    if type(pass_no) is not int or pass_no not in (1, 2, 3, 4, 5):
        raise PipelineError("cache-shared-v1 supports P1-P5 only.")
    if pass_no != 1:
        return CodecContext(**vars(v2.context_for(inputs, pass_no)))
    # Preserve the existing memory contract/validation, with the new source map.
    p1.encode_input(inputs)
    _, bids, scenes = encode_source(inputs["SOURCE_BLOCKS"])
    return CodecContext(bids, scenes, (), (), (),
                        retry_input={k: copy.deepcopy(inputs[k]) for k in RETRY_FIELDS if k in inputs})


def _extend_output(value):
    return {"p": value["p"], "a": [], "o": [], **{k: value[k] for k in ("c", "i", "e", "t")}}


def encode_output(value: dict, pass_no: int, ctx: CodecContext) -> dict:
    """Encode canonical accepted outputs, never a pre-repair raw response."""
    if pass_no != 1:
        return _extend_output(v2.encode_output(value, pass_no, ctx))
    v2._record(value, ("terms", "observations"), ("terms", "observations"), "P1 canonical artifact")
    from .schemas import P1
    jsonschema.Draft202012Validator(P1).validate(value)
    def evidence(refs):
        if any(ref not in ctx.block_ids for ref in refs):
            raise PipelineError("cache-shared-v1 unknown P1 evidence reference.")
        return [ctx.block_ids.index(ref) for ref in refs]
    result = {"p": 1, "a": [{
        "s": t["source"], "a": copy.deepcopy(t["aliases"]), "c": v2._encode_code(t["category"], p1.CATEGORIES, "category"),
        "m": t["meaning"], "q": v2._encode_code(t["confidence"], p1.CONFIDENCES, "confidence"),
        "p": [{"t": c["text"], "r": c["reason"]} for c in t["candidates"]], "e": evidence(t["evidence"]),
    } for t in value["terms"]], "o": [{
        "a": copy.deepcopy(o["about"]), "k": v2._encode_code(o["kind"], p1.OBSERVATION_KINDS, "kind"),
        "s": o["statement"], "q": v2._encode_code(o["confidence"], p1.CONFIDENCES, "confidence"), "e": evidence(o["evidence"]),
    } for o in value["observations"]], "c": [], "i": [], "e": [], "t": []}
    decode_output(result, pass_no, ctx)
    return result


def decode_output(value: Any, expected_pass: int, ctx: CodecContext) -> dict:
    jsonschema.Draft202012Validator(TRANSPORT_SCHEMA).validate(value)
    if type(value["p"]) is not int or value["p"] != expected_pass:
        raise PipelineError("cache-shared-v1 reported pass differs from application's expected pass.")
    active = {1: {"a", "o"}, 2: {"c", "i"}, 3: {"t"}, 4: {"c", "e"}, 5: {"t"}}[expected_pass]
    if any(value[k] for k in {"a", "o", "c", "i", "e", "t"} - active):
        raise PipelineError("cache-shared-v1 nonempty inactive output arrays.")
    if expected_pass != 1:
        return v2.decode_output({k: value[k] for k in ("p", "c", "i", "e", "t")}, expected_pass, ctx)
    # JSON Schema regards 1.0 as integer; compact codes require actual integers.
    for term in value["a"]:
        v2._code(term["c"], p1.CATEGORIES, "category")
        v2._code(term["q"], p1.CONFIDENCES, "confidence")
    for observation in value["o"]:
        v2._code(observation["k"], p1.OBSERVATION_KINDS, "kind")
        v2._code(observation["q"], p1.CONFIDENCES, "confidence")
    return p1.decode_output({"t": value["a"], "o": value["o"]}, list(ctx.block_ids))


def retry_feedback(error: str, ctx: CodecContext, pass_no: int) -> dict:
    if pass_no != 1:
        feedback = v2.retry_feedback(error, ctx, pass_no)
        feedback["RETRY_INSTRUCTION"] = feedback["RETRY_INSTRUCTION"].replace("p/c/i/e/t", "p/a/o/c/i/e/t")
        return feedback
    # Canonical IDs/quoted source fragments remain in local diagnostics only.
    problem = "Correct compact fields and codes; ground all terms, aliases and observations in the supplied source."
    if "evidence" in error or "index" in error:
        problem = "Use only local integer block indices in evidence e; never cite another unit."
    elif "candidate" in error:
        problem = "Each term requires one to three nonempty Polish candidates."
    return {"VALIDATION_ERROR": problem, "RETRY_INSTRUCTION":
            f"Return the complete P1 object p/a/o/c/i/e/t with p=1 and c/i/e/t empty. Evidence e uses block indices "
            f"0 through {len(ctx.block_ids)-1}. Never emit canonical identifier strings in evidence."}


def encode_input(inputs: dict, pass_no: int) -> tuple[SharedLayout, CodecContext]:
    ctx = context_for(inputs, pass_no)
    source, bids, scenes = encode_source(inputs["SOURCE_BLOCKS"])
    if (bids, scenes) != (ctx.block_ids, ctx.scene_ids):
        raise PipelineError("cache-shared-v1 inconsistent source map.")
    if pass_no == 1:
        payload, _, _ = p1.encode_input(inputs)
        common = None
        suffix = _field("EXISTING_MEMORY", payload["EXISTING_MEMORY"]) + _field("SECTION_ID", inputs["SECTION_ID"])
        draft = None
    else:
        # Existing v2 owns memory, sentence and artifact transformations/guards.
        old, _ = v2.encode_input(inputs, pass_no)
        payload = v2.parse_output(old.text)
        common = source + _field("HISTORICAL_LOOKUP", payload["SOURCE_LOOKUP"]["h"])
        common += "".join(_field(k, payload[k]) for k in COMMON_ORDER[1:])
        stage = {k: payload[k] if k == "SOURCE_SENTENCES" else _extend_output(payload[k]) for k in STAGE_FIELDS[pass_no]}
        suffix = "".join(_field(k, stage[k]) for k in STAGE_FIELDS[pass_no])
        draft = common + _field("POLISH_DRAFT", stage["POLISH_DRAFT"]) if pass_no in (4, 5) else None
    if any(k in inputs for k in RETRY_FIELDS):
        suffix += "".join(_field(k, v) for k, v in retry_feedback(str(inputs.get("VALIDATION_ERROR", "")), ctx, pass_no).items())
    suffix += '"ACTIVE_PASS":' + str(pass_no) + '}'
    return SharedLayout(source, common, draft, suffix), ctx


def decode_input(layout: SharedLayout, ctx: CodecContext) -> dict:
    """Offline inverse using the versioned local maps and original retry data."""
    wire = v2.parse_output(layout.text)
    n = wire["ACTIVE_PASS"]
    if n != 1:
        legacy = {k: v for k, v in wire.items() if k not in ("CACHE_SHARED_V1", "HISTORICAL_LOOKUP")}
        legacy["SOURCE_LOOKUP"] = {**legacy["SOURCE_LOOKUP"], "h": wire["HISTORICAL_LOOKUP"]}
        for k in STAGE_FIELDS[n]:
            if k != "SOURCE_SENTENCES":
                legacy[k] = {f: legacy[k][f] for f in ("p", "c", "i", "e", "t")}
        return v2.decode_input(CacheLayout(encode_value(legacy), "", None), ctx)
    blocks = []
    for i, k, s, f, text in wire["SOURCE_BLOCKS"]:
        block = {"id": v2._index(i, ctx.block_ids, "block"), "kind": v2._index(k, wire["SOURCE_LOOKUP"]["k"], "kind"), "text": text}
        if s != -1:
            block["scene_id"] = v2._index(s, ctx.scene_ids, "scene")
        if f != 0:
            block["scene_start"] = v2._code(f, {1: False, 2: True}, "scene-start")
        blocks.append(block)
    memory = wire["EXISTING_MEMORY"]
    result = {"SOURCE_BLOCKS": blocks, "SECTION_ID": wire["SECTION_ID"], "EXISTING_MEMORY": {
        "catalogue": [dict(zip(("id", "source", "aliases"), row, strict=True)) for row in memory["c"]],
        "matched": [dict(zip(("id", "source", "aliases", "candidates", "chosen", "meaning_notes"), row, strict=True)) for row in memory["m"]],
        "catalogue_incomplete": v2._code(memory["i"], {0: False, 1: True}, "catalogue-incomplete"),
    }}
    result.update(copy.deepcopy(ctx.retry_input))
    return result


def _shared_legend() -> str:
    value = v2.LEGEND
    for old, new in (
        ("Compact cache-v2 JSON contract (P2-P5 only).", "IntelliTex cache-shared-v1 compact JSON contract (P1-P5)."),
        ("ALL fields p,c,i,e,t", "ALL fields p,a,o,c,i,e,t"),
        ("P2 uses c,i;", "P1 uses a,o; P2 uses c,i;"),
        ("this same p/c/i/e/t encoding", "this same p/a/o/c/i/e/t encoding"),
        ("SOURCE_LOOKUP.h historical identifiers", "HISTORICAL_LOOKUP historical identifiers"),
    ):
        if value.count(old) != 1:
            raise PipelineError(f"cache-shared-v1 cannot adapt the cache-v2 legend clause: {old!r}")
        value = value.replace(old, new, 1)
    return value


LEGEND = _shared_legend()
LEGEND += """P1 a contains term records {s:source,a:aliases,c:category,m:meaning,q:confidence,p:candidates,e:evidence}.
Candidates are {t:text,r:reason}. P1 o contains observations {a:about,k:kind,s:statement,q:confidence,e:evidence}.
P1 evidence e is an array of local block indices, not the translation-memory [domain,index] representation.
P1 EXISTING_MEMORY: c catalogue rows [id,source,aliases], m matched rows
[id,source,aliases,candidates,chosen,meaning_notes], i catalogue-incomplete flag 0/1.
P1 uses only SOURCE_BLOCKS, EXISTING_MEMORY and SECTION_ID; translation-only data is absent.
"""


def _p1_prompt(prompt: str) -> str:
    # Use compact-v1's exact canonical contract recognition, then adapt only its
    # known transport clauses to shared source rows and the superset root.
    value = p1.compact_instructions(prompt)
    old = ("BLOCK_KINDS is a lookup array; each SOURCE_BLOCKS row is [i,k,s,f,t]: local evidence index, BLOCK_KINDS index, "
           "local scene index, scene-start flag 0/1, and original text.")
    if value.count(old) != 1 or value.count("Root: t=terms, o=observations.") != 1:
        raise PipelineError("cache-shared-v1 cannot recognize adapted P1 compact clauses; select compact-v1/canonical.")
    value = value.replace(old, "SOURCE_LOOKUP.k is the kind lookup; SOURCE_BLOCKS rows [b,k,s,f,t] use local block, kind and "
                          "scene indices (-1 absent), scene-start presence/value code 0 absent/1 false/2 true, and unchanged text.", 1)
    # The existing adapter generated this legend, not project-specific prose.
    # Keep the code tables once in our common legend rather than duplicating it.
    start = "Compact JSON contract:\nRoot: t=terms, o=observations."
    end = "never canonical block-ID strings."
    if value.count(start) != 1 or value.count(end) != 1:
        raise PipelineError("cache-shared-v1 cannot recognize the P1 compact legend; select compact-v1/canonical.")
    first, last = value.index(start), value.index(end) + len(end)
    value = value[:first] + "Shared compact P1 contract: p=1, a=terms, o=observations; c,i,e,t are empty arrays. Use the common field/code legend." + value[last:]
    # compact-v1 recognizes its primary contract. Reject extra conflicting
    # custom contracts here without changing that legacy transport's behavior.
    if re.search(r'\{\s*["\']?[A-Za-z_]\w*["\']?\s*:', value) or re.search(
        r"\b(?:return|output|respond(?: with)?)\s+(?:only\s+|exactly\s+)?(?:a\s+|one\s+)?(?:string|plain[- ]?text|xml|yaml|markdown|csv|canonical\s+(?:json|ids))\b", value, re.I
    ):
        raise PipelineError("cache-shared-v1 unsupported custom P1 output contract; preserve the standard contract and put substantive rules around it, or select canonical.")
    return value


def developer_contract(prompts: dict[int, str]) -> str:
    if set(prompts) != {1, 2, 3, 4, 5} or any(not p.strip() for p in prompts.values()):
        raise PipelineError("cache-shared-v1 requires all five nonempty project pass definitions.")
    try:
        definitions = {"1": _p1_prompt(prompts[1])}
        definitions.update({str(n): v2.compact_prompt(prompts[n], n) for n in range(2, 6)})
    except PipelineError as exc:
        raise PipelineError(f"cache-shared-v1 cannot build the shared contract: {exc}") from exc
    return LEGEND + encode_value(definitions)


def load_developer_contract(project_root: Path, prompt: str, pass_no: int) -> str:
    try:
        prompts = {n: (project_root / "prompts" / f"pass{n}.txt").read_text(encoding="utf-8") for n in range(1, 6)}
    except OSError as exc:
        raise PipelineError(f"Cannot read cache-shared-v1 project pass definitions: {exc}") from exc
    if prompts.get(pass_no) != prompt:
        raise PipelineError("cache-shared-v1 active prompt differs from its project definition.")
    return developer_contract(prompts)


def diagnostics(layout: SharedLayout, developer: str, model, effort, pass_no, inputs, base) -> dict:
    identity = {"cache_layout": WIRE_FORMAT, "codec_map_version": MAP_VERSION,
                "developer_instructions_sha256": digest(developer),
                "transport_output_schema_sha256": digest(encode_value(TRANSPORT_SCHEMA)),
                "base_instructions_sha256": digest(base), "model": model, "effort": effort}
    result = {**identity, "requested_model": model, "requested_effort": effort, "pass_no": pass_no,
              "analysis_unit_id" if pass_no == 1 else "chunk_id": inputs["SECTION_ID" if pass_no == 1 else "CHUNK_ID"],
              "full_input_sha256": digest(layout.text), "full_input_utf8_bytes": len(layout.text.encode("utf-8")),
              "input_utf8_bytes": len(layout.text.encode("utf-8")), "developer_utf8_bytes": len(developer.encode("utf-8")),
              "source_utf8_bytes": sum(len(b["text"].encode("utf-8")) for b in inputs["SOURCE_BLOCKS"]),
              "transport_schema_utf8_bytes": len(encode_value(TRANSPORT_SCHEMA).encode("utf-8")),
              "dynamic_suffix_sha256": digest(layout.dynamic_suffix),
              "cache_prefix_sha256": digest(layout.common_prefix), "cache_prefix_utf8_bytes": len(layout.common_prefix.encode("utf-8")),
              "cache_identity_sha256": digest({**identity, "prefix": digest(layout.common_prefix)})}
    for name, prefix in (("source", layout.source_prefix), ("translation_common", layout.translation_common_prefix), ("draft", layout.draft_prefix)):
        if prefix is not None:
            result.update({f"{name}_prefix_sha256": digest(prefix), f"{name}_prefix_utf8_bytes": len(prefix.encode("utf-8")),
                           f"{name}_cache_identity_sha256": digest({**identity, "prefix": digest(prefix)})})
    return result
