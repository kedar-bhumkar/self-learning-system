from learned.redact import redact


def test_redact_keys_and_emails():
    out = redact("key sk-abc123def456ghi789 mail a@b.com")
    assert "sk-abc" not in out and "a@b.com" not in out
    assert "[REDACTED]" in out


def test_redact_other_token_shapes():
    for s in ("Authorization: Bearer abcdefgh12345678",
              "ghp_" + "a1" * 15,
              "AKIAABCDEFGHIJKLMNOP",
              "deadbeef" * 6,
              "api_key = hunter2hunter2",
              "Zm9vYmFy" + "QmF6UXV4" * 6):
        out = redact(s)
        assert "[REDACTED]" in out, s
    assert "hunter2" not in redact("api_key = hunter2hunter2")


def test_redact_leaves_normal_text():
    s = "Please don't use café ☕ in src/learned/hooks/session_end.py, use the other way."
    assert redact(s) == s
