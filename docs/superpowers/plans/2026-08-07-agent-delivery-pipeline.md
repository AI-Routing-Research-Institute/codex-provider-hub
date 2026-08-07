# Agent-Driven Delivery Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce Agent-owned feature branches, permanent change records, compliant commits, required dual-platform PR checks, zero-approval auto-merge, deterministic versioning, and automatic Windows/macOS releases.

**Architecture:** Put all deterministic policy in one standard-library Python module used by Git hooks and GitHub Actions. Keep Agent procedure in a repository-scoped skill and `AGENTS.md`; use immutable TOML-frontmatter change records as the data source for PR policy, semantic versioning, and release notes.

**Tech Stack:** Python 3.11+, unittest, Git hooks, GitHub Actions, GitHub REST API, Markdown with TOML frontmatter

---

### Task 1: Feature Record Model And Policy Core

**Files:**
- Create: `docs/changes/2026-08-07-agent-delivery-pipeline.md`
- Create: `docs/changes/template.md`
- Create: `scripts/team_policy.py`
- Create: `tests/test_team_policy.py`

- [ ] **Step 1: Add failing parser and semantic-version tests**

Test these public interfaces:

```python
from scripts.team_policy import (
    PolicyError,
    bump_version,
    parse_change_record,
    validate_change_record,
)

record = parse_change_record(path)
self.assertEqual(record.metadata["release_bump"], "minor")
self.assertEqual(record.sections["目标"], "建立 Agent 自驱交付流水线。")
validate_change_record(record, required_status="planned")
self.assertEqual(bump_version("v0.1.7", "patch"), "v0.1.8")
self.assertEqual(bump_version("v0.1.7", "minor"), "v0.2.0")
self.assertEqual(bump_version("v0.1.7", "major"), "v1.0.0")
```

Also assert malformed delimiters, missing metadata, invalid enums, empty required sections, and status below the required state raise `PolicyError` with the missing field or section in the message.

- [ ] **Step 2: Run focused tests and confirm import failure**

Run: `.venv-ci\Scripts\python.exe -m unittest tests.test_team_policy -v`

Expected: FAIL because `scripts.team_policy` does not exist.

- [ ] **Step 3: Implement the record parser and SemVer core**

Define:

```python
@dataclass(frozen=True)
class ChangeRecord:
    path: Path
    metadata: dict[str, str]
    sections: dict[str, str]

class PolicyError(RuntimeError):
    pass

def parse_change_record(path: Path) -> ChangeRecord: ...
def validate_change_record(record: ChangeRecord, *, required_status: str) -> None: ...
def parse_version(tag: str) -> tuple[int, int, int]: ...
def bump_version(tag: str, bump: str) -> str: ...
```

Parse text between the first pair of `+++` delimiters with `tomllib.loads`. Require metadata keys `id`, `type`, `release_bump`, and `status`; require the ten exact Markdown sections from the design. Use status ordering `planned < implemented < verified` and bump ordering `none < patch < minor < major`.

- [ ] **Step 4: Add this feature's permanent record and template**

Create the current record with `type = "build"`, `release_bump = "none"`, and `status = "planned"`. Complete target, current state, scope, non-goals, compatibility, risks, and test plan. Leave actual changes, verification, and PR explicitly marked as pending while the status is planned.

Create `docs/changes/template.md` with the same metadata and section structure, using safe example values rather than parser placeholders.

- [ ] **Step 5: Run focused tests**

Run: `.venv-ci\Scripts\python.exe -m unittest tests.test_team_policy -v`

Expected: parser and SemVer tests PASS.

### Task 2: Commit, Branch, And Diff Enforcement

**Files:**
- Modify: `scripts/team_policy.py`
- Modify: `tests/test_team_policy.py`
- Create: `.githooks/pre-commit`
- Create: `.githooks/commit-msg`
- Create: `.githooks/pre-push`
- Modify: `.gitattributes`

- [ ] **Step 1: Add failing policy tests**

Cover:

```python
validate_commit_message(valid_message)  # no exception
with self.assertRaisesRegex(PolicyError, "功能修改"):
    validate_commit_message("✨ feat(core): 新增功能\n")

validate_branch_name("feat/agent-delivery-governance")
with self.assertRaisesRegex(PolicyError, "main"):
    validate_branch_name("main")

validate_staged_paths(["local_proxy/core.py", "docs/changes/x.md"])
with self.assertRaisesRegex(PolicyError, "生成产物"):
    validate_staged_paths(["dist/app.exe"])
```

Assert valid titles match `emoji type(scope): 简体中文描述`, exact body headings are non-empty, product changes require a `docs/changes/*.md` path in the branch diff, and sensitive/generated path patterns are rejected.

- [ ] **Step 2: Run tests and confirm missing-function failures**

Run: `.venv-ci\Scripts\python.exe -m unittest tests.test_team_policy -v`

Expected: FAIL for undefined validators.

- [ ] **Step 3: Implement Git-backed CLI commands**

Add helpers `run_git`, `changed_paths`, `added_change_records`, `validate_commit_message`, `validate_branch_name`, `validate_staged_paths`, and commands:

```text
python scripts/team_policy.py install-hooks
python scripts/team_policy.py pre-commit
python scripts/team_policy.py commit-msg <message-path>
python scripts/team_policy.py pre-push
python scripts/team_policy.py validate-pr --base <sha> --head <sha>
```

`pre-commit` uses `git diff --cached --name-only`; `validate-pr` uses `git diff --name-only <base>...<head>` and validates every commit message in that range. `pre-push` rejects main and tag refs from stdin, fetches `origin/main`, verifies the branch contains it, requires verified records, and invokes full verification.

- [ ] **Step 4: Add hook wrappers**

Each LF-only wrapper uses `#!/bin/sh`, resolves the repository root with `git rev-parse --show-toplevel`, chooses `python3` then `python`, and exits nonzero when no interpreter exists. `commit-msg` forwards `$1`; `pre-push` preserves stdin for ref validation.

Add `.githooks/* text eol=lf` to `.gitattributes`.

- [ ] **Step 5: Verify hooks and policy tests**

Run:

```powershell
.venv-ci\Scripts\python.exe -m unittest tests.test_team_policy -v
.venv-ci\Scripts\python.exe scripts\team_policy.py install-hooks
git config --get core.hooksPath
```

Expected: tests PASS and hooks path is `.githooks`.

### Task 3: Repository Commit Skill And Agent Instructions

**Files:**
- Create: `.agents/skills/git-commit-helper/SKILL.md`
- Create: `.agents/skills/git-commit-helper/agents/openai.yaml`
- Modify: `AGENTS.md`
- Modify: `tests/test_team_policy.py`

- [ ] **Step 1: Add failing repository-asset tests**

Assert the repository skill frontmatter name is `git-commit-helper`, its description triggers for commit/push/PR work, the body prohibits main/tag pushes, and `agents/openai.yaml` references `$git-commit-helper`. Assert `AGENTS.md` requires direct reading of the repository skill, feature branches, records, auto-merge, dual-platform checks, and automatic releases without human approval.

- [ ] **Step 2: Run tests and confirm missing repository skill failure**

Run: `.venv-ci\Scripts\python.exe -m unittest tests.test_team_policy -v`

Expected: FAIL because `.agents/skills/git-commit-helper` is absent.

- [ ] **Step 3: Vendor and tighten the skill**

Use the local skill as the source, preserving emoji/Chinese commit conventions and explicit staging. Replace generic push behavior with the nine repository-specific steps from the design. Require feature record lifecycle, policy commands, PR creation/update, auto-merge, CI inspection, and no direct main/tag push.

- [ ] **Step 4: Rewrite AGENTS workflow**

Replace the old user-approved tagging flow. Require bootstrap hooks, current-main fetch, feature branch creation before file edits, planned/implemented/verified record transitions, repository skill usage for all Git delivery operations, no direct main push, zero-human-approval PR auto-merge, and Agent-selected automatic release bumps.

- [ ] **Step 5: Validate skill and focused tests**

Run the system skill validator against `.agents/skills/git-commit-helper`, then run `tests.test_team_policy`. Expected: both PASS.

### Task 4: Dual-Platform Pull-Request Gate

**Files:**
- Create: `.github/workflows/pr-policy.yml`
- Modify: `tests/test_team_policy.py`

- [ ] **Step 1: Add failing workflow contract tests**

Assert workflow trigger `pull_request`, permissions `contents: read`, stable jobs `policy`, `tests-windows`, and `tests-macos`, full checkout history, Python 3.13, policy validation with base/head SHAs, Python unittest discovery, JS syntax checks, and all `tests/*.test.js` files.

- [ ] **Step 2: Implement workflow**

Use Ubuntu for policy, `windows-latest` for Windows tests, and `macos-latest` for macOS tests. Install `requirements-status.txt` on both test platforms. Do not use `pull_request_target` and do not expose write permissions.

- [ ] **Step 3: Run focused workflow tests**

Run: `.venv-ci\Scripts\python.exe -m unittest tests.test_team_policy -v`

Expected: PASS.

### Task 5: Ruleset Configuration

**Files:**
- Modify: `scripts/team_policy.py`
- Modify: `tests/test_team_policy.py`

- [ ] **Step 1: Add failing payload tests**

Test `build_ruleset_payload()` includes `refs/heads/main`, enforcement `active`, pull requests with zero approvals, required checks `policy`, `tests-windows`, `tests-macos`, and non-fast-forward/deletion protections. Test dry-run JSON contains no token.

- [ ] **Step 2: Implement configuration commands**

Add:

```text
python scripts/team_policy.py configure-ruleset --repo OWNER/REPO --dry-run
python scripts/team_policy.py configure-ruleset --repo OWNER/REPO
python scripts/team_policy.py verify-ruleset --repo OWNER/REPO
```

Use `urllib.request` and `GITHUB_TOKEN`. Create or update a ruleset named `agent-delivery-main`; read it back and compare enforcement, target, conditions, and required checks. Never print the token.

- [ ] **Step 3: Run focused tests**

Run: `.venv-ci\Scripts\python.exe -m unittest tests.test_team_policy -v`

Expected: PASS without network access.

### Task 6: Automatic Versioning And Release Notes

**Files:**
- Modify: `scripts/team_policy.py`
- Modify: `tests/test_team_policy.py`
- Create: `.github/workflows/auto-release.yml`
- Modify: `.github/workflows/windows-release.yml`
- Modify: `tests/test_windows_release.py`
- Modify: `tests/test_macos_release.py`

- [ ] **Step 1: Add failing release-plan and workflow tests**

Test highest bump selection, records added since a base tag, `none` no-release behavior, release-note grouping, tag collision failure, and JSON output containing `release`, `tag`, and `notes_path`. Assert auto-release uses main push, concurrency `release-main`, `contents: write`, `actions: write`, exact tag push, and explicit dispatch of both existing workflows.

- [ ] **Step 2: Implement release commands**

Add `release-plan` and `release-notes` commands. Select only records added by `git diff --diff-filter=A --name-only <tag>..<head> -- docs/changes`; require verified status; choose the maximum bump; render title, summary, compatibility, risks, and verification from each record.

- [ ] **Step 3: Implement auto-release workflow**

On main push, fetch all tags, call `release-plan`, skip safely for `release=false`, check local and remote tag absence, create/push one lightweight tag, and run:

```text
gh workflow run windows-release.yml -f tag="$TAG"
gh workflow run macos-release.yml -f tag="$TAG"
```

- [ ] **Step 4: Generate Windows release notes from records**

Before `gh release create`, run `team_policy.py release-notes` against the prior tag and current release tag, write `.build/release-notes.md`, and pass it to `--notes-file`. Keep `packaging/release-notes.md` only as the recovery fallback when no record can be selected.

- [ ] **Step 5: Run release and policy tests**

Run:

```powershell
.venv-ci\Scripts\python.exe -m unittest tests.test_team_policy tests.test_windows_release tests.test_macos_release -v
```

Expected: PASS.

### Task 7: Complete Record, Full Verification, And Delivery

**Files:**
- Modify: `docs/changes/2026-08-07-agent-delivery-pipeline.md`
- Modify: `docs/superpowers/plans/2026-08-07-agent-delivery-pipeline.md`

- [ ] **Step 1: Complete the feature record**

Set status to `implemented`, list every changed file and behavior, then run the complete suite. After success set status to `verified`, record exact commands/results, and leave PR as `pending` until creation.

- [ ] **Step 2: Run complete verification**

Run:

```powershell
.venv-ci\Scripts\python.exe -m unittest discover -s tests -p test_*.py
node --check proxy_static/app.js
node --check provider_status/static/app.js
Get-ChildItem tests -Filter *.test.js | ForEach-Object { node --test $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
git diff --check
```

Expected: all Python and Node tests PASS and diff check exits 0.

- [ ] **Step 3: Commit logical changes through the repository skill**

Create small commits for policy core/hooks, repository governance assets, PR CI/ruleset, and automatic release. Every subject starts with the mapped emoji and every body contains `功能修改`, `影响范围`, and `验证结果`.

- [ ] **Step 4: Rebase, reverify, push, and create PR**

Fetch `origin/main`, rebase the feature branch, rerun complete verification, push only `feat/agent-delivery-governance`, create a PR, update the feature record PR URL if possible without invalidating immutable release data, and enable auto-merge.

- [ ] **Step 5: Configure and verify branch ruleset**

With repository-administration credentials, apply `agent-delivery-main`, read it back, and verify direct main pushes are blocked, approvals are zero, and all three required checks are configured. If credentials are unavailable, report this as a hard blocker rather than claiming enforcement is complete.
