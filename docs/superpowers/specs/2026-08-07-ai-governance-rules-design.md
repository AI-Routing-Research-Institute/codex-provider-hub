# Project AI Governance Rules Design

## Goal

Add repository-wide AI instructions that make release tagging and commit documentation mandatory, explicit, and auditable. The rules apply to every AI agent operating anywhere in this repository.

## Delivery

Create a root-level `AGENTS.md`. Do not duplicate the rules in `README.md` or `CONTRIBUTING.md`, and do not add commit hooks or CI policy checks in this change.

The instructions use mandatory language such as "must", "must not", and "only when". Recommendations or optional wording are not sufficient for release and commit controls.

## Release Milestone Gate

An AI agent must evaluate release readiness whenever it completes any of the following:

- one independently deliverable feature;
- one coherent batch of related fixes;
- a user-requested commit that may complete either of the above.

The agent must not wait for the user to ask whether a release is due.

A release may be proposed only when every condition below is satisfied:

1. The feature or fix batch is complete and has no known in-scope unfinished work.
2. The repository's full automated test suite has just passed against the exact target commit.
3. All intended changes are committed.
4. The target commit is on `main` and has been pushed to `origin/main`.
5. The working tree has no unintended tracked or untracked changes.
6. Release notes can state the user-visible changes, compatibility impact, and verification evidence.
7. The proposed semantic version tag does not already exist locally or remotely.

If any condition is false or cannot be verified, the agent must stop the release operation, report the blocker, and must not create or push a tag. Old test output, assumptions, and partial test runs cannot replace fresh evidence.

## Tag Approval And CI/CD Trigger

Before requesting approval, the agent must show:

- the proposed `vX.Y.Z` tag and semantic-versioning rationale;
- the exact target commit hash;
- the commit range since the previous release;
- complete release notes;
- the exact verification commands and results;
- the Windows and macOS GitHub Actions workflows that the tag will trigger.

Generic instructions such as "commit", "push", "continue", or "release when ready" do not authorize tagging. The user must explicitly approve the specific version and target commit. Approval is single-use and cannot be reused for another version or commit.

Only after that approval may the agent create the repository's existing lightweight `vX.Y.Z` tag and push that tag. It must not use `git push --tags` or `git push --all`.

After pushing the tag, the agent must inspect both release workflows and report their URLs, current status, and any failing job or step. Reporting only that CI/CD was triggered is insufficient.

## Commit Requirements

Before every commit, the agent must inspect the complete diff, separate unrelated concerns into different commits, and exclude unrelated untracked files, generated artifacts, credentials, and local state.

Each commit must have:

- a specific Conventional Commit title that describes the behavior changed;
- a detailed body with the exact headings `功能修改`, `影响范围`, and `验证结果`.

The body requirements are:

- `功能修改`: list each user-visible behavior or internal logic change in concrete terms;
- `影响范围`: identify affected modules, interfaces, configuration, compatibility, migration needs, and known risks; explicitly state when an item has no impact;
- `验证结果`: list every command actually run and its result; explicitly disclose tests not run and the reason.

Vague descriptions such as "update code", "fix issue", or "adjust logic" are prohibited. After committing, the agent must report the commit hash and complete commit summary to the user.

## Verification

The implementation is documentation-only. Verify that:

1. `AGENTS.md` exists at the repository root and therefore covers the complete repository.
2. The file contains every mandatory release gate, explicit approval rule, CI/CD follow-up, and commit-body section from this design.
3. No instruction permits automatic tagging without specific user approval.
4. No unrelated file is included in the implementation commit.
