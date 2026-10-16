"""Small atomic file helpers (stdlib only)."""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path


def _tmp_for(path: Path) -> Path:
    return path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")


def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 text via a unique temp file + os.replace (never a partial file)."""
    path = Path(path)
    tmp = _tmp_for(path)
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        for attempt in range(3):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                # Windows: a reader holding the target open; retry briefly (~100 ms total)
                if attempt == 2:
                    raise
                time.sleep(0.05)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def create_numbered(directory: Path, stem: str, suffix: str, text: str) -> Path:
    """Atomically create the first free `<stem><suffix>`, `<stem>.1<suffix>`, ... with `text`.

    Never overwrites an existing file, even when several writers race: the complete content is
    written to a temp file and published with os.link (fails if the target exists).
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f"{stem}{suffix}.{uuid.uuid4().hex[:8]}.tmp"
    tmp.write_text(text, encoding="utf-8", newline="\n")
    try:
        n = 0
        while True:
            target = directory / (f"{stem}{suffix}" if n == 0 else f"{stem}.{n}{suffix}")
            try:
                os.link(tmp, target)
                return target
            except FileExistsError:
                n += 1
            except OSError:
                # filesystem without hard links: claim the name exclusively, then replace it
                try:
                    os.close(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                except FileExistsError:
                    n += 1
                    continue
                os.replace(tmp, target)
                return target
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
