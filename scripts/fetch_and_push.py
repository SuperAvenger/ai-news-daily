#!/usr/bin/env python3
"""Compatibility entrypoint for the resilient AI news pipeline."""

from scripts import fetch_and_push_base as _base

# Re-export the preserved pipeline API so existing tests/imports keep working.
for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)


if __name__ == "__main__":
    from scripts.run_resilient import main as _run_resilient

    _run_resilient()
