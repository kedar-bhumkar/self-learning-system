import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from learned.install_util import (add_rule_block, merge_hooks, remove_rule_block,
                                  session_end_group, session_start_group, unmerge_hooks)

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

EXISTING = {
    "model": "opus",
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}],
        "SessionEnd": [{"hooks": [{"type": "command", "command": "other.exe", "timeout": 9}]}],
    },
}


def merged(settings):
    return merge_hooks(settings, session_end_group(PY), session_start_group(PY))


def test_settings_merge_preserves_existing_hooks():
    out = merged(EXISTING)
    assert out["model"] == "opus"
    assert out["hooks"]["PreToolUse"] == EXISTING["hooks"]["PreToolUse"]
    ends = out["hooks"]["SessionEnd"]
    assert ends[0] == EXISTING["hooks"]["SessionEnd"][0]
    assert ends[1] == {"hooks": [{"type": "command", "command": PY,
                                  "args": ["-m", "learned.hooks.session_end"], "timeout": 5}]}
    assert out["hooks"]["SessionStart"] == [
        {"matcher": "startup|resume|clear",
         "hooks": [{"type": "command", "command": PY,
                    "args": ["-m", "learned.hooks.session_start"], "timeout": 5}]}]
    assert "learned" not in json.dumps(EXISTING)  # input not mutated


def test_merge_is_idempotent_and_handles_empty():
    once = merged(EXISTING)
    assert merged(once) == once
    assert merged({})["hooks"]["SessionEnd"] == [session_end_group(PY)]


def test_unmerge_removes_only_ours():
    assert unmerge_hooks(merged(EXISTING)) == EXISTING
    assert unmerge_hooks(merged({})) == {}


def test_rule_block_idempotent_removable_and_keeps_line_endings():
    base = "# my rules\r\nbe nice\r\n"
    once = add_rule_block(base)
    assert once.count("<!-- learned-memory:start -->") == 1
    assert "\r\n" in once and "\n" not in once.replace("\r\n", "")
    assert "recall" in once and "~/.claude/learned" in once
    assert add_rule_block(once) == once
    assert remove_rule_block(once) == base
    assert remove_rule_block(add_rule_block("")) == ""
    assert remove_rule_block("untouched") == "untouched"


def _run(script, home, *extra):
    env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home)}
    env.pop("LEARNED_HOME", None)
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts" / script), "-Python", PY, *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        timeout=120)


def _tree(root):
    """Snapshot of <home>/.claude (PowerShell itself creates AppData in a fresh profile)."""
    root = root / ".claude"
    return sorted((str(p.relative_to(root)), p.read_bytes() if p.is_file() else b"")
                  for p in root.rglob("*"))


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell not available")
def test_install_dryrun_lists_all_six_changes_and_modifies_nothing(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    settings = home / ".claude" / "settings.json"
    settings.write_text(json.dumps(EXISTING, indent=2), encoding="utf-8")
    claude_md = home / ".claude" / "CLAUDE.md"
    claude_md.write_text("# rules\r\n", encoding="utf-8", newline="")
    before = _tree(home)
    res = _run("install.ps1", home, "-DryRun")
    assert res.returncode == 0, res.stderr + res.stdout
    out = res.stdout
    for marker in ("[1/6] pip install", "[2/6] settings.json", "[3/6] scheduled task",
                   "[4/6] skill and command", "[5/6] CLAUDE.md", "[6/6] Store.init"):
        assert marker in out, marker
    assert "learned.hooks.session_end" in out and "learned.hooks.session_start" in out
    assert "learned.consolidate" in out and "03:00" in out and "StartWhenAvailable" in out
    assert str(home) in out
    assert _tree(home) == before


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell not available")
def test_uninstall_dryrun_modifies_nothing(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps(merged(EXISTING)), encoding="utf-8")
    before = _tree(home)
    res = _run("uninstall.ps1", home, "-DryRun")
    assert res.returncode == 0, res.stderr + res.stdout
    assert "learned.hooks.session_end" in res.stdout or "remove" in res.stdout.lower()
    assert _tree(home) == before
