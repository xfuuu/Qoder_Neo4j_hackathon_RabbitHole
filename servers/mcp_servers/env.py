"""Config from the environment, falling back to the repo's .env."""

from __future__ import annotations

import os

from .runs import ROOT


def get(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line.startswith(f"{key}="):
                    value = line.split("=", 1)[1].strip().strip("'\"")
    if not value:
        raise RuntimeError(f"{key} is not set (env or .env)")
    return value
