import pytest

from learned.bare import is_bare_reply


@pytest.mark.parametrize("text", [
    "Go", "Yes", "1", "apply", "  YES!  ", "ok.", "go ahead", "Thanks!", "12", "1 2",
    "option b", "sounds good", "do it", "same as that", "lgtm", "(yes)", "yes please",
])
def test_bare(text):
    assert is_bare_reply(text)


@pytest.mark.parametrize("text", [
    "use pip not uv", "no, stage by path", "Go with option 2, batched subagents",
    "yes but use uv", "always use tabs", "", "   ", "no em dashes",
    "go yes ok sure",  # 4 words
])
def test_not_bare(text):
    assert not is_bare_reply(text)
