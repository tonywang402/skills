# skills

Reusable workflows for AI coding agents. Each skill lives in its own directory, with execution instructions in its `SKILL.md`.

## Available skills

| Skill | Purpose | Documentation |
| --- | --- | --- |
| `code-review-workspace` | Generate an offline, single-file HTML review workspace from a real Git diff to help reviewers understand changes and organize feedback | [SKILL.md](code-review-workspace/SKILL.md) |

### Code Review Workspace

- **Start with the big picture:** read the main conclusion, behavior changes, and technical-domain summaries organized using the Pyramid Principle.
- **Inspect the details:** explore GitHub-style diffs and per-file explanations of what changed and why, down to individual methods, components, and tests.
- **Organize feedback:** add line comments stored in the current browser and export them as Markdown with `Copy all`. Comments are not automatically posted to GitHub.
- **Read offline:** the generated HTML uses no CDNs, external fonts, or APIs and supports switching between Chinese and English content.

## Installation

The skills CLI currently requires Node.js 22.20 or later. Run it directly with `npx`; a global CLI installation is not required. See the [skills CLI reference](https://github.com/vercel-labs/skills/blob/main/docs/commands.md) for all options.

Run this command from the project where you want to use the skill:

```bash
npx skills add tonywang402/skills --skill code-review-workspace
```

Follow the prompts to choose a target agent and installation scope. Skills are installed to the current project by default; add `-g` to make them available across projects.

```bash
# Install for OpenCode in the current project
npx skills add tonywang402/skills --skill code-review-workspace -a opencode

# Install for Codex across projects
npx skills add tonywang402/skills --skill code-review-workspace -a codex -g

# List skills available in this repository without installing
npx skills add tonywang402/skills --list
```

Installation places the skill files where the selected agent can load them. To generate a review page, ask the agent to run the skill's workflow.

## Usage

After installation, open the target code repository in your agent and explicitly request `code-review-workspace`. The examples below are prompts for the agent, not terminal commands.

**Current branch and working tree**

> Use code-review-workspace to generate a code review HTML page for this branch against origin/main, including committed, staged, unstaged, and untracked changes.

By default, the scope runs from the merge base of `origin/main` and `HEAD` to the final working-tree state. It can include changes made before the current conversation. Specify a narrower scope if needed.

**A specific branch or commit**

> Use code-review-workspace to generate a review page for the committed state of feature/example against origin/main. Exclude uncommitted working-tree changes.

`--head <rev>` reviews the cumulative diff from the merge base to that revision. It does not mean only the changes introduced by that single commit relative to its parent.

**A GitHub or GitHub Enterprise pull request**

> Use code-review-workspace to generate an offline review page for this PR: https://github.com/OWNER/REPO/pull/123

Replace the example URL with a real PR URL. PR mode uses the PR's recorded base commit and head to determine the review scope.

### Generating and reading a workspace

1. The agent collects the real diff, analyzes behavior before and after the changes, and writes structured explanations.
2. The rendering script validates file coverage, grouping, and bilingual content, then produces one HTML file.
3. The agent checks comment persistence, Markdown export, and desktop and mobile layouts in a browser. If no browser tool is available, it should report those checks as not verified.
4. Open the HTML path from the agent's report. Read the conclusion and behavior changes first, then inspect grouped diffs, add line comments, and export feedback as Markdown.

The default output is under `analysis/<ticket-or-PR-or-branch>/..._code-review.html` in the target repository. Use the exact path reported by the agent.

### Requirements and boundaries

| Scenario | Requirements |
| --- | --- |
| Local code analysis and HTML generation | A skill-capable AI agent, Git 2.25+, and Python 3.9+; the Python script uses only the standard library |
| PR URL analysis | Additionally requires the `gh` CLI authenticated for the relevant GitHub or GitHub Enterprise host |
| Complete browser smoke checks | A browser automation tool available to the agent, such as Playwright |
| Reading a generated page | A browser; the page itself works offline, while external source links still require network access and repository permissions |

The skill presents changes and organizes feedback. It does not replace human review, post GitHub comments, approve PRs, or modify product code. Generated pages contain real code, so inspect their contents before sharing. Comments are stored in the current browser origin's localStorage and do not automatically sync across devices. Moving the file or changing its URL may also make existing comments unavailable.

See [SKILL.md](code-review-workspace/SKILL.md) for the full workflow and the [content contract](code-review-workspace/references/content-contract.md) for the page's content structure.

## Updating and removing

Run these commands from the project where the skill is installed:

```bash
# List skills installed in the current project
npx skills list

# Check for updates; this does not validate skill content
npx skills check

# Update this skill in the current project
npx skills update code-review-workspace -p

# Remove it from the current project
npx skills remove code-review-workspace
```

If you installed with `-g`, use the same scope when updating or removing:

```bash
npx skills update code-review-workspace -g
npx skills remove code-review-workspace -g
```

Installed copies need an update after the source files change on GitHub. Removing an installed skill does not delete its source from this repository.

## Maintenance and feedback

When adding a skill, create a separate directory with a `SKILL.md`. Its `name` should match the directory name, and its `description` should explain both what it does and when to use it. Include `scripts/`, `references/`, and `assets/` as needed, following the [Agent Skills specification](https://agentskills.io/specification). Keep the skill list and examples in this README up to date. After publishing changes, use `npx skills add tonywang402/skills --list` to check that the remote skills can be found.

When changing `code-review-workspace`, verify realistic use cases against its [instructions](code-review-workspace/SKILL.md) and [content contract](code-review-workspace/references/content-contract.md). Page changes should be checked on desktop and mobile layouts, including comment and export behavior. `skills check` does not replace these checks.

Report problems in [Issues](https://github.com/tonywang402/skills/issues) with the agent, operating system and tool versions, review scope, reproduction steps, and sanitized error messages. Do not attach credentials, customer data, or code and review HTML that you are not authorized to share.
