"""One-line append-only logs inside the store (stdlib only). Never raises."""
from __future__ import annotations

import traceback
from datetime import datetime, timezone
from pathlib import Path


def log_line(home: Path, filename: str, what: str, exc: BaseException | None = None) -> None:
    try:
        detail = ""
        if exc is not None:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            detail = " | " + " / ".join(l.strip() for l in tb.splitlines() if l.strip())
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{stamp} {what}{detail}".replace("\r", " ").replace("\n", " ")
        Path(home).mkdir(parents=True, exist_ok=True)
        with open(Path(home) / filename, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    except Exception:
        pass
