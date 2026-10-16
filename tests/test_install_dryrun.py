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
                                  "args": ["-P", "-m", "learned.hooks.session_end"], "timeout": 5}]}
    assert out["hooks"]["SessionStart"] == [
        {"matcher": "startup|resume|clear",
         "hooks": [{"type": "command", "command": PY,
                    "args": ["-P", "-m", "learned.hooks.session_start"], "timeout": 5}]}]
    assert "learned" not in json.dumps(EXISTING)  # input not mutated


def test_merge_over_old_bare_m_entry_updates_in_place():
    old = {"hooks": {
        "SessionEnd": [{"hooks": [{"type": "command", "command": "old.exe",
                                   "args": ["-m", "learned.hooks.session_end"], "timeout": 5}]}],
        "SessionStart": [{"matcher": "startup|resume|clear", "hooks": [
            {"type": "command", "command": "old.exe",
             "args": ["-m", "learned.hooks.session_start"], "timeout": 5}]}]}}
    out = merged(old)
    assert len(out["hooks"]["SessionEnd"]) == 1 and len(out["hooks"]["SessionStart"]) == 1
    h = out["hooks"]["SessionEnd"][0]["hooks"]
    assert len(h) == 1 and h[0]["args"] == ["-P", "-m", "learned.hooks.session_end"]
    assert h[0]["command"] == PY
    assert unmerge_hooks(old) == {}  # old form is removed too


def test_unmerge_keeps_user_empty_containers():
    s = {"hooks": {"SessionStart": [], "Other": []}}
    assert unmerge_hooks(s) == s
    assert unmerge_hooks({"hooks": {}}) == {"hooks": {}}


@pytest.mark.parametrize("bad", [{"hooks": []}, {"hooks": {"SessionEnd": {}}},
                                 {"hooks": {"SessionStart": ["x"]}},
                                 {"hooks": {"SessionEnd": [{"hooks": "x"}]}}])
def test_bad_shapes_raise(bad):
    with pytest.raises(ValueError):
        merged(bad)
    with pytest.raises(ValueError):
        unmerge_hooks(bad)


def test_rule_block_unbalanced_markers_error_and_outdated_body_refreshed():
    from learned.install_util import END, START
    for bad in (START + "\nx\n", "x\n" + END + "\n", END + "\n" + START + "\n"):
        with pytest.raises(ValueError):
            add_rule_block(bad)
        with pytest.raises(ValueError):
            remove_rule_block(bad)
    old = f"top\n\n{START}\nold text\n{END}\nbottom\n"
    new = add_rule_block(old)
    assert "old text" not in new and new.count(START) == 1
    assert new.startswith("top\n\n") and new.endswith("\nbottom\n")


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


def _run(script, home, *extra, learned_home=None):
    env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home)}
    env.pop("LEARNED_HOME", None)
    if learned_home:
        env["LEARNED_HOME"] = str(learned_home)
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
    assert "-P -m learned.consolidate" in out and "03:00" in out and "StartWhenAvailable" in out
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
    assert "would remove learned hooks" in res.stdout
    assert _tree(home) == before


# ---- --write paths (python level, temp files only) ----
def _util(*args):
    return subprocess.run([PY, "-P", "-m", "learned.install_util", *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


@pytest.mark.parametrize("nl", ["\n", "\r\n"])
def test_write_preserves_line_endings_and_backs_up(tmp_path, nl):
    f = tmp_path / "settings.json"
    raw = json.dumps(EXISTING, indent=2).replace("\n", nl) + nl
    f.write_bytes(raw.encode("utf-8"))
    res = _util("merge-hooks", "--settings", str(f), "--python", PY, "--write")
    assert res.returncode == 0, res.stderr
    out = f.read_bytes().decode("utf-8")
    assert out.endswith(nl)
    assert out.count("\r\n") == (out.count("\n") if nl == "\r\n" else 0)
    assert json.loads(out)["hooks"]["SessionEnd"][1]["hooks"][0]["args"][0] == "-P"
    baks = list(tmp_path.glob("settings.json.bak-*"))
    assert len(baks) == 1 and baks[0].read_bytes() == raw.encode("utf-8")
    res = _util("unmerge-hooks", "--settings", str(f), "--write")
    assert res.returncode == 0
    assert json.loads(f.read_text(encoding="utf-8")) == EXISTING


def test_write_without_trailing_newline_keeps_none(tmp_path):
    f = tmp_path / "settings.json"
    f.write_bytes(json.dumps({"a": 1}).encode("utf-8"))
    assert _util("merge-hooks", "--settings", str(f), "--python", PY, "--write").returncode == 0
    assert not f.read_bytes().endswith(b"\n")


def test_malformed_settings_exits_nonzero_and_is_untouched(tmp_path):
    f = tmp_path / "settings.json"
    f.write_bytes(b'{"hooks": ')
    for cmd in (("merge-hooks", "--python", PY), ("unmerge-hooks",)):
        res = _util(cmd[0], "--settings", str(f), *cmd[1:], "--write")
        assert res.returncode != 0 and "not valid JSON" in res.stderr
    assert f.read_bytes() == b'{"hooks": ' and not list(tmp_path.glob("*.bak-*"))


def test_claude_md_unbalanced_marker_exits_nonzero(tmp_path):
    f = tmp_path / "CLAUDE.md"
    f.write_bytes(b"x\n<!-- learned-memory:start -->\n")
    res = _util("rule", "add", "--claude-md", str(f), "--write")
    assert res.returncode != 0 and f.read_bytes() == b"x\n<!-- learned-memory:start -->\n"


# ---- ownership of skill / command ----
@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell not available")
def test_install_refuses_foreign_skill_and_command(tmp_path):
    home = tmp_path / "home"
    skill = home / ".claude" / "skills" / "recall"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("my own recall skill", encoding="utf-8")
    before = _tree(home)
    res = _run("install.ps1", home, "-DryRun")
    assert res.returncode != 0 and "REFUSED" in res.stdout and "skills" in res.stdout
    assert _tree(home) == before
    (skill / "SKILL.md").write_text("<!-- learned-memory: managed -->\n", encoding="utf-8")
    cmd = home / ".claude" / "commands" / "learned.md"
    cmd.parent.mkdir(parents=True)
    cmd.write_text("mine", encoding="utf-8")
    res = _run("install.ps1", home, "-DryRun")
    assert res.returncode != 0 and "REFUSED" in res.stdout and "learned.md" in res.stdout
    cmd.write_text("<!-- learned-memory: managed -->\n", encoding="utf-8")
    res = _run("install.ps1", home, "-DryRun")
    assert res.returncode == 0 and "REFUSED" not in res.stdout


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell not available")
def test_uninstall_skips_foreign_and_removes_managed(tmp_path):
    home = tmp_path / "home"
    skill = home / ".claude" / "skills" / "recall"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("my own recall skill", encoding="utf-8")
    res = _run("uninstall.ps1", home, "-DryRun")
    assert res.returncode == 0 and "SKIP (not managed" in res.stdout
    (skill / "SKILL.md").write_text("<!-- learned-memory: managed -->", encoding="utf-8")
    res = _run("uninstall.ps1", home, "-DryRun")
    assert "would remove: " + str(skill) in res.stdout


def test_shipped_files_carry_ownership_marker():
    for rel in ("skills/recall/SKILL.md", "commands/learned.md"):
        assert "learned-memory: managed" in (ROOT / rel).read_text(encoding="utf-8")
    assert "python -P -m learned.cli" in (ROOT / "commands/learned.md").read_text(encoding="utf-8")


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell not available")
@pytest.mark.parametrize("via_env", [False, True])
def test_install_custom_learned_home_warns_and_needs_accept(tmp_path, via_env):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    custom = tmp_path / "elsewhere" / "learned"
    before = _tree(home)
    extra = () if via_env else ("-LearnedHome", str(custom))
    kw = {"learned_home": custom} if via_env else {}
    res = _run("install.ps1", home, "-DryRun", *extra, **kw)
    assert res.returncode == 1, res.stderr + res.stdout
    assert "WARNING" in res.stdout and "setx LEARNED_HOME" in res.stdout
    assert str(custom) in res.stdout and "[1/6]" not in res.stdout
    assert _tree(home) == before
    res = _run("install.ps1", home, "-DryRun", "-AcceptCustomHome", *extra, **kw)
    assert res.returncode == 0, res.stderr + res.stdout
    assert "WARNING" in res.stdout and f"Store.init at {custom}" in res.stdout
    assert _tree(home) == before


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell not available")
def test_install_default_home_has_no_warning(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    res = _run("install.ps1", home, "-DryRun", "-LearnedHome",
               str(home / ".claude" / "learned") + "/")
    assert res.returncode == 0 and "WARNING" not in res.stdout


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell not available")
def test_failure_report_builder_lists_steps_and_undo():
    text = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    start = text.index("function Format-InstallFailure")
    fn = text[start:text.index("function Test-CustomHome")]
    cmd = (fn + " Format-InstallFailure -Completed @('1/6 package','2/6 hooks') "
           "-Failed '3/6 scheduled task' -Reason 'boom'")
    res = subprocess.run([POWERSHELL, "-NoProfile", "-Command", cmd], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=60)
    assert res.returncode == 0, res.stderr
    assert "completed steps: 1/6 package, 2/6 hooks" in res.stdout
    assert "failed step: 3/6 scheduled task (boom)" in res.stdout
    assert "manual undo: run scripts" + chr(92) + "uninstall.ps1" in res.stdout
