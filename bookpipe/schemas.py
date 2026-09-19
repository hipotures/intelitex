"""Small, real JSON Schemas, unlike example objects with pipe-separated values."""
from __future__ import annotations

S = {"type": "string"}

def obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}

def arr(items, **kwargs):
    return {"type": "array", "items": items, **kwargs}

def enum(*values):
    return {"type": "string", "enum": list(values)}

CONF = enum("high", "medium", "low")
CATEGORY = enum("name", "organization", "people", "place", "ship", "status", "technology", "science", "jargon", "other")
CANDIDATE = obj({"text": {"type": "string", "minLength": 1}, "reason": S})
P1 = obj({
    "terms": arr(obj({
        "source": {"type": "string", "minLength": 1}, "aliases": arr(S),
        "category": CATEGORY, "meaning": S, "confidence": CONF,
        "candidates": arr(CANDIDATE, minItems=1, maxItems=3),
        "evidence": arr(S, minItems=1),
    })),
    "observations": arr(obj({
        "about": arr(S, minItems=1), "kind": enum("reference", "gender", "register", "technical", "continuity"),
        "statement": S, "confidence": CONF, "evidence": arr(S, minItems=1),
    })),
})
P2 = obj({
    "checks": arr(obj({"sid": S, "risk": enum("low", "medium", "high")})),
    "issues": arr(obj({
        "sid": S, "source_span": S,
        "type": enum("idiom", "pragmatics", "reference", "terminology", "morphology", "technical", "relation", "style"),
        "meaning": S, "constraint": S, "confidence": CONF,
    })),
})
TRANSLATION = obj({"translations": arr(obj({"id": S, "text": {"type": "string", "minLength": 1}}))})
P4 = obj({
    "checks": arr(obj({"sid": S, "status": enum("ok", "needs_correction")})),
    "corrections": arr(obj({
        "sid": S, "block_id": S, "source_span": S, "draft_span": S,
        "problem": S, "constraint": S,
        "severity": enum("minor", "major", "critical"), "confidence": CONF,
    })),
})
SCHEMAS = {1: P1, 2: P2, 3: TRANSLATION, 4: P4, 5: TRANSLATION}
