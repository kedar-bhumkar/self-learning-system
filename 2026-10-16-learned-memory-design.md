# Learned Memory: Design Spec

Date: 2026-10-16
Status: design approved in conversation; written spec awaiting review

## 1. Goal and constraints

Claude should learn how the user works and carry that into every session without being told again. It covers:

- **Decisions and rejections** (e.g. "rejected option X because Y").
- **Working style and preferences.**
- **Project gist**: purpose, architecture, status, for any kind of project.

Constraints:

- Generic: works for any project type.
- Runs in the background; no manual step.
- Local-first: raw transcripts never leave the machine. Only short, redacted candidate windows go to the Jev API.
- Auto-promotion of facts across scopes (user choice "A"), made safe by thresholds, a log, and reversibility.
- Semantic similarity uses real embeddings, not term overlap.

Out of scope (YAGNI): cloud consolidation, hosted memory services (Mem0 cloud), tag hierarchy beyond 255 tags, a UI.

## 2. Prior art considered

| Option | Why not the base |
|---|---|
| Native Auto Memory | Per-project, ~200 lines, no scope/promotion. Kept on; complementary. |
| claudeception (installed) | Extracts how-to skills, not preferences/rejections/project gist. |
| claude-mem | Unstructured session log; AGPL-3.0; repo not verified by us. |
| Mem0 / OpenMemory | Cloud version sends conversations off-machine; benchmarks are vendor-reported. |

Approach chosen: everything local (capture hook + daily local scheduled task), with Jev for typed judgments.

## 3. Storage layout

`~/.claude/learned/` is a local git repo with no remote (excluded from any sync by default).

```
inbox/          raw candidate facts, one JSONL per session
global/         promoted: how the user works
topics/         promoted: shared across projects (hyperframes, windows, jev ...)
projects/<p>/   gist.md, decisions, rejections, status
tags.json       tag vocabulary, each tag with a one-line description
promotions.log  every promotion: what, origin, the three Jev probabilities
core.md         pre-built always-loaded global block
```

One fact is one markdown file with frontmatter:

```yaml
kind: rejection | decision | preference | correction | project-gist | status
scope: project | topic | global
project: <origin project>
tags: [windows, ffmpeg]        # drawn from tags.json
sources: [session ids]
confidence: stated | inferred
status: active | superseded
created: / last_seen:
```

Body: one or two self-contained sentences; rejections include the why.

Rules:

- `stated` beats `inferred` when they conflict.
- `last_seen` drives decay (lower search rank, never deletion).
- Promotion moves a record up a scope and keeps origin and sources. Nothing is destroyed; git and the log allow reversal.

## 4. Capture (SessionEnd hook)

The hook receives the transcript path, starts a detached background process and returns immediately.

**Stage 1, plain code, no model.**
- Parse the session JSONL; keep user turns plus the preceding assistant message for context.
- Treat `AskUserQuestion` answers as high-value (explicit choices).
- Drop tool output, system reminders, skill bodies, pasted content.
- Strip obvious secrets (keys, tokens, emails) by regex before anything leaves the machine.

**Stage 2, Jev.**
- Choice per window: `rejection | decision | preference | correction | none`.
- Separate Noul: "contains a credential or private personal data?" Matching windows are dropped.
- Windows above threshold and not `none` become inbox records (`confidence: stated`, user text kept verbatim).

**Not done at capture:** project gist and status. Jev returns judgments, not prose, so the daily pass generates them.

Safeguards:

- Env flag set on the extractor's own headless runs; the hook skips sessions carrying it (no recursion).
- Skip sessions with fewer than a few user turns.
- Per-session marker makes it idempotent.
- Fails soft: with no API key or network, raw windows go to the inbox unclassified for the daily run. Never blocks or errors a session.
- Per-project opt-out via a `.no-learn` file.

## 5. Consolidation (daily Windows scheduled task)

Driven by new inbox records only. Existing records are read as context, not re-judged. A lock file prevents overlap; a missed day is caught up on the next run.

1. **Ingest and dedupe.** Embed each new record; fetch ~5-10 nearest neighbours (same project first, then global). Jev Noul: "same thing?" and "contradict?". Same: merge, add sources, bump `last_seen`. Contradiction: newer `stated` wins; the older record becomes `superseded`.
2. **Tagging.** One Choice over `tags.json` (up to 255 options, each described) plus `none`. A confident match reuses the tag. A new tag is created only when `none` clearly wins and no existing tag is a close runner-up; a headless `claude -p` call writes its one-line description.
3. **Promotion.** Candidates are embedding clusters that span two or more projects. Three Noul: personal rule not tied to a project; same fact across the cluster; stable. All at or above the threshold (start ~0.85, higher for `global` than `topics`): `global` if personal, `topics` if shared but not personal; otherwise unchanged. Each promotion is appended to `promotions.log` with the three probabilities. Stored probabilities mean unchanged clusters are not re-asked.
4. **Gist and status refresh.** Only for projects with new records: a headless `claude -p` call rewrites `gist.md` from README, CLAUDE.md and the new records.
5. **Commit.** One git commit per run with a summary of merges, promotions and new tags.

**Weekly sweep (cheap):** decay (plain code over `last_seen`), and for any newly created tag, a Noul check of older records against that tag only.

**Shadow mode:** the first week logs promotions without applying them, so thresholds can be checked against real decisions. After that it is fully automatic.

## 6. Recall

**Always-loaded core (SessionStart hook).** Detects the project from the working directory and injects a capped block of about 1.5k tokens: global top rules and rejections (ranked by `stated`, reconfirmations, `last_seen`; pre-built as `core.md`), plus the project's `gist.md`, status and its few most recent rejections and decisions. If the hook fails, the session starts normally.

**On-demand search.** A `recall` skill, backed by a short rule in the global `CLAUDE.md`, calls `zvec_grep_search` over `~/.claude/learned/` before non-trivial project work and before proposing options. Scope order: Global, then Topics tagged for the project, then the current project. Other projects are searched only when the user asks. Results are short snippets carrying frontmatter.

**Treatment of recalled facts.** Background context, not instructions. `stated` outranks `inferred`; old unconfirmed facts are flagged as possibly stale. When a recalled fact changes Claude's action, Claude says so in one line.

**Index.** Building the persistent search index over `~/.claude/learned/` requires explicit user authorization (global rule). Until granted, recall falls back to plain grep, which matches exact words only.

**Native Auto Memory** stays on; it is complementary.

## 7. Jev usage and limits

- API: `POST https://api.typesafe.ai/v1/systemone`; question types `noul`, `choice` (up to 255 options), `score`. One condition per Noul; combine in code.
- Thresholds are starting points, to be tuned on the user's own data. A wrong `global` promotion costs more than a missed one, hence the higher bar.
- Needs a TypeSafe API key; billed per token (no prices on the docs page, so no cost estimate). Usage is proportional to daily activity because only candidate windows and neighbour pairs are judged.
- Facts sent to a third-party API are a privacy tradeoff; mitigated by redaction, the credentials filter and the opt-out file.
- Live docs are the source of truth and should be re-read before writing integration code.

## 8. Controls (`/learned` command or `recall` skill)

- `list` / `show` by scope, tag or project.
- `forget <fact>`: delete plus a tombstone so it cannot be relearned from old transcripts.
- `demote`, `pin`: move a record down a scope; pin so decay and consolidation never touch it.
- `rerun --full`: force a full re-judgment.
- All data is plain markdown in git: hand-editable, `git revert` undoes any run.

## 9. Not stored

Anything derivable from code or git history, and one-off task details. Capture records decisions and rejections, not what happened in the session.

## 10. Testing

1. Replay several existing transcripts through capture; check that real rejections are found and noise stays under a stated bar.
2. Build a labeled set of about 30-50 facts for dedupe, tagging and promotion; tune thresholds; check tag reuse beats tag sprawl.
3. Shadow-mode week, then review the log.
4. End-to-end: fresh session loads the core, and a past rejection is recalled when relevant.

## 11. Build order

1. Store layout.
2. Capture hook.
3. Core injection.
4. Consolidation with Jev.
5. Search and the `recall` skill.
6. Controls.

## 12. Open items

- TypeSafe API key provisioning and where it is stored.
- Authorization for the persistent search index.
- Exact threshold values and the decay window (set from the labeled set).
- Whether this folder becomes a git repo for the implementation (it is not one yet).
