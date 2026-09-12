"""Registry and reaper for live interact sessions.

An interact session holds a real browser. The agent is asked to close one when it
is done, but the server guarantees it: every open session is recorded on disk, a
background thread stops anything older than the age limit, and orphans left by a
dead process are reaped at startup.

Explorers run in parallel, so several live processes share this registry. Every
entry records the process that opened it, and startup reaping takes only the
sessions whose owner is gone — never one a sibling explorer is still reading.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from ..lock import guard, write_atomic
from ..runs import SEARCH

REGISTRY = SEARCH / ".sessions.json"
MAX_AGE = 120.0


def _load() -> dict[str, dict]:
    if not REGISTRY.exists():
        return {}
    try:
        return json.loads(REGISTRY.read_text())
    except json.JSONDecodeError:
        return {}


def _save(sessions: dict[str, dict]) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(REGISTRY, json.dumps(sessions, indent=2))


def _stop(client, sessions: dict[str, dict], ids: list[str]) -> None:
    """Close each session and drop it from the registry. Caller holds the lock."""
    for sid in ids:
        try:
            client.interact_stop(sid)
        except Exception:
            pass  # already gone server-side; drop it either way
        sessions.pop(sid, None)
    if ids:
        _save(sessions)


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def note_open(scrape_id: str, url: str) -> None:
    with guard(REGISTRY):
        sessions = _load()
        sessions.setdefault(
            scrape_id, {"url": url, "opened": time.time(), "pid": os.getpid()}
        )
        _save(sessions)


def note_closed(scrape_id: str) -> None:
    with guard(REGISTRY):
        sessions = _load()
        if sessions.pop(scrape_id, None) is not None:
            _save(sessions)


def age(scrape_id: str) -> float:
    entry = _load().get(scrape_id)
    return time.time() - entry["opened"] if entry else 0.0


def reap(client, max_age: float = MAX_AGE) -> list[str]:
    """Stop every session older than max_age. Returns the ids reaped."""
    with guard(REGISTRY):
        sessions = _load()
        stale = [sid for sid, e in sessions.items() if time.time() - e["opened"] > max_age]
        _stop(client, sessions, stale)
    return stale


def reap_orphans(client) -> list[str]:
    """Stop sessions whose owning process is gone. A session held by an explorer
    that is still running belongs to that process, not to us."""
    with guard(REGISTRY):
        sessions = _load()
        dead = [sid for sid, e in sessions.items() if not _alive(e.get("pid"))]
        _stop(client, sessions, dead)
    return dead


def start_reaper(client, max_age: float = MAX_AGE, interval: float = 15.0) -> None:
    reap_orphans(client)  # left behind by a process that died, not by a live sibling

    def loop():
        while True:
            time.sleep(interval)
            try:
                reap(client, max_age)
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True).start()


def stop_all(client) -> None:
    with guard(REGISTRY):
        sessions = _load()
        _stop(client, sessions, list(sessions))
        _save({})
