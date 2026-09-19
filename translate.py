#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["beautifulsoup4>=4.12,<5", "rich>=13,<15", "httpx>=0.27,<1", "jsonschema>=4.21,<5", "defusedxml>=0.7,<1"]
# ///
"""Run with: uv run translate.py --help"""
from bookpipe.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
