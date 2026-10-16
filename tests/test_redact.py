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


import pytest

SECRETS = {
    "google": "AIza" + "A1b2C3d4E5" * 3 + "xyz12",
    "stripe_sk": "sk_live_" + "abcdEFGH12345678wxyz",
    "stripe_rk": "rk_test_" + "abcdEFGH12345678wxyz",
    "npm": "npm_" + "aB3dE5gH7jK9mN1pQ3sT5v",
    "gitlab": "glpat-" + "aB3dE5gH7jK9mN1pQ3sT5v",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
}


@pytest.mark.parametrize("name", sorted(SECRETS))
def test_redact_token_shapes(name):
    s = SECRETS[name]
    out = redact(f"use {s} here")
    assert s not in out and out == "use [REDACTED] here"


@pytest.mark.parametrize("text,secret", [
    ("the password is hunter2hunter2 ok", "hunter2hunter2"),
    ("passwd: abc12345xyz", "abc12345xyz"),
    ("pwd = Zx9Zx9Zx9", "Zx9Zx9Zx9"),
    ("password=\"s3cretValue99\"", "s3cretValue99"),
])
def test_redact_password_phrasing(text, secret):
    out = redact(text)
    assert secret not in out and "[REDACTED]" in out


@pytest.mark.parametrize("text", [
    "pin react@18.3.1 and @types/node@20.1.0",
    "install typescript@5.4.2 please",
    "set max tokens: 4096",
    "the secret: use tabs",
    "the password is required",
    "token = None",
    "mail me at user@example.co.uk",  # still an email: handled below
])
def test_benign_strings_survive(text):
    if "user@example" in text:
        assert "user@example" not in redact(text)
    else:
        assert redact(text) == text
