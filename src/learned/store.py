"""On-disk store: layout, records, tags, tombstones, config, git (stdlib only)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .fsutil import atomic_write_text
from .records import Record, parse_record, render_record

_IGNORE_LINES = [".lock", "inbox/", "*.tmp", "capture.log", "consolidate.log",
                 "tombstones.json"]
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
        try:
            have = gi.read_text(encoding="utf-8").splitlines()
        except OSError:
            have = []
        missing = [l for l in _IGNORE_LINES if l not in have]
        if missing:
            atomic_write_text(gi, "\n".join(have + missing) + "\n")
        if not (self.home / ".git").exists():
            self._git("init", "-q")

    @staticmethod
    def _write_json(path: Path, data) -> None:
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    @staticmethod
    def _read_json(path: Path, default, strict: bool = False):
        """Plain reads fall back to `default` on any problem; strict reads (used before a
        write) only default for a missing file and raise on corrupt JSON, so a bad file is
        never silently overwritten."""
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (OSError, ValueError):
            if strict:
                raise
            return default

    # ---- records ----
    def path_for(self, r: Record) -> Path:
        if r.scope == "project":
            return self.home / "projects" / r.project / f"{r.id}.md"
        return self.home / _SCOPE_DIRS.get(r.scope, r.scope) / f"{r.id}.md"

    def save(self, r: Record) -> Path:
        p = self.path_for(r)
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p, render_record(r))
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
        path = self.home / "tags.json"
        t = self._read_json(path, {}, strict=True)
        t[name] = desc
        self._write_json(path, t)

    def tombstones(self) -> list[str]:
        return self._read_json(self.home / "tombstones.json", [])

    def add_tombstone(self, text: str) -> None:
        path = self.home / "tombstones.json"
        t = self._read_json(path, [], strict=True)
        t.append(text)
        self._write_json(path, t)

    def config(self) -> dict:
        return self._read_json(self.home / "config.json", {})

    def set_config(self, **kv) -> None:
        path = self.home / "config.json"
        c = self._read_json(path, {}, strict=True)
        c.update(kv)
        self._write_json(path, c)

    # ---- git ----
    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-c", "user.name=learned", "-c", "user.email=learned@localhost",
             "-c", "core.autocrlf=false", "-C", str(self.home), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace")

    def _untrack_ignored(self) -> None:
        """Stores created before inbox/ and tombstones.json were ignored may track them:
        untrack each once (forgotten text must never reach the store's git history)."""
        for path in ("inbox", "tombstones.json"):
            tracked = self._git("ls-files", "--", path)
            if tracked.returncode == 0 and tracked.stdout.strip():
                self._git("rm", "-r", "--cached", "-q", "--ignore-unmatch", "--", path)

    def commit(self, message: str) -> bool:
        """Commit everything in the store repo (not a code repo).

        Returns True if a commit was made, False if there was nothing to commit.
        Raises RuntimeError (or OSError if git is missing) when git itself fails.
        """
        self._untrack_ignored()
        add = self._git("add", "-A")
        if add.returncode != 0:
            raise RuntimeError(f"git add failed: {add.stderr.strip()}")
        st = self._git("status", "--porcelain")
        if st.returncode != 0:
            raise RuntimeError(f"git status failed: {st.stderr.strip()}")
        if not st.stdout.strip():
            return False
        res = self._git("commit", "-q", "-m", message)
        if res.returncode != 0:
            raise RuntimeError(f"git commit failed: {(res.stderr or res.stdout).strip()}")
        return True
