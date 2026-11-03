---
name: code-review-workspace
description: >-
  Build an offline, single-file HTML code-review workspace from a real git diff: a Pyramid-Principle
  summary of what changed and why, GitHub-style diffs grouped by technical domain and logical step,
  a per-file panel explaining every changed method, component or test (改了什么 / 为什么), line
  comments saved in the browser, and Copy-all Markdown export. Use this skill whenever the user wants
  changes prepared, presented or explained for code review: the committed and uncommitted changes
  of the current coding session versus origin/main, a specific commit, or a GitHub / GitHub
  Enterprise pull-request URL, even if they only say '生成 code review HTML', '整理改动给 reviewer 看',
  '为这个 PR 做评审页面', '解释一下你改了什么方便 review' or 'make a review page for this branch'.
  Do not use it to post comments to GitHub, approve or judge a PR, write a PR description or commit
  message, or apply review feedback to the code.
compatibility: >-
  Requires git 2.25+ and Python 3.9+ (standard library only); an authenticated gh CLI for the PR host
  when given a pull-request URL; a browser automation tool (e.g. Playwright) for the smoke test.
metadata:
  version: "1.3"
---

# Code Review Workspace

Produce one offline HTML page that lets reviewers grasp a change top-down, read GitHub-style diffs, open a per-file "what changed / why" panel, and add or edit line comments (stored in localStorage, exported with Copy all).

`scripts/review_workspace.py` owns everything mechanical: scope, diff, hunks, stats, statuses, HTML, validation. You own the analysis and the writing in `content.json`. Run the script as a black box (`--help` lists options) and never read `assets/review-shell.html`.

## Hard rules

1. **The git diff is the only source of change facts.** Describe only what `collect` wrote to `diff.patch`, never what you remember doing in this session: edits may have failed, been reverted, or never been saved.
2. **Intent sources**: the changed files themselves (code, comments, docs), commit messages and the PR title/body (in `manifest.json`), and the user's own requirement statements in this session — never your own earlier messages. Mark anything no source supports as an inference: start that sentence with `推断：` (en: `Inferred: `), one claim per sentence. This applies to every text, not only panels. When one inference covers a whole group, state it once in the group summary instead of in every unit.
3. **Before = merge base, after = review head.** Read the original code from the merge base that `collect` prints, not from the current tip of `origin/main`, which may contain commits this change never saw.
4. **Change nothing but the output HTML.** No edits to product code, tests or config; no commits, pushes or index changes (`collect` uses a temporary index). Temporary files live in the work directory and are removed by `cleanup`.
5. **Ask instead of guessing** when the scope is unclear (which commits or PR the user means) or `collect` fails. Never pick another base or commit on your own.
6. The page stays offline (no CDNs, fonts, APIs) and must never contain secrets.
7. **The page shows no evidence.** Evidence lives only in `ledger.md`. No page text cites `path:line`, `#L12` or `L10–L20`, or carries an `Evidence:` / `证据：` note; the diff under each file is the evidence. `render` rejects such citations.

## Workflow

Copy this checklist and tick it off:

```
- [ ] 1. Collect the scope
- [ ] 2. Analyze before → after per unit
- [ ] 3. Plan the pyramid
- [ ] 4. Write content.json
- [ ] 5. Render until it passes
- [ ] 6. Smoke-test in a browser
- [ ] 7. Clean up and report
```

`<skill>` below is the directory that contains this SKILL.md.

### 1. Collect the scope

Run `git -C <repo> status --porcelain --untracked-files=all` and keep the output for step 7. Then:

| The user gives | Run |
|---|---|
| Nothing specific, in a coding session ("review my changes") | `python3 <skill>/scripts/review_workspace.py collect --repo <repo>` |
| A commit or branch tip | `… collect --repo <repo> --head <rev>` |
| A PR URL (github.com or GitHub Enterprise) | `… collect --pr <url> --repo <workspace>` |

- Working-tree mode diffs merge-base(origin/main, HEAD) against the final working-tree state: every branch commit plus staged, unstaged and untracked files (whether something is staged does not matter).
- PR mode diffs the PR head against the PR's recorded base commit, exactly like its "Files changed" tab, merged PRs included. A repository that is not local is cloned (blobless) into the work directory.
- `collect` prints the scope, commits, untracked files, every changed file with status, stats and test pairing (`[test of …]`), any `NOTE` (for example a base that could not be fetched), the work directory, and the output path. Always re-run with the same `--work`.
- `collect` stops before reading any untracked file that looks like a secret (`.env*`, keys, credential files). Re-run with `--exclude '<glob>'`; use `--allow '<glob>'` only after the user confirms the file is safe to show.
- Also inspect "Untracked included". Leave out, with `--exclude`, files the user clearly does not mean to ship: build output, logs, `tmp*` files, editor or OS files, personal notes. When unsure, ask the user.
- If `gh` reports an authentication error, ask the user to run `gh auth login --hostname <host>` themselves. Never handle tokens.

### 2. Analyze before → after, per unit

For each changed file, in dependency order (contracts and types → implementation → consumers → tests):

1. Read its section of `<work>/diff.patch`. For a large patch, `grep -n '^diff --git' <work>/diff.patch` gives each file's offset.
2. Map every hunk to its unit: a method or function, a component, a test case, or the smallest contract item (endpoint parameter, DTO field, config key, schema property, dependency version, translation key, CSS rule).
3. First understand the **before** code: read the whole unit with `git -C <repo> show <mergeBase>:<old path>` — what it did and why it was shaped that way. Then read the **after** code: the working-tree file in working-tree mode, otherwise `git -C <repo> show <headSha>:<path>`. Follow a caller or callee only when you need it to state the effect. Framework or library behavior may be checked read-only (dependency sources in the build cache, official docs). For binary or mode-only changes, record what changed (type, size, mode) without reading the content.
4. Create `<work>/ledger.md` and add one line per unit: `unit | before | after | behavioral difference | evidence (path:line, or artifact + path/URL)`.

The ledger feeds the whole page:

- The 行为变化 table gets the differences a user, caller or operator can observe at runtime (API, UI, persistence, messages, configuration). Build- or dev-environment-only changes get a row only if they change what ships; when you cannot tell, leave the row out and list the question under "Not verified".
- Each file's panel is built from its ledger lines, without the evidence column.
- Risks are the differences whose correctness rests on an assumption.

### 3. Plan the pyramid for 代码改动

First read [references/content-contract.md](references/content-contract.md): the structure you plan here is exactly the structure you will write.

Levels: `conclusion` → `changes.summary` → domains (`points`) → logic groups (`title`, `summary`) → files (`role`) → panel units.

- **以上统下 — every level summarizes the level below.** Build bottom-up: units → role → group summary → domain points → changes summary → conclusion. Then read top-down: each parent abstracts all of its children (it does not list them) and claims nothing they do not support.
- **归类分组 — one logical category per group.** Domains are technical domains. Use this vocabulary and order, skipping absent ones:
  - **Backend**, **Frontend**: application code plus the module's own build and dependency files (`backend/build.gradle.kts`, `frontend/package.json`, `yarn.lock`).
  - **Runtime Config**: configuration the running application reads (`application*.properties`/`.yml`, env files, flag stubs).
  - **Database**, **Messaging**: migrations and schemas; topics, events and their contracts.
  - **Infrastructure**: deployment (Terraform, Helm, Dockerfile, Kubernetes).
  - **Build & CI**: repository-level build, pipelines, scripts and toolchain files (`settings.gradle.kts`, CI workflows, `.java-version`, `.nvmrc`).
  - **Documentation**, then **Tests**.

  A file outside any module belongs to the domain of what uses it. Add another short English name (such as `Assets`) only when nothing fits, and place it before Documentation.

  Inside a domain, a group holds files whose changes serve the same purpose or step, never merely the same directory. When one change crosses domains, show the chain in the `flow` section (实现链路) and keep the domains technical. No empty domains or groups; every file appears exactly once.
- **逻辑递进 — a clear order inside every group.** Order groups within a domain, and files within a group, by one principle:
  - dependency order (contract → implementation → consumer/presentation) by default;
  - runtime flow (input → processing → output) when the change follows a request or event;
  - importance (most impactful first) for independent changes.

  Peers with no inherent order (six parallel client configurations, say) follow path order.
- **Tests sit with their implementation.** A test goes in the same group, after the implementation it verifies; the same applies to deleted tests. Only tests whose implementation is not in the diff go to `Tests`, the last domain. `render` enforces collect's `[test of …]` pairing; override it with `pairedWith`, which takes one path or a list when a test verifies several files.

### 4. Write content.json

`<work>/content.json` starts as a skeleton that lists every changed path once, all in one placeholder group. Rewrite it to match your plan; only the field names are fixed. Every text is a `{"zh", "en"}` pair with the same meaning.

Panel rules — the units of each file (改了什么 / 为什么):

- **Audience and goal**: the developers in this review, so they can judge each change against its intent.
- **Scope**: only this file's changes. Mention another file only to explain a reason.
- **Granularity**: one unit per changed method, component, test case or smallest contract item.
  - `name` is the exact identifier (`NotificationService.toSearchRequest`, or a test's name as written). For non-code changes use the section heading, the key, or the first words of the changed text; `file mode`; `binary content`; `old path → new path` for a rename or copy; or the file name for a whole-file add or delete.
  - The page's diffs show only hunk headers and code lines: no rename, copy, mode, binary or "no newline" notes. Such a change must therefore be stated in its unit.
  - Fold import-only and formatting-only hunks into the unit that needs them. Trivial lifecycle methods share one unit (`setUp / tearDown`).
  - Generated files, lock files, translations and API specs get units too: the added parameter, the bumped dependency, the new key.
- **Voice**: write for a smart reader who has not read the code. Be direct, precise and concrete (types, values, branches, before → after), with enough context to stand alone. Quote code, identifiers and paths in backticks; that is the only place an `en` text may contain Chinese. No filler, no line-by-line restating of the diff.
- **`change`** = what is different now and its observable effect. **`reason`** = why: the problem solved, the requirement, the constraint, or the risk avoided.

Other text: `conclusion` is one sentence (≤ 90 Chinese characters); `role` is one short line; a group `summary` says why the group exists.

### 5. Render

Run `python3 <skill>/scripts/review_workspace.py render --work <work>`. It validates the contract, file coverage, test placement, translations and the no-evidence rule, renders the HTML, and self-checks file counts, statuses, hunks and `+/-` against git.

Fix every listed error and re-run until it prints `Rendered`. Fix warnings too, unless you have a reason to keep the text as is. Keep the "Report facts" lines it prints.

### 6. Smoke-test in a browser

1. Serve the directory that contains the HTML: `python3 -m http.server <port> --bind 127.0.0.1 --directory <that dir>`. In-editor browsers often block `file://`, and a separate origin keeps test data away from real review comments.
2. Wait until `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:<port>/<file>` prints `200`.
3. Load [scripts/smoke_test.js](scripts/smoke_test.js) into the page (Playwright: `page.addScriptTag({ path })`; otherwise evaluate the file's text).
4. Run `await window.__crwSmoke(phase)` for each phase in order:
   1. `seed`
   2. Reload the page and load the script again.
   3. `edit` (inline and list editing, cancel, validation, language switching, metadata and export)
   4. Reload the page and load the script again.
   5. `verify` (edited comment persists after reload, copy, locate and delete)
   6. `layout` at 1440×900, then `layout` again at 390×844
   7. `cleanup`
5. Each phase returns `{ ok, failures, checks }`; `checks` names every verified behavior, and a layout failure names the overflowing text. After a fix: run `cleanup`, re-render, reload, and start again from `seed`.
6. The test captures Copy all in the page and never writes to the system clipboard.
7. Stop the server. Without any browser tool, say so in the report instead of claiming success.

### 7. Clean up and report

Run `python3 <skill>/scripts/review_workspace.py cleanup --work <work>`. Then confirm that `git status --porcelain --untracked-files=all` differs from step 1 only by the new HTML. Report with this template, pasting render's "Report facts" lines:

```markdown
**Review workspace**: <path> (<file:// URL>)
- Scope: …          ← render's Report facts
- Size: …           ← render's Report facts
- Pyramid: <conclusion>
  - <Domain>: 01 <group> → 02 <group> …
- Untracked files excluded: …   ← render's Report facts
- Smoke test: seed <✓/✗> · edit <✓/✗> (inline/list, cancel, validation, metadata, export) · verify <✓/✗> (edited commentRestored, copyAllMarkdown, copyAllButton, locate, delete) · layout 1440×900 <✓/✗> · 390×844 <✓/✗> · cleanup <✓/✗>
- Storage keys: …   ← render's Report facts
- Inferences to confirm: <the 推断 statements, or "none">
- Not verified: <items, or "none">
- Repository: no tracked file changed; only <path> was added.
```

## Gotchas

- In working-tree mode, `git show HEAD:<path>` misses uncommitted edits; read the working-tree file for "after".
- The page translates by exact zh text, so one zh string must keep one English translation everywhere. `render` lists conflicts.
- Untracked files under `analysis/` (earlier reviews) are excluded automatically; tracked files there are still reviewed.
- Re-running `collect` with the same `--work` refreshes an untouched skeleton but keeps an edited `content.json`; `render` then reports files that were added to or removed from the diff.
- Long unbroken identifiers are fine in text. The page wraps them, and `layout` reports any overflow it finds.
- Fixed labels from the template stay English in both languages by design: `Viewed`, `Copy all`, `Review comments`, `Ask about this diff`, `Review Workspace`, and the file/hunk counts. Domain names are English too.
