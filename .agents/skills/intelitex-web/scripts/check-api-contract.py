#!/usr/bin/env python3
"""Detect changes to inspected API source files; this is not a runtime API validator."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


def git_blob_sha(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def inspect(repo: Path, baseline_file: Path) -> dict:
    root = repo.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Repository argument must be a directory.")
    baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
    if baseline.get("format_version") != 1 or not baseline.get("source_blobs"):
        raise ValueError("Invalid API source baseline.")
    files = []
    for name, expected in baseline["source_blobs"].items():
        if not isinstance(name, str) or Path(name).is_absolute() or not isinstance(expected, str):
            raise ValueError("Invalid baseline source entry.")
        target = (root / name).resolve()
        if not target.is_relative_to(root):
            raise ValueError("Source baseline path escapes the repository.")
        if not target.is_file():
            actual = None
            state = "missing"
        else:
            actual = git_blob_sha(target.read_bytes())
            state = "matched" if actual == expected else "changed"
        files.append({"path": name, "expected_blob": expected, "actual_blob": actual, "status": state})
    return {"scope": "source_drift_only", "inspected_commit": baseline["inspected_commit"],
            "status": "baseline_matched" if all(f["status"] == "matched" for f in files)
                      else "needs_reaudit", "files": files,
            "note": "This does not prove route behavior, payload compatibility, test results, or complete coverage."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--baseline", type=Path,
                        default=Path(__file__).resolve().parents[1] / "references/api-baseline.json")
    args = parser.parse_args(argv)
    try:
        report = inspect(args.repo, args.baseline)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"scope": "source_drift_only", "status": "error", "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "baseline_matched" else 3


if __name__ == "__main__":
    sys.exit(main())
