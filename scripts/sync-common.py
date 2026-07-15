#!/usr/bin/env python3
"""Regenerates each service's app/logging_middleware.py from the single canonical
source at common/logging_middleware.py.

Usage:
    python3 scripts/sync-common.py          # regenerate all 5 copies
    python3 scripts/sync-common.py --check  # exit 1 if any copy is out of sync, no changes made

Run this after editing common/logging_middleware.py. See that file's docstring for
why this script exists instead of a shared pip package or Docker-level copy: each
service stays independently buildable/deployable, with the file still physically
present where each service's local venv + pytest workflow expects it.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL = REPO_ROOT / "common" / "logging_middleware.py"
SERVICES = [
    "ticket-service",
    "equipment-history-service",
    "knowledge-service",
    "recommendation-service",
    "agent-orchestrator",
]

HEADER = (
    '"""GENERATED FILE — do not edit directly.\n'
    "\n"
    "Source of truth: common/logging_middleware.py at the repo root. Edit that file,\n"
    "then run `python3 scripts/sync-common.py` to regenerate this copy (and the other\n"
    "4 services' copies) from it.\n"
    '"""\n\n'
)


def _generated_body() -> str:
    text = CANONICAL.read_text()
    # Strip the canonical file's own (longer) module docstring — generated copies get
    # the short HEADER above instead, pointing back at the one real source of truth
    # rather than repeating the full explanation 5 times.
    marker = '"""\n\n'
    idx = text.index(marker) + len(marker)
    return HEADER + text[idx:]


def main() -> int:
    check_only = "--check" in sys.argv
    body = _generated_body()
    out_of_sync = []
    for service in SERVICES:
        target = REPO_ROOT / "services" / service / "app" / "logging_middleware.py"
        current = target.read_text() if target.exists() else None
        if current != body:
            out_of_sync.append(target)
            if not check_only:
                target.write_text(body)

    if check_only:
        if out_of_sync:
            print("Out of sync with common/logging_middleware.py:")
            for path in out_of_sync:
                print(f"  {path.relative_to(REPO_ROOT)}")
            return 1
        print("All 5 copies match common/logging_middleware.py.")
        return 0

    if out_of_sync:
        print(f"Regenerated {len(out_of_sync)} file(s) from common/logging_middleware.py:")
        for path in out_of_sync:
            print(f"  {path.relative_to(REPO_ROOT)}")
    else:
        print("All 5 copies already match common/logging_middleware.py — nothing to do.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
