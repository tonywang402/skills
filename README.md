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
