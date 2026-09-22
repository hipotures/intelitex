#!/usr/bin/env python3
"""Verify immutable skill source assets without rewriting files or using the network."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

EXPECTED = {
    "reference": (143061, "c50fe95eb761ae332171759cfb2ba95fcc996baaa98d9a1471a570f6f54a26a5"),
    "supplied_contract": (92586, "5cb7d0ed9520517d8e2a6f29239b0d0f217b7e2ea189d748b81f035c4503af91"),
}


def contained_file(root: Path, relative: str) -> Path:
    """Reject manifest paths outside the skill, including escaping symlinks."""
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("Expected a relative asset path.")
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Asset path escapes the skill directory.")
    return target


def verify(skill_root: Path) -> dict:
    root = skill_root.resolve()
    manifest = json.loads((root / "assets/reference-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1:
        raise ValueError("Unsupported reference manifest version.")
    results = []
    for key, (expected_size, expected_hash) in EXPECTED.items():
        record = manifest[key]
        if (record.get("bytes"), record.get("sha256")) != (expected_size, expected_hash):
            raise ValueError(f"Manifest changed the pinned {key} identity; manual review required.")
        raw = contained_file(root, record["path"]).read_bytes()
        actual_hash = hashlib.sha256(raw).hexdigest()
        results.append({"resource": key, "path": record["path"], "bytes": len(raw),
                        "sha256": actual_hash,
                        "matched": len(raw) == expected_size and actual_hash == expected_hash})
    return {"scope": "immutable_reference_integrity", "status": "passed" if all(
        r["matched"] for r in results) else "failed", "resources": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        report = verify(args.skill_root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"scope": "immutable_reference_integrity", "status": "error",
                          "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
