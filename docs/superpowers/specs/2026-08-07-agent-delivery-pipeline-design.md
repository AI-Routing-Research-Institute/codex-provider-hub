# Agent-Driven Delivery Pipeline Design

## Goal

Turn the repository's AI guidance into an enforceable delivery pipeline for a three-person team. Normal feature delivery must require no human review or release decision: an Agent plans the change, maintains a permanent feature record, develops on a feature branch, commits through the repository skill, opens a pull request, waits for required Windows and macOS checks, enables auto-merge, chooses the semantic version bump, and triggers both release workflows.

## Non-Goals

- Do not build or host a separate GitHub App.
- Do not require a human pull-request approval.
- Do not rely on an Agent's unverified statement as a policy check.
- Do not make local hooks the only enforcement layer.
- Do not delete historical feature records after release.

## Enforcement Model

Use four complementary layers:

1. `AGENTS.md` defines the mandatory Agent workflow before any file modification.
2. A repository-scoped `git-commit-helper` skill defines the only permitted staging, commit, push, pull-request, and auto-merge workflow.
3. Versioned Git hooks provide immediate local feedback and block obvious policy violations.
4. GitHub required checks and a main-branch ruleset enforce the same policy remotely, even when hooks are absent or bypassed.

Local hooks are advisory from GitHub's perspective because they can be skipped with `--no-verify`. Remote required checks are authoritative. The repository ruleset must forbid direct updates to `main`, require pull requests, require all named policy/test checks, require zero approving reviews, and forbid force pushes and deletion.

## Repository Skill

Vendor the existing local skill into `.agents/skills/git-commit-helper/` with its `SKILL.md` and `agents/openai.yaml`. Extend it with repository-specific mandatory steps:

1. Refresh `origin/main` and create or reuse a non-main feature branch.
2. Create a feature record before modifying product code.
3. Inspect staged and unstaged changes and split independent concerns.
4. Stage explicit paths only.
5. Use a Simplified Chinese `emoji type(scope): description` title.
6. Include exact commit-body headings `功能修改`, `影响范围`, and `验证结果`.
7. Run the repository policy and verification commands before push.
8. Push only the feature branch, create or update a pull request, and enable auto-merge.
9. Never push `main`, create tags manually, use `git push --all`, or use `git push --tags`.

`AGENTS.md` must explicitly require reading this repository skill for every request that creates commits, pushes code, or manages a pull request. If the host does not auto-discover repository skills, the Agent must read the file directly.

## Permanent Feature Records

Store one permanent Markdown record per independently deliverable feature or coherent fix batch under `docs/changes/`. Use filenames matching `YYYY-MM-DD-short-slug.md`.

Each file starts with TOML frontmatter so Python's standard-library `tomllib` can parse it without another dependency:

```toml
+++
id = "2026-08-07-agent-delivery-pipeline"
type = "feature"
release_bump = "minor"
status = "planned"
+++
```

Allowed values:

- `type`: `feature`, `fix`, `refactor`, `performance`, `build`, `docs`, or `chore`.
- `release_bump`: `major`, `minor`, `patch`, or `none`.
- `status`: `planned`, `implemented`, or `verified`.

The Markdown body must contain these exact sections:

- `## 目标`
- `## 现状`
- `## 设计范围`
- `## 非目标`
- `## 兼容性`
- `## 风险`
- `## 测试计划`
- `## 实际改动`
- `## 验证结果`
- `## PR`

The Agent creates the record with `status = "planned"` and completes the first seven sections before product-code edits. It changes the status to `implemented` only after the intended code and tests exist, and to `verified` only after fresh full verification. The PR section records the final pull-request URL. Files are never deleted after release. Later corrections use a new feature record rather than rewriting a released record.

## Policy And Hook Implementation

Implement one standard-library Python policy module, `scripts/team_policy.py`, with small command-oriented entry points shared by local hooks, CI, and tests:

- `install-hooks`: set the local repository's `core.hooksPath` to `.githooks`.
- `pre-commit`: reject commits on `main` or `master`, sensitive/generated staged files, and product changes with no feature record in the branch diff.
- `commit-msg <path>`: validate the emoji Conventional Commit title and required detailed body headings.
- `pre-push`: reject direct main/tag pushes, require the branch to contain current `origin/main`, validate verified feature records, and run full verification against the pushed HEAD.
- `validate-pr --base <sha> --head <sha>`: enforce branch-diff policy in GitHub Actions.
- `release-plan --base-tag <tag> --head <sha>`: read feature records added since the latest semantic tag, select the highest requested bump, calculate the next version, and emit machine-readable JSON.
- `release-notes --base-tag <tag> --head <sha> --output <path>`: render release notes from the same immutable records.

Create executable wrappers in `.githooks/pre-commit`, `.githooks/commit-msg`, and `.githooks/pre-push`. Wrappers locate the repository root and invoke the policy module with the active Python interpreter. A failed or missing Python invocation blocks the Git operation with an actionable error.

Full verification runs:

- Python unittest discovery;
- JavaScript syntax checks for both application scripts;
- every `tests/*.test.js` Node test file.

## Pull-Request CI And Auto-Merge

Add a pull-request workflow with stable required-check names:

- `policy`: Ubuntu job validating commit messages, branch diff, and feature records.
- `tests-windows`: Windows job running the full verification suite.
- `tests-macos`: macOS job running the full verification suite.

Use `pull_request`, not `pull_request_target`, so untrusted branch code receives no elevated secrets. The Agent creates the PR and enables GitHub auto-merge after push. The ruleset requires the three named checks and zero approvals; GitHub merges only after they pass.

Provide a deterministic ruleset configuration command in `scripts/team_policy.py`. It reads an explicit GitHub token from the environment, submits the repository ruleset through the GitHub API, and supports a dry-run mode. Applying the ruleset is a one-time privileged operation. The Agent must read the resulting remote ruleset back and verify every required rule before claiming enforcement is active.

## Automatic Versioning And Release

Add an `auto-release` workflow triggered by pushes to `main`. It has a `release-main` concurrency group and performs these steps:

1. Fetch complete history and tags.
2. Find the latest semantic version tag reachable from `main`.
3. Read feature records added since that tag.
4. Exit successfully without a release when every record requests `none` or no new record exists.
5. Select the highest requested bump using `major > minor > patch`.
6. Calculate the next strict SemVer version and generate release notes.
7. Verify the target tag does not exist locally or remotely.
8. Create and push exactly one lightweight `vX.Y.Z` tag for the current `main` commit.
9. Explicitly dispatch `windows-release.yml` and `macos-release.yml` with that tag.

The coordinator uses `GITHUB_TOKEN` to push the tag and explicit `workflow_dispatch` calls for the release workflows. This avoids relying on a tag-push event created by `GITHUB_TOKEN`, which GitHub suppresses from recursively starting ordinary workflows. Grant only `contents: write` and `actions: write` to the coordinator.

Update the Windows release workflow to generate the release body from feature records at the tag. macOS continues attaching its artifacts to the release created by Windows. Existing direct tag and manual dispatch support remains available for recovery, but Agents are prohibited from using it during the normal path.

## Semantic Version Rules

The Agent chooses and explains `release_bump` while creating the feature record:

- `major`: incompatible public behavior, configuration, stored data, or integration contract.
- `minor`: backward-compatible user-visible feature or substantial new capability.
- `patch`: backward-compatible defect fix or user-visible performance/reliability improvement.
- `none`: internal docs, tests, plans, or maintenance that does not warrant a published artifact.

CI validates the value but does not reinterpret it. When a release includes multiple records, the highest bump wins and all records appear in the generated notes.

## Failure Handling

- Stale branch: pre-push and CI fail; the Agent rebases on current `origin/main`, reruns verification, and updates the PR.
- Missing or incomplete feature record: commit/push/CI fail with the exact missing field or section.
- Invalid commit message: commit-msg or PR policy fails and prints the required format.
- Test failure: auto-merge remains blocked; the Agent fixes the branch and pushes a new commit.
- Release race: workflow concurrency serializes runs; the later run refetches tags before calculating its version.
- Existing tag: release stops without overwriting or force-pushing.
- Workflow dispatch failure: release coordinator fails and reports which platform was not dispatched.
- Missing administrative credentials: ruleset application stops and reports the required repository administration permission; it must not claim branch protection is active.

## Testing

Add focused unit tests for frontmatter parsing, required sections, staged-path policy, branch restrictions, commit-message validation, bump precedence, SemVer calculation, release record selection, release-note rendering, and ruleset payload generation.

Add repository tests that assert:

- all three hook wrappers exist and invoke the policy module;
- the repository skill and UI metadata are valid;
- PR CI exposes the three stable check names;
- auto-release has concurrency, minimal permissions, tag collision checks, and explicit dispatches for both release workflows;
- release workflows consume generated release notes;
- `AGENTS.md` requires the repository skill, feature branches, permanent records, PR auto-merge, and automatic release.

Run the full existing Python and Node suites after the focused tests pass.
