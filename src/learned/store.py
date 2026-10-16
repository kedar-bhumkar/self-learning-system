"""On-disk store: layout, records, tags, tombstones, config, git (stdlib only)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .records import Record, parse_record, render_record

_GITIGNORE = ".lock\ninbox/.done/\ninbox/.done\n"
_SCOPE_DIRS = {"global": "global", "topic": "topics"}


class Store:
    def __init__(self, home: Path):
        self.home = Path(home)

    # ---- layout ----
    def init(self) -> None:
        for d in ("inbox", "global", "topics", "projects"):
            (self.home / d).mkdir(parents=True, exist_ok=True)
        tags = self.home / "tags.json"
        if not tags.exists():
            self._write_json(tags, {})
        gi = self.home / ".gitignore"
        if not gi.exists():
            gi.write_text(_GITIGNORE, encoding="utf-8", newline="\n")
        if not (self.home / ".git").exists():
            self._git("init", "-q")

    @staticmethod
    def _write_json(path: Path, data) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")

    @staticmethod
    def _read_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    # ---- records ----
    def path_for(self, r: Record) -> Path:
        if r.scope == "project":
            return self.home / "projects" / r.project / f"{r.id}.md"
        return self.home / _SCOPE_DIRS.get(r.scope, r.scope) / f"{r.id}.md"

    def save(self, r: Record) -> Path:
        p = self.path_for(r)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_record(r), encoding="utf-8", newline="\n")
        return p

    def _record_files(self):
        for d in ("global", "topics"):
            yield from sorted((self.home / d).glob("*.md"))
        yield from sorted((self.home / "projects").glob("*/*.md"))

    def get(self, id: str) -> Record | None:
        for p in self._record_files():
            if p.stem == id:
                try:
                    return parse_record(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return None
        return None

    def load_all(self, *, scope: str | None = None, project: str | None = None,
                 status: str | None = "active") -> list[Record]:
        out: list[Record] = []
        for p in self._record_files():
            if p.name == "gist.md":
                continue
            try:
                r = parse_record(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if scope is not None and r.scope != scope:
                continue
            if project is not None and r.project != project:
                continue
            if status is not None and r.status != status:
                continue
            out.append(r)
        return out

    def delete(self, id: str) -> None:
        for p in self._record_files():
            if p.stem == id:
                p.unlink()

    # ---- tags / tombstones / config ----
    def tags(self) -> dict[str, str]:
        return self._read_json(self.home / "tags.json", {})

    def add_tag(self, name: str, desc: str) -> None:
        t = self.tags()
        t[name] = desc
        self._write_json(self.home / "tags.json", t)

    def tombstones(self) -> list[str]:
        return self._read_json(self.home / "tombstones.json", [])

    def add_tombstone(self, text: str) -> None:
        t = self.tombstones()
        t.append(text)
        self._write_json(self.home / "tombstones.json", t)

    def config(self) -> dict:
        return self._read_json(self.home / "config.json", {})

    def set_config(self, **kv) -> None:
        c = self.config()
        c.update(kv)
        self._write_json(self.home / "config.json", c)

    # ---- git ----
    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-c", "user.name=learned", "-c", "user.email=learned@localhost",
             "-c", "core.autocrlf=false", "-C", str(self.home), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace")

    def commit(self, message: str) -> bool:
        """Commit everything in the store repo (not a code repo). False if clean/failed."""
        try:
            self._git("add", "-A")
            if not self._git("status", "--porcelain").stdout.strip():
                return False
            return self._git("commit", "-q", "-m", message).returncode == 0
        except OSError:
            return False
