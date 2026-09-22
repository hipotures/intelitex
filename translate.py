#!/usr/bin/env python3
"""Backwards-compatible shim; use ``uv run intelitex`` for new commands."""
from bookpipe.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
