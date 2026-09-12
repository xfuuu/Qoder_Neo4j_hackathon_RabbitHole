"""Cross-process locking for the files parallel explorers share.

Each explorer subagent runs its own server process, so a `threading.Lock` guards
nothing: credit accounting, page-id allocation and the session registry are all
read-modify-write over files that two processes can reach at the same moment.
"""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def guard(path: Path):
    """Serialize a read-modify-write on `path` across processes and threads."""
    lockfile = path.with_name(path.name + ".lock")
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    with lockfile.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def write_atomic(path: Path, text: str) -> None:
    """Replace `path` in one step, so a concurrent reader never catches it empty."""
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(text)
    tmp.replace(path)
