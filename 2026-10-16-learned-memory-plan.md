# Learned Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local background system that captures the user's decisions, rejections, preferences and project gists from Claude Code sessions, consolidates and promotes them with Jev, and feeds them back into later sessions.

**Architecture:** A stdlib-only `SessionEnd` hook spawns a detached capture worker that writes candidate facts to `~/.claude/learned/inbox/`. A daily scheduled task runs consolidation (dedupe, tag, promote, gist refresh) using embeddings and Jev judgments behind a `Judge` interface. A `SessionStart` hook injects a capped core block; a `recall` skill searches the store on demand.

**Tech Stack:** Python 3.13, `typesafe-sdk` (Jev), `sentence-transformers` + `numpy` (already installed), `pytest`, git, Windows Task Scheduler.

**Spec:** `2026-10-16-learned-memory-design.md` (same folder). Repo root for all paths below: `C:\DDrive\Programming\Project\misc\self-learning-system`.

## Global Constraints

- Python 3.13 on Windows; use the existing global interpreter, installing the package with `pip install -e ".[dev]"`. Hook modules (`learned/hooks/*`) import stdlib only (no numpy, torch, typesafe).
- `SessionEnd` default budget is 1.5 s: the hook must spawn a detached worker and return in well under 1 s; set the hook `timeout` to 5.
- Jev: `TYPESAFE_API_KEY` from the environment only, never written to disk; model `jev-latest`; one condition per Noul; Choice up to 255 options with a `none` option.
- Store `~/.claude/learned/` is a git repo with no remote; override the location with env `LEARNED_HOME` (all tests use `tmp_path`).
- Extractor runs set env `LEARNED_EXTRACTOR=1`; capture and hooks skip when it is set. A `.no-learn` file in the session `cwd` skips capture.
- All file I/O is explicit UTF-8; hook stdout is JSON with `ensure_ascii=True`. Hooks always exit 0 and never raise.
- Project name = lowercased basename of `cwd`; `home` if `cwd` is the user's home directory.
- Default thresholds (tunable in Task 13): see `Thresholds` in Task 1.
- Core block ≤ 6000 characters; text is factual statements, not imperatives.
- Creating the persistent `zvec` search index requires explicit user authorization at execution time. Editing `~/.claude/settings.json`, `~/.claude/CLAUDE.md` or registering the scheduled task requires showing the diff and getting the user's OK first.
- Commits: stage files by explicit path (never `git add .`); every message ends with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

- Truncated or malformed JSONL lines, and `message.content` given as a list of blocks or missing: skip the line, never crash (Task 2).
- Non-ASCII text (emoji, accents) and paths with spaces through hook stdin/stdout and file writes on Windows cp1252 defaults (Tasks 4, 5).
- Jev API down or `TYPESAFE_API_KEY` missing: capture stores unclassified candidates; consolidation skips Jev steps, keeps the inbox, and retries next run (Tasks 4, 10).
- A session that is resumed or `/clear`ed and ends again with the same `session_id`: only windows after the stored watermark are captured (Task 4).
- Capture writing to the inbox while consolidation runs, and two sessions ending at once: per-session inbox files, atomic writes, lock only around consolidation (Tasks 4, 10).

---

## File Structure

```
pyproject.toml
src/learned/__init__.py        (empty)
src/learned/config.py          paths, Thresholds, project_name
src/learned/models.py          Window, Candidate, verdict dataclasses
src/learned/records.py         Record + markdown/frontmatter (de)serialization
src/learned/store.py           Store: layout, save/load, tags, tombstones, git commit
src/learned/redact.py          secret redaction
src/learned/transcript.py      JSONL -> Windows
src/learned/judge.py           Judge protocol, JevJudge
src/learned/testing.py         FakeJudge, FakeEmbedder (used by tests only)
src/learned/embed.py           Embedder (sentence-transformers)
src/learned/capture.py         capture_session pipeline + worker entry
src/learned/hooks/session_end.py, session_start.py
src/learned/core.py            core block assembly
src/learned/ingest.py          dedupe/merge/supersede
src/learned/tagging.py         tag reuse / creation
src/learned/promotion.py       cluster, judge, promote, log
src/learned/gist.py            claude -p runner, gist refresh
src/learned/consolidate.py     orchestrator, lock, decay, weekly sweep
src/learned/cli.py             list/show/forget/demote/pin/rerun
skills/recall/SKILL.md, commands/learned.md, scripts/install.ps1
tests/test_*.py, eval/labeled.jsonl, eval/run_eval.py
```

---

### Task 1: Config, records and store

**Files:** Create `pyproject.toml`, `src/learned/{__init__,config,models,records,store}.py`; Test `tests/test_store.py`.

**Interfaces:**
- Produces:
  - `config.learned_home() -> Path`; `config.project_name(cwd: str) -> str`; `@dataclass Thresholds` with defaults `window_min_prob=0.6, sensitive_drop=0.5, min_user_turns=3, neighbor_k=8, neighbor_sim=0.75, same_fact=0.8, contradiction=0.8, tag_reuse=0.6, new_tag_none=0.7, new_tag_runner_up=0.25, cluster_sim=0.8, promote_topic=0.85, promote_global=0.90, tombstone_sim=0.9, stale_days=90, core_max_chars=6000`.
  - `models.py`: frozen `Window(text, context, is_choice_answer, timestamp, line_no)`; `Candidate(text, context, kind: str|None, kind_prob, project, cwd, session_id, timestamp)` with `to_json()/from_json()`; frozen `WindowVerdict(kind, kind_prob, sensitive_prob)`, `TagVerdict(tag: str|None, probabilities: dict[str,float])`, `PromotionVerdict(personal, same, stable)`.
  - `records.Record` dataclass: `id, kind, scope, project, tags: list, sources: list, confidence, status, created, last_seen, body, pinned=False, stale=False, promoted_to=None, judgments: dict`; `render_record(r) -> str`, `parse_record(text) -> Record`, `new_id(body) -> str` (first 10 hex of sha1). Frontmatter is one `key: <json>` line per field.
  - `store.Store(home: Path)`: `init()`, `path_for(r) -> Path`, `save(r) -> Path`, `get(id) -> Record|None`, `load_all(*, scope=None, project=None, status="active") -> list[Record]`, `delete(id)`, `tags() -> dict[str,str]`, `add_tag(name, desc)`, `add_tombstone(text)`, `tombstones() -> list[str]`, `config() -> dict`, `set_config(**kv)`, `commit(message) -> bool`.

- [ ] **Step 1: Write failing tests** in `tests/test_store.py`: `test_render_parse_roundtrip_unicode` (body `"don't use café ☕"` survives); `test_init_creates_layout_and_git_repo` (`inbox/`, `global/`, `topics/`, `projects/`, `tags.json`, `.git` exist; `.lock` and `inbox/.done` ignored); `test_save_paths_by_scope` (project -> `projects/<p>/<id>.md`, topic -> `topics/`, global -> `global/`); `test_load_all_filters_status_and_scope`; `test_init_idempotent`; `test_project_name_home_and_basename`; `test_commit_returns_false_when_clean`.
- [ ] **Step 2:** Run `python -m pytest tests/test_store.py -v`; expect FAIL (ImportError).
- [ ] **Step 3:** Implement the above in the listed files; `pyproject.toml` declares package `learned` under `src/`, deps `typesafe-sdk`, `sentence-transformers`, `numpy`, dev extra `pytest`; marker `live` registered. `store.commit` runs `git add -A` inside the store repo only (the store is not the user's code repo).
- [ ] **Step 4:** `pip install -e ".[dev]"` then run the tests; expect PASS.
- [ ] **Step 5:** `git add pyproject.toml src tests` and commit `feat: store, records, config`.

### Task 2: Redaction and transcript windows

**Files:** Create `src/learned/{redact,transcript}.py`; Test `tests/test_redact.py`, `tests/test_transcript.py`.

**Interfaces:**
- Consumes: `models.Window`.
- Produces: `redact.redact(text: str) -> str` (replaces API-key-like tokens, `Bearer` tokens, `sk-…`, `ghp_…`, AWS keys, emails, long hex/base64 secrets with `[REDACTED]`); `transcript.extract_windows(path: Path) -> list[Window]`.

- [ ] **Step 1: Write failing tests.** `test_redact_keys_and_emails` (`"key sk-abc123def456ghi789 mail a@b.com"` contains no `sk-abc` nor `a@b.com`); `test_redact_leaves_normal_text`. Transcript tests build JSONL in `tmp_path`: `test_user_turn_with_preceding_assistant_context`; `test_skips_meta_sidechain_system_reminder_and_tool_results`; `test_content_as_block_list_joined`; `test_ask_user_question_answer_flagged` (tool_use named `AskUserQuestion` followed by its `tool_result` -> `is_choice_answer=True`); `test_malformed_and_truncated_lines_skipped` (garbage line and a final half-written line yield the valid windows only); `test_line_no_is_one_based_file_line`; `test_pasted_content_tags_removed`.
- [ ] **Step 2:** Run both test files; expect FAIL.
- [ ] **Step 3:** Implement. Skip lines where `isMeta` or `isSidechain` is true, `type` not in `user`/`assistant`, or the text starts with `<system-reminder>`, `<local-command`, `<command-name>`. Drop `<pasted_content …>…</pasted_content>` spans. Context = text of the nearest preceding assistant message, truncated to 1500 chars.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: redaction and transcript windows` (explicit paths).

### Task 3: Judge interface and Jev implementation

**Files:** Create `src/learned/{judge,testing}.py`; Test `tests/test_judge.py`.

**Interfaces:**
- Consumes: verdict dataclasses.
- Produces: `class Judge(Protocol)` with `classify_window(text: str, context: str) -> WindowVerdict`, `same_fact(a: str, b: str) -> float`, `contradicts(a: str, b: str) -> float`, `choose_tag(text: str, tags: dict[str,str]) -> TagVerdict`, `promotion(texts: list[str]) -> PromotionVerdict`, `fits_tag(text: str, tag: str, description: str) -> float`; `JevJudge(client=None, model="jev-latest")`; `testing.FakeJudge(**overrides)` where each override is a callable replacing the method of the same name and defaults are neutral (`kind="none"`, probabilities 0.0, `tag=None`); `testing.FakeEmbedder(vectors: dict[str, list[float]])` with `encode(texts) -> np.ndarray` (rows L2-normalized).

- [ ] **Step 1: Write failing tests.** Offline: `test_choose_tag_builds_choice_with_none_and_descriptions` using a stub client that records the question; `test_promotion_sends_three_separate_noul_questions`; `test_classify_window_sends_choice_and_sensitive_noul`; `test_kind_prob_read_from_probabilities`. Live (`@pytest.mark.live`, skipped without `TYPESAFE_API_KEY`): `test_live_noul_in_unit_interval`, `test_live_choice_returns_listed_option`.
- [ ] **Step 2:** Run `python -m pytest tests/test_judge.py -v -m "not live"`; expect FAIL.
- [ ] **Step 3:** Implement `JevJudge` over `typesafe_sdk.TypeSafeClient().system_one(state=..., questions=...)`, reading `response.nouls[id].noul` and `response.choices[id].choice` / `.probabilities`. The SDK example shows no `model` argument: inspect `inspect.signature(TypeSafeClient.system_one)` and the "Choosing a model" docs page and pass `model="jev-latest"` only in the form the SDK accepts. Question wording follows the docs: one condition per Noul, positive phrasing (high = yes). Choice kinds: `rejection|decision|preference|correction|none`.
- [ ] **Step 4:** Run offline tests (PASS), then `python -m pytest tests/test_judge.py -m live -v`; confirm the `.probabilities` attribute name and fix the wrapper if it differs.
- [ ] **Step 5:** Commit `feat: judge interface and Jev implementation`.

### Task 4: Capture pipeline and SessionEnd hook

**Files:** Create `src/learned/capture.py`, `src/learned/hooks/{__init__,session_end}.py`; Test `tests/test_capture.py`, `tests/test_session_end_hook.py`.

**Interfaces:**
- Consumes: `extract_windows`, `redact`, `Judge`, `Store`, `Thresholds`, `project_name`.
- Produces: `capture.capture_session(transcript: Path, session_id: str, cwd: str, judge: Judge, store: Store, th: Thresholds = Thresholds()) -> int` (number of candidates written); `capture.main(argv) -> int` (worker entry: `python -m learned.capture <transcript> <session_id> <cwd>`); `hooks.session_end.main(stdin_text: str, spawn=subprocess.Popen) -> int`.
- Writes `inbox/<session_id>.jsonl` (atomic: temp file then rename; appended sessions write `<session_id>.<n>.jsonl`) and watermark `inbox/.done/<session_id>.json` = `{"last_line": int}`; writes `projects/<p>/meta.json` = `{"cwd": ...}` on first sight.

- [ ] **Step 1: Write failing tests** with `FakeJudge`: `test_writes_classified_candidates_above_threshold`; `test_drops_none_low_prob_and_sensitive` (sensitive_prob 0.9 dropped; kind_prob 0.4 dropped); `test_text_is_redacted_before_judge_sees_it` (judge stub asserts no secret); `test_skips_when_extractor_env_set`; `test_skips_when_no_learn_file`; `test_skips_short_sessions` (< `min_user_turns` windows); `test_watermark_only_new_windows_on_second_call`; `test_judge_failure_stores_unclassified` (judge raises -> candidate `kind is None`); `test_writes_project_meta`; `test_unicode_text_roundtrip`. Hook: `test_hook_spawns_detached_and_returns_zero` (fake `spawn` captures args incl. `creationflags` and env, no wait); `test_hook_garbage_stdin_returns_zero`; `test_hook_skips_when_extractor_env_set`; `test_hook_runs_under_half_second` (real call with fake spawn).
- [ ] **Step 2:** Run both files; expect FAIL.
- [ ] **Step 3:** Implement. Hook reads `session_id`, `transcript_path`, `cwd` from stdin JSON (common fields; the SessionEnd-specific field list was not verified in docs, so rely only on these three), spawns `[sys.executable, "-m", "learned.capture", transcript, session_id, cwd]` with `creationflags=DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP`, `close_fds=True`, no pipes. Wrap everything in try/except returning 0.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: session-end capture`.

### Task 5: Core block and SessionStart hook

**Files:** Create `src/learned/core.py`, `src/learned/hooks/session_start.py`; Test `tests/test_core.py`, `tests/test_session_start_hook.py`.

**Interfaces:**
- Produces: `core.global_block(store: Store, th: Thresholds) -> str`; `core.project_block(store: Store, project: str, th: Thresholds) -> str`; `core.build_core(store, project: str|None, th) -> str` (empty string when there is nothing to say); `core.write_core_md(store, th) -> Path` (writes `core.md` with the global block); `hooks.session_start.main(stdin_text: str, home: Path|None = None) -> str` (returns the JSON string printed to stdout, or `""`).
- Ranking for global records: `confidence == "stated"` first, then `len(sources)` descending, then `last_seen` descending; skip `stale` unless nothing else fits.

- [ ] **Step 1: Write failing tests:** `test_empty_store_returns_empty_string`; `test_stated_outranks_inferred`; `test_output_never_exceeds_core_max_chars` (200 records, `len(out) <= 6000`); `test_project_block_has_gist_status_and_recent_rejections`; `test_superseded_records_excluded`; `test_text_is_factual_not_imperative` (header starts `Learned notes from past sessions`; no line starts with `You must`/`Always`); hook: `test_hook_emits_additional_context_json` (parse stdout, `hookSpecificOutput.hookEventName == "SessionStart"`); `test_hook_prints_nothing_for_empty_store`; `test_hook_swallows_errors_and_exits_clean` (corrupt record file); `test_hook_handles_unicode_and_spaces_in_cwd`; `test_hook_skips_when_extractor_env_set`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement; the hook reads `core.md` for the global part when present, else computes it; drop the lowest-ranked lines until under the cap. Output is `json.dumps({...}, ensure_ascii=True)`.
- [ ] **Step 4:** Run; expect PASS. **Step 5:** Commit `feat: core block and session-start hook`.

### Task 6: Embeddings and ingest (dedupe, merge, supersede)

**Files:** Create `src/learned/{embed,ingest}.py`; Test `tests/test_ingest.py`.

**Interfaces:**
- Consumes: `Store`, `Judge`, `Candidate`, `Record`, `Thresholds`.
- Produces: `embed.Embedder(model_name="all-MiniLM-L6-v2")` with `encode(texts: list[str]) -> np.ndarray` (L2-normalized, so dot product = cosine); `ingest.ingest(store, judge, embedder, th, now: str) -> IngestReport` where `IngestReport` has `created: list[str]`, `merged: list[tuple[str,str]]`, `superseded: list[str]`, `skipped: int`, `jev_failed: bool`. Processed inbox files move to `inbox/processed/`.

- [ ] **Step 1: Write failing tests** using `FakeEmbedder` and `FakeJudge`: `test_new_candidate_becomes_project_record_stated`; `test_unclassified_candidate_is_classified_at_ingest`; `test_none_kind_after_classification_is_dropped`; `test_same_fact_merges_sources_and_bumps_last_seen`; `test_contradiction_supersedes_older_stated_loses_to_newer_stated`; `test_inferred_never_supersedes_stated`; `test_pinned_record_not_superseded_but_gains_sources`; `test_neighbors_limited_to_k_and_cutoff` (judge call count); `test_judge_exception_keeps_inbox_file_and_flags_failure`; `test_tombstoned_text_is_skipped` (sim ≥ `tombstone_sim`).
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. Neighbors: active records of the same project first, then others, cosine ≥ `neighbor_sim`, top `neighbor_k`. Merge if `same_fact ≥ th.same_fact`; else supersede if `contradicts ≥ th.contradiction` and the new record is `stated`. Record ids come from `new_id(body)`.
- [ ] **Step 4:** Run; expect PASS. **Step 5:** Commit `feat: ingest with embeddings and dedupe`.

### Task 7: Tagging

**Files:** Create `src/learned/tagging.py`; Test `tests/test_tagging.py`.

**Interfaces:**
- Consumes: `Judge.choose_tag`, `Store.tags/add_tag`, `ClaudeRunner = Callable[[str], str]` (a type alias defined in `tagging.py`; Task 9's `gist.claude_p` is the real runner, injected by Task 10).
- Produces: `tagging.assign_tag(store, judge, runner, record, th) -> Record` (returns the record with `tags` set to a one-element list or left `[]`).

- [ ] **Step 1: Write failing tests:** `test_confident_existing_tag_reused`; `test_none_with_weak_runner_up_creates_tag_via_runner` (runner returns `"name: hyperframes\ndescription: Video pipeline work"`, tag appears in `tags.json`); `test_none_with_strong_runner_up_creates_no_tag`; `test_empty_vocabulary_skips_judge_and_creates_tag`; `test_low_confidence_leaves_untagged`; `test_new_tag_name_is_slugified_and_unique`; `test_runner_failure_leaves_untagged`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement per spec §5.2: reuse when the top non-`none` probability ≥ `tag_reuse`; create only when `none ≥ new_tag_none` and every other option `< new_tag_runner_up`.
- [ ] **Step 4:** Run; expect PASS. **Step 5:** Commit `feat: tag reuse and creation`.

### Task 8: Promotion

**Files:** Create `src/learned/promotion.py`; Test `tests/test_promotion.py`.

**Interfaces:**
- Consumes: `Store`, `Judge.promotion`, `Embedder`, `Thresholds`, `Record`.
- Produces: `promotion.promote(store, judge, embedder, th, touched: list[Record], shadow: bool, now: str) -> list[PromotionEvent]`; `PromotionEvent(ids, scope, personal, same, stable, applied)`; appends one JSON line per event to `promotions.log`.
- Rules: candidate cluster = `touched` record plus active, unpinned records from other projects with cosine ≥ `cluster_sim`; must span ≥ 2 projects. If `same ≥ promote_topic` and `stable ≥ promote_topic`: scope `global` when `personal ≥ promote_global`, else `topic`; otherwise no change. Applied promotion creates one record at the new scope (body of the newest stated member, sources = union, origin project = newest member's) and marks members `status="superseded"`, `promoted_to=<new id>`. Probabilities are stored in members' `judgments["promotion:<sorted ids>"]` and a cached cluster is not re-asked. `shadow=True` logs with `applied=False` and changes nothing else.

- [ ] **Step 1: Write failing tests:** `test_single_project_cluster_never_promoted`; `test_two_projects_personal_high_promotes_global`; `test_shared_not_personal_promotes_topic`; `test_below_threshold_leaves_everything`; `test_shadow_mode_logs_but_does_not_apply`; `test_log_line_has_three_probabilities`; `test_pinned_members_excluded`; `test_cached_cluster_not_judged_twice` (judge call count); `test_promoted_record_keeps_origin_and_union_of_sources`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS. **Step 5:** Commit `feat: cross-project promotion`.

### Task 9: Gist refresh and headless runner

**Files:** Create `src/learned/gist.py`; Test `tests/test_gist.py`.

**Interfaces:**
- Produces: `gist.claude_p(prompt: str, timeout: int = 120) -> str` (runs `claude -p --model haiku --output-format text` with env `LEARNED_EXTRACTOR=1`, working directory a temp dir; raises `RuntimeError` on non-zero exit); `gist.refresh_gists(store, runner, projects: set[str]) -> list[str]` (returns refreshed project names). Writes `projects/<p>/gist.md` from `projects/<p>/meta.json` cwd's `README.md`/`CLAUDE.md` (first 6000 chars each, if present) plus that project's records created or merged this run.

- [ ] **Step 1: Write failing tests** with a fake runner: `test_gist_written_with_readme_and_new_records_in_prompt`; `test_missing_cwd_or_readme_still_works`; `test_runner_failure_keeps_previous_gist`; `test_only_requested_projects_refreshed`; `test_claude_p_sets_extractor_env` (monkeypatch `subprocess.run`, assert env and args).
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement; before relying on flags run `claude -p --help` and adjust the argument list to what the installed CLI accepts. **Step 4:** Run; expect PASS. **Step 5:** Commit `feat: gist refresh`.

### Task 10: Consolidation orchestrator, decay and weekly sweep

**Files:** Create `src/learned/consolidate.py`; Test `tests/test_consolidate.py`.

**Interfaces:**
- Consumes: Tasks 6-9 and 5 (`write_core_md`).
- Produces: `consolidate.run(home: Path, judge, embedder, runner, th, now: str, full: bool = False) -> RunReport`; `consolidate.decay(store, th, now) -> int` (sets `stale=True` on unpinned records with `last_seen` older than `stale_days`, except `project-gist`); `consolidate.recheck_new_tags(store, judge, new_tags: list[str], th) -> int` (Noul `fits_tag` per older record against the new tag only; adds the tag when probability ≥ `tag_reuse`); `consolidate.main(argv) -> int` (scheduled-task entry; builds `JevJudge` and `Embedder`).
- Order per run: lock -> ingest -> tag new records -> promote (shadow while `config["shadow_until"]` ≥ today) -> gists for touched projects -> weekly sweep when `config["last_sweep"]` is ≥ 7 days old -> `write_core_md` -> `store.commit`. `full=True` clears `judgments` and tags on unpinned active records and treats them all as touched.

- [ ] **Step 1: Write failing tests:** `test_full_pipeline_end_to_end_with_fakes` (inbox file in, tagged record out, `core.md` and a git commit exist); `test_lock_prevents_second_run_and_stale_lock_ignored` (> 6 h old); `test_jev_failure_keeps_inbox_and_still_commits_other_work`; `test_missing_api_key_main_returns_zero_and_logs`; `test_decay_marks_stale_not_deleted_and_skips_pinned`; `test_weekly_sweep_only_when_due`; `test_recheck_new_tags_checks_only_new_tag`; `test_shadow_mode_until_date`; `test_full_rerun_clears_cached_judgments`; `test_capture_during_run_not_lost` (file added to inbox after ingest starts remains unprocessed).
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement; the lock is `.lock` created with `os.open(..., O_CREAT|O_EXCL)`. **Step 4:** Run; expect PASS. **Step 5:** Commit `feat: daily consolidation`.

### Task 11: Controls CLI

**Files:** Create `src/learned/cli.py`, `commands/learned.md`; Test `tests/test_cli.py`.

**Interfaces:**
- Produces: `cli.main(argv: list[str], store: Store|None = None) -> int` with subcommands `list [--scope S] [--project P] [--tag T]`, `show <id>`, `forget <id>` (deletes the record, adds its body as a tombstone, commits), `demote <id>` (global -> topic -> project), `pin <id>`, `rerun --full`. `commands/learned.md` is the slash-command wrapper that runs `python -m learned.cli $ARGUMENTS`.

- [ ] **Step 1: Write failing tests:** `test_list_filters_and_output_columns`; `test_show_prints_frontmatter_and_body`; `test_forget_deletes_and_tombstones_so_reingest_skips` (combine with `ingest` and `FakeEmbedder`); `test_demote_moves_one_level_and_keeps_sources`; `test_pin_sets_flag_and_pinned_survives_decay`; `test_unknown_id_returns_nonzero_without_traceback`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS. **Step 5:** Commit `feat: controls cli`.

### Task 12: Recall skill and installer

**Files:** Create `skills/recall/SKILL.md`, `scripts/install.ps1`, `scripts/uninstall.ps1`; Test `tests/test_install_dryrun.py`.

**Interfaces:**
- Produces: `scripts/install.ps1 -DryRun` prints the exact changes without applying them; without `-DryRun` it (1) runs `pip install -e .`, (2) merges `SessionEnd` (timeout 5) and `SessionStart` command hooks into `~/.claude/settings.json` after writing a timestamped backup, with `args` using the absolute `sys.executable` and `-m learned.hooks.session_end|session_start`, (3) registers a daily Task Scheduler task running `python -m learned.consolidate` with StartWhenAvailable, (4) copies `skills/recall` to `~/.claude/skills/recall` and `commands/learned.md` to `~/.claude/commands/`, (5) appends a short "recall" rule to `~/.claude/CLAUDE.md`, (6) runs `Store.init()`. `uninstall.ps1` reverses each.
- The `recall` skill: when to use (before non-trivial project work and before proposing options), the call shape `zvec_grep_search` with `root=<LEARNED_HOME>`, scope order Global -> Topics tagged for the project -> current project, other projects only on request, and the treatment rules from spec §6.

- [ ] **Step 1: Write failing test** `test_install_dryrun_lists_all_six_changes_and_modifies_nothing` (runs PowerShell with `-DryRun` against a temp `HOME`; asserts settings file unchanged and output mentions each of the six items); `test_settings_merge_preserves_existing_hooks` (Python helper `learned.install_util.merge_hooks(settings: dict, session_end: dict, session_start: dict) -> dict`, put in `src/learned/install_util.py`).
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Show the user the `-DryRun` output and the planned `settings.json` diff; apply only after explicit approval. Ask separately for authorization to build the `zvec` index over `~/.claude/learned/`; do not build it otherwise.
- [ ] **Step 6:** Commit `feat: recall skill and installer`.

### Task 13: Evaluation harness and threshold tuning

**Files:** Create `eval/run_eval.py`, `eval/labeled.jsonl` (30-50 facts, built from real transcripts with the user's help); Test `tests/test_eval_runner.py`.

**Interfaces:**
- Produces: `eval.run_eval.main(argv) -> int` with modes `capture` (replays given transcripts through classification and prints precision/recall against labels), `dedupe`, `tag` (reuse rate vs new-tag rate), `promote` (confusion matrix per threshold). Output is a table plus suggested thresholds; it never edits `config.py`.

- [ ] **Step 1: Write failing test** `test_metrics_computation_on_tiny_labeled_set` using `FakeJudge`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS.
- [ ] **Step 5:** With the user, label real facts, run `python eval/run_eval.py capture --transcripts <dir>` on 5+ existing transcripts, review the table, and record the chosen thresholds in the spec's Open Items.
- [ ] **Step 6:** Enable shadow mode for the first week via `config["shadow_until"]`; after the week, review `promotions.log` with the user before turning it off. Run the end-to-end check from spec §10.4 in a fresh session.
- [ ] **Step 7:** Commit `feat: evaluation harness`.
