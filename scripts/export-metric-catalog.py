#!/usr/bin/env python3
"""Export the canonical metric registry as deterministic documentation JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hoardarr.telemetry.catalog import catalog_document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": 1,
        "source": "hoardarr.telemetry.catalog",
        "metrics": catalog_document(),
    }
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
