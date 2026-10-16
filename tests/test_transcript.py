import json

from learned.transcript import extract_windows


def line(type_, content, **kw):
    d = {"type": type_, "message": {"role": type_, "content": content},
         "timestamp": "2026-01-01T00:00:00Z"}
    d.update(kw)
    return json.dumps(d, ensure_ascii=False)


def write(tmp_path, lines):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_user_turn_with_preceding_assistant_context(tmp_path):
    p = write(tmp_path, [
        line("user", "first"),
        line("assistant", [{"type": "text", "text": "I propose option A"}]),
        line("user", "no, don't use A"),
    ])
    ws = extract_windows(p)
    assert [w.text for w in ws] == ["first", "no, don't use A"]
    assert ws[0].context == ""
    assert ws[1].context == "I propose option A"
    assert not ws[1].is_choice_answer
    assert ws[1].timestamp == "2026-01-01T00:00:00Z"


def test_context_truncated_to_1500(tmp_path):
    p = write(tmp_path, [line("assistant", "x" * 3000), line("user", "hi")])
    assert len(extract_windows(p)[0].context) == 1500


def test_skips_meta_sidechain_system_reminder_and_tool_results(tmp_path):
    p = write(tmp_path, [
        line("user", "meta", isMeta=True),
        line("user", "side", isSidechain=True),
        line("user", "<system-reminder>x</system-reminder>"),
        line("user", "<local-command-stdout>x"),
        line("user", "<command-name>/foo</command-name>"),
        line("system", "sys"),
        line("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "out"}]),
        line("user", "real one"),
    ])
    assert [w.text for w in extract_windows(p)] == ["real one"]


def test_content_as_block_list_joined(tmp_path):
    p = write(tmp_path, [line("user", [{"type": "text", "text": "a"},
                                       {"type": "text", "text": "b"}])])
    assert extract_windows(p)[0].text == "a\nb"


def test_missing_content_skipped(tmp_path):
    p = write(tmp_path, [json.dumps({"type": "user"}),
                         json.dumps({"type": "user", "message": {"role": "user"}}),
                         json.dumps({"type": "user", "message": "str"}),
                         line("user", "ok")])
    assert [w.text for w in extract_windows(p)] == ["ok"]


def test_ask_user_question_answer_flagged(tmp_path):
    p = write(tmp_path, [
        line("assistant", [{"type": "tool_use", "id": "q1", "name": "AskUserQuestion",
                            "input": {"questions": [{"question": "Which db?"}]}}]),
        line("user", [{"type": "tool_result", "tool_use_id": "q1",
                       "content": 'User has answered: "Which db?"="SQLite"'}]),
        line("assistant", [{"type": "tool_use", "id": "b1", "name": "Bash", "input": {}}]),
        line("user", [{"type": "tool_result", "tool_use_id": "b1", "content": "ls out"}]),
    ])
    ws = extract_windows(p)
    assert len(ws) == 1
    assert ws[0].is_choice_answer
    assert "SQLite" in ws[0].text
    assert "Which db?" in ws[0].context


def test_malformed_and_truncated_lines_skipped(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join([line("user", "one"), "garbage{{", line("user", "two"),
                            line("user", "half")[:25]]), encoding="utf-8")
    assert [w.text for w in extract_windows(p)] == ["one", "two"]


def test_missing_file_returns_empty(tmp_path):
    assert extract_windows(tmp_path / "nope.jsonl") == []


def test_line_no_is_one_based_file_line(tmp_path):
    p = write(tmp_path, ["garbage", line("assistant", "a"), "", line("user", "x")])
    assert extract_windows(p)[0].line_no == 4


def test_pasted_content_tags_removed(tmp_path):
    p = write(tmp_path, [
        line("user", 'before <pasted_content id="1">\nSECRET BIG\nPASTE</pasted_content> after'),
        line("user", '<pasted_content a="b">only</pasted_content>'),
    ])
    ws = extract_windows(p)
    assert len(ws) == 1
    assert "SECRET" not in ws[0].text
    assert ws[0].text.split() == ["before", "after"]


def test_system_reminder_blocks_and_spans_removed(tmp_path):
    p = write(tmp_path, [
        line("user", [{"type": "text", "text": "real one"},
                      {"type": "text", "text": "<system-reminder>noise</system-reminder>"}]),
        line("user", [{"type": "text", "text": "<system-reminder>noise</system-reminder>"},
                      {"type": "text", "text": "real two"}]),
        line("user", [{"type": "text", "text": "<system-reminder>only</system-reminder>"}]),
        line("user", "real three <system-reminder>\nmulti\nline</system-reminder> tail"),
        line("user", [{"type": "text", "text": "<command-name>/x</command-name>"},
                      {"type": "text", "text": "real four"}]),
    ])
    texts = [w.text for w in extract_windows(p)]
    assert texts == ["real one", "real two", "real three  tail", "real four"]
    assert all("noise" not in t and "multi" not in t for t in texts)
