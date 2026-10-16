from learned.config import Thresholds
from learned.core import build_core, global_block, project_block, write_core_md
from learned.records import Record, new_id
from learned.store import Store

TH = Thresholds()


def rec(body, scope="global", project="proj", kind="preference", confidence="stated",
        sources=("s1",), last_seen="2026-01-01", status="active", stale=False):
    return Record(id=new_id(body, project, scope), kind=kind, scope=scope, project=project,
                  tags=[], sources=list(sources), confidence=confidence, status=status,
                  created="2025-12-01", last_seen=last_seen, body=body, stale=stale)


def mkstore(tmp_path, *records):
    s = Store(tmp_path)
    s.init()
    for r in records:
        s.save(r)
    return s


def test_empty_store_returns_empty_string(tmp_path):
    s = mkstore(tmp_path)
    assert global_block(s, TH) == ""
    assert project_block(s, "proj", TH) == ""
    assert build_core(s, "proj", TH) == ""
    assert build_core(s, None, TH) == ""


def test_stated_outranks_inferred(tmp_path):
    s = mkstore(tmp_path,
                rec("INFERRED many", confidence="inferred", sources=("a", "b", "c"),
                    last_seen="2026-05-01"),
                rec("STATED one", confidence="stated"),
                rec("STATED two sources", confidence="stated", sources=("a", "b")),
                rec("STATED newer", confidence="stated", last_seen="2026-03-01"))
    out = global_block(s, TH)
    order = [out.index(k) for k in ("STATED two sources", "STATED newer", "STATED one",
                                    "INFERRED many")]
    assert order == sorted(order)


def test_stale_ranked_last_and_flagged(tmp_path):
    s = mkstore(tmp_path, rec("OLD stale", stale=True, sources=("a", "b", "c")),
                rec("FRESH inferred", confidence="inferred"))
    out = global_block(s, TH)
    assert out.index("FRESH") < out.index("OLD stale")
    assert "stale" in out.split("OLD stale")[0].splitlines()[-1].lower()


def test_output_never_exceeds_core_max_chars(tmp_path):
    recs = [rec(f"Global preference number {i} " + "x" * 120, last_seen=f"2026-01-{i % 28 + 1:02d}")
            for i in range(200)]
    recs += [rec(f"Project decision {i} " + "y" * 100, scope="project", kind="decision")
             for i in range(50)]
    s = mkstore(tmp_path, *recs)
    (tmp_path / "projects" / "proj" / "gist.md").write_text(
        "## Purpose\n" + "p" * 300 + "\n## Status\nIn progress\n", encoding="utf-8")
    for out in (global_block(s, TH), project_block(s, "proj", TH), build_core(s, "proj", TH)):
        assert 0 < len(out) <= 6000
    assert len(build_core(s, "proj", Thresholds(core_max_chars=500))) <= 500


def test_project_block_has_gist_status_and_recent_rejections(tmp_path):
    s = mkstore(
        tmp_path,
        rec("Rejected Redis because of ops cost", scope="project", kind="rejection",
            last_seen="2026-02-02"),
        rec("Chose SQLite", scope="project", kind="decision", last_seen="2026-02-01"),
        rec("Old unrelated rejection", scope="project", kind="rejection", last_seen="2025-01-01"),
        rec("Other project rejection", scope="project", project="other", kind="rejection"),
        rec("A preference in the project", scope="project", kind="preference"),
    )
    (tmp_path / "projects" / "proj" / "gist.md").write_text(
        "## Purpose\nA memory tool\n## Architecture\nHooks + store\n## Status\nCapture done\n",
        encoding="utf-8")
    out = project_block(s, "proj", TH)
    for needle in ("A memory tool", "Hooks + store", "Capture done",
                   "Rejected Redis", "Chose SQLite"):
        assert needle in out
    assert "Other project" not in out and "A preference in the project" not in out
    assert out.index("Rejected Redis") < out.index("Chose SQLite")


def test_project_block_without_gist_still_lists_records(tmp_path):
    s = mkstore(tmp_path, rec("Rejected Z", scope="project", kind="rejection"))
    assert "Rejected Z" in project_block(s, "proj", TH)


def test_superseded_records_excluded(tmp_path):
    s = mkstore(tmp_path, rec("OLD global", status="superseded"),
                rec("OLD proj", scope="project", kind="rejection", status="superseded"),
                rec("LIVE global"))
    out = build_core(s, "proj", TH)
    assert "LIVE global" in out and "OLD" not in out


def test_text_is_factual_not_imperative(tmp_path):
    s = mkstore(tmp_path, rec("Prefers uv over pip"),
                rec("Rejected Redis", scope="project", kind="rejection"))
    out = build_core(s, "proj", TH)
    assert out.startswith("Learned notes from past sessions")
    for line in out.splitlines():
        assert not line.startswith(("You must", "Always"))
    assert "not instructions" in out.splitlines()[0]


def test_write_core_md(tmp_path):
    s = mkstore(tmp_path, rec("Prefers uv over pip"))
    p = write_core_md(s, TH)
    assert p == tmp_path / "core.md"
    assert p.read_text(encoding="utf-8") == global_block(s, TH)


def test_multiline_body_collapsed_to_one_line(tmp_path):
    s = mkstore(tmp_path, rec("line one\nline two"))
    assert "- " in global_block(s, TH) and "line one line two" in global_block(s, TH)


def test_combine_blocks_does_not_duplicate_header(tmp_path):
    from learned.core import combine_blocks
    s = mkstore(tmp_path, rec("Prefers uv"), rec("Rejected Z", scope="project", kind="rejection"))
    core_md = global_block(s, TH)
    out = combine_blocks(core_md, project_block(s, "proj", TH), TH.core_max_chars)
    assert out.count("Learned notes from past sessions") == 1
    assert out == build_core(s, "proj", TH)
    # an older/different header wording in core.md is dropped too
    old = "Learned notes from past sessions (old wording)\nGlobal (how the user works):\n- (preference, stated) x"
    out2 = combine_blocks(old, "", TH.core_max_chars)
    assert out2.count("Learned notes from past sessions") == 1 and "old wording" not in out2


def test_ranking_handles_timestamps_of_different_lengths(tmp_path):
    s = mkstore(tmp_path,
                rec("DATE only", last_seen="2026-01-01"),
                rec("DATETIME later", last_seen="2026-01-01T10:00:00Z"),
                rec("EARLIER", last_seen="2025-12-31T23:59:59Z"))
    out = global_block(s, TH)
    assert out.index("DATETIME later") < out.index("DATE only") < out.index("EARLIER")


def test_stale_stated_ranks_after_fresh_inferred(tmp_path):
    s = mkstore(tmp_path, rec("STALE stated", stale=True),
                rec("FRESH inferred", confidence="inferred"))
    out = global_block(s, TH)
    assert out.index("FRESH inferred") < out.index("STALE stated")
