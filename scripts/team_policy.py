from __future__ import annotations

import re
import argparse
import json
import os
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


REQUIRED_SECTIONS = (
    "目标",
    "现状",
    "设计范围",
    "非目标",
    "兼容性",
    "风险",
    "测试计划",
    "实际改动",
    "验证结果",
    "PR",
)
ALLOWED_TYPES = {
    "feature",
    "fix",
    "refactor",
    "performance",
    "build",
    "docs",
    "chore",
}
STATUS_ORDER = {"planned": 0, "implemented": 1, "verified": 2}
BUMPS = {"none", "patch", "minor", "major"}
VERSION_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
COMMIT_TITLE_RE = re.compile(
    r"^(?:🎉 init|✨ feat|🐞 fix|🦄 refactor|🌈 style|⚡️? perf|📃 docs|🧪 test|🐳 chore|🔧 build)"
    r"\([a-z0-9_-]+\): (?=.*[\u4e00-\u9fff]).+$"
)
COMMIT_SECTIONS = ("功能修改", "影响范围", "验证结果")
BLOCKED_PATH_PATTERNS = (
    re.compile(r"^(?:dist|\.build|\.tmp-dist[^/]*)/"),
    re.compile(r"^(?:\.env(?:\..*)?|secrets|private)(?:/|$)"),
    re.compile(r"\.(?:sqlite3?|db)(?:-|$)"),
    re.compile(r"\.(?:pem|key|p12|pfx)$"),
)
ALLOWED_COMMIT_NAMES = frozenset(
    (
        "moye12325",
        "loongkkk",
        "LOONGKKK\\loong",
        "Gao Yiheng",
        "GitHub",
    )
)
COAUTHOR_RE = re.compile(r"(?im)^\s*co-authored-by:\s*([^<]+?)\s*<[^>]+>\s*$")


class PolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChangeRecord:
    path: Path
    metadata: dict[str, str]
    sections: dict[str, str]


def parse_change_record(path: Path) -> ChangeRecord:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("+++\n"):
        raise PolicyError(f"{path}: missing TOML frontmatter opener")
    end = text.find("\n+++\n", 4)
    if end < 0:
        raise PolicyError(f"{path}: missing TOML frontmatter closer")
    try:
        raw_metadata = tomllib.loads(text[4:end])
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"{path}: invalid TOML frontmatter: {exc}") from exc
    metadata = {str(key): str(value) for key, value in raw_metadata.items()}
    body = text[end + 5 :]
    matches = list(re.finditer(r"(?m)^## ([^\r\n]+)\s*$", body))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        value_start = match.end()
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[match.group(1).strip()] = body[value_start:value_end].strip()
    return ChangeRecord(path=path, metadata=metadata, sections=sections)


def validate_change_record(record: ChangeRecord, *, required_status: str) -> None:
    for key in ("id", "type", "release_bump", "status"):
        if not record.metadata.get(key, "").strip():
            raise PolicyError(f"{record.path}: missing metadata field {key}")
    if record.metadata["type"] not in ALLOWED_TYPES:
        raise PolicyError(f"{record.path}: invalid type {record.metadata['type']!r}")
    if record.metadata["release_bump"] not in BUMPS:
        raise PolicyError(
            f"{record.path}: invalid release_bump {record.metadata['release_bump']!r}"
        )
    status = record.metadata["status"]
    if status not in STATUS_ORDER:
        raise PolicyError(f"{record.path}: invalid status {status!r}")
    if required_status not in STATUS_ORDER:
        raise PolicyError(f"invalid required status {required_status!r}")
    if STATUS_ORDER[status] < STATUS_ORDER[required_status]:
        raise PolicyError(
            f"{record.path}: status must be at least {required_status}, got {status}"
        )
    for section in REQUIRED_SECTIONS:
        if not record.sections.get(section, "").strip():
            raise PolicyError(f"{record.path}: missing or empty section {section}")


def parse_version(tag: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(tag)
    if match is None:
        raise PolicyError(f"invalid semantic version tag: {tag!r}")
    return tuple(int(value) for value in match.groups())


def bump_version(tag: str, bump: str) -> str:
    if bump not in BUMPS - {"none"}:
        raise PolicyError(f"invalid version bump: {bump!r}")
    major, minor, patch = parse_version(tag)
    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"v{major}.{minor}.{patch}"


def highest_bump(bumps: list[str]) -> str:
    order = {"none": 0, "patch": 1, "minor": 2, "major": 3}
    invalid = [bump for bump in bumps if bump not in order]
    if invalid:
        raise PolicyError(f"invalid release bump: {invalid[0]!r}")
    return max(bumps, key=order.get, default="none")


def build_release_plan(base_tag: str, records: list[ChangeRecord]) -> dict[str, object]:
    bumps = [record.metadata["release_bump"] for record in records]
    bump = highest_bump(bumps)
    if bump == "none":
        return {"release": False, "bump": "none", "tag": None, "records": []}
    return {
        "release": True,
        "bump": bump,
        "tag": bump_version(base_tag, bump),
        "records": [str(record.path).replace("\\", "/") for record in records],
    }


def render_release_notes(tag: str, records: list[ChangeRecord]) -> str:
    lines = [f"# Codex Provider Hub {tag}", "", "本版本由已验证的功能变更说明自动生成。", ""]
    for record in records:
        metadata = record.metadata
        lines.extend(
            [
                f"## {metadata['id']}",
                f"- 类型：{metadata['type']}",
                f"- 发布级别：{metadata['release_bump']}",
                f"- 目标：{record.sections['目标']}",
                f"- 实际改动：{record.sections['实际改动']}",
                f"- 兼容性：{record.sections['兼容性']}",
                f"- 风险：{record.sections['风险']}",
                f"- 验证结果：{record.sections['验证结果']}",
                f"- PR：{record.sections['PR']}",
                "",
            ]
        )
    return "\n".join(lines)


def release_records(base_tag: str, head: str) -> list[ChangeRecord]:
    records = _change_records_for_range(base_tag, head)
    for record in records:
        validate_change_record(record, required_status="verified")
    return records


def command_release_plan(base_tag: str, head: str) -> None:
    records = release_records(base_tag, head)
    plan = build_release_plan(base_tag, records)
    if plan["release"]:
        tag = str(plan["tag"])
        local_exists = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/tags/{tag}"],
            check=False,
        ).returncode == 0
        remote_exists = subprocess.run(
            ["git", "ls-remote", "--exit-code", "origin", f"refs/tags/{tag}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
        if local_exists or remote_exists:
            raise PolicyError(f"release tag already exists: {tag}")
    print(json.dumps(plan, ensure_ascii=False))


def command_release_notes(base_tag: str, head: str, output: str) -> None:
    records = release_records(base_tag, head)
    tag = head if head.startswith("v") else "draft"
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(render_release_notes(tag, records), encoding="utf-8")


def build_ruleset_payload() -> dict[str, object]:
    return {
        "name": "agent-delivery-main",
        "target": "branch",
        "enforcement": "active",
        "conditions": {
            "ref_name": {"include": ["refs/heads/main"], "exclude": []}
        },
        "rules": [
            {"type": "deletion"},
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": True,
                },
            },
        ],
    }


def github_api_request(
    repo: str,
    path: str,
    *,
    token: str,
    method: str = "GET",
    payload: dict[str, object] | None = None,
) -> object:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{path.lstrip('/')}",
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PolicyError(f"GitHub API {method} {path} failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise PolicyError(f"GitHub API connection failed: {exc.reason}") from exc


def configure_ruleset(repo: str, *, dry_run: bool) -> None:
    payload = build_ruleset_payload()
    if dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise PolicyError("configure-ruleset requires GITHUB_TOKEN")
    existing = github_api_request(repo, "rulesets", token=token)
    ruleset_id = next(
        (item["id"] for item in existing if item.get("name") == "agent-delivery-main"),
        None,
    )
    path = f"rulesets/{ruleset_id}" if ruleset_id else "rulesets"
    method = "PUT" if ruleset_id else "POST"
    github_api_request(repo, path, token=token, method=method, payload=payload)
    print(json.dumps(verify_ruleset(repo, token=token), ensure_ascii=False, indent=2))


def verify_ruleset(repo: str, *, token: str) -> dict[str, object]:
    rulesets = github_api_request(repo, "rulesets", token=token)
    ruleset_id = next(
        (item["id"] for item in rulesets if item.get("name") == "agent-delivery-main"),
        None,
    )
    if ruleset_id is None:
        raise PolicyError("remote ruleset agent-delivery-main was not found")
    actual = github_api_request(repo, f"rulesets/{ruleset_id}", token=token)
    expected = build_ruleset_payload()
    for key in ("target", "enforcement", "conditions"):
        if actual.get(key) != expected[key]:
            raise PolicyError(f"remote ruleset mismatch in {key}")
    actual_types = {rule.get("type") for rule in actual.get("rules", [])}
    expected_types = {rule["type"] for rule in expected["rules"]}
    if not expected_types.issubset(actual_types):
        raise PolicyError("remote ruleset is missing required rule types")
    return {"name": actual.get("name"), "id": actual.get("id"), "verified": True}


def validate_branch_name(branch: str) -> None:
    if branch in {"main", "master"}:
        raise PolicyError(f"禁止在受保护分支 {branch} 上提交或推送")
    if not branch.strip():
        raise PolicyError("当前处于 detached HEAD，禁止提交或推送")


def _message_sections(message: str) -> dict[str, str]:
    lines = message.replace("\r\n", "\n").split("\n")
    positions = {line.strip(): index for index, line in enumerate(lines) if line.strip() in COMMIT_SECTIONS}
    sections: dict[str, str] = {}
    for section in COMMIT_SECTIONS:
        if section not in positions:
            raise PolicyError(f"commit 正文缺少章节：{section}")
        start = positions[section] + 1
        later = [positions[name] for name in COMMIT_SECTIONS if positions.get(name, -1) > positions[section]]
        end = min(later) if later else len(lines)
        sections[section] = "\n".join(lines[start:end]).strip()
        if not sections[section]:
            raise PolicyError(f"commit 正文章节不能为空：{section}")
    if not (positions["功能修改"] < positions["影响范围"] < positions["验证结果"]):
        raise PolicyError("commit 正文章节顺序必须为：功能修改、影响范围、验证结果")
    return sections


def validate_commit_message(message: str) -> None:
    title = message.replace("\r\n", "\n").split("\n", 1)[0].strip()
    if COMMIT_TITLE_RE.fullmatch(title) is None:
        raise PolicyError(
            "commit 标题必须使用前置 emoji、Conventional Commit 和简体中文描述"
        )
    _message_sections(message)


def validate_staged_paths(paths: list[str]) -> None:
    normalized = [path.replace("\\", "/") for path in paths]
    for path in normalized:
        if any(pattern.search(path) for pattern in BLOCKED_PATH_PATTERNS):
            raise PolicyError(f"禁止提交生成产物、敏感文件或本地状态：{path}")


def _extract_name(ident: str) -> str:
    name = ident.split("<", 1)[0].strip()
    if not name:
        raise PolicyError(f"无法从身份中解析用户名：{ident!r}")
    return name


def _coauthor_names(message: str) -> list[str]:
    return [match.group(1).strip() for match in COAUTHOR_RE.finditer(message)]


def validate_commit_identities(identities: list[tuple[str, str]]) -> None:
    disallowed = [
        (role, name)
        for role, name in identities
        if name not in ALLOWED_COMMIT_NAMES
    ]
    if disallowed:
        detail = "；".join(f"{role} {name}" for role, name in disallowed)
        raise PolicyError(
            f"提交身份不在白名单中，拒绝提交：{detail}。"
            "仅允许项目已登记的提交者用户名"
        )


def run_git(args: list[str], *, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PolicyError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def _paths_from_git(args: list[str]) -> list[str]:
    output = run_git(args)
    return [line.strip() for line in output.splitlines() if line.strip()]


def _change_records_for_range(base: str, head: str) -> list[ChangeRecord]:
    paths = _paths_from_git(
        ["diff", "--diff-filter=A", "--name-only", f"{base}...{head}", "--", "docs/changes"]
    )
    return [parse_change_record(Path(path)) for path in paths if path.endswith(".md") and not path.endswith("template.md")]


def _require_change_record(paths: list[str], records: list[ChangeRecord]) -> None:
    product_prefixes = (
        "local_proxy/",
        "provider_status/",
        "proxy_static/",
        "scripts/",
        ".github/",
        "packaging/",
        "deploy/",
    )
    has_product_change = any(path.startswith(product_prefixes) or path.endswith(".py") for path in paths)
    if has_product_change and not records:
        raise PolicyError("功能改动必须新增 docs/changes/*.md 变更说明")


def validate_pr(base: str, head: str) -> None:
    paths = _paths_from_git(["diff", "--name-only", f"{base}...{head}"])
    validate_staged_paths(paths)
    records = _change_records_for_range(base, head)
    _require_change_record(paths, records)
    for record in records:
        validate_change_record(record, required_status="verified")
    messages = run_git(["log", "--format=%an%x1f%cn%x1f%B%x00", f"{base}..{head}"])
    for entry in (part for part in messages.split("\x00") if part.strip()):
        author_name, committer_name, message = entry.split("\x1f", 2)
        message = message.strip()
        if not message:
            continue
        validate_commit_message(message)
        identities = [
            ("author", author_name.strip()),
            ("committer", committer_name.strip()),
        ]
        identities.extend(("co-author", name) for name in _coauthor_names(message))
        validate_commit_identities(identities)


def run_full_verification() -> None:
    commands = [
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
    ]
    commands.extend(
        ["node", "--check", str(path)]
        for path in sorted(Path("proxy_static/src").glob("*.js"))
    )
    commands.append(["node", "--check", "provider_status/static/app.js"])
    commands.extend(
        ["node", "--test", str(path)] for path in sorted(Path("tests").glob("*.test.js"))
    )
    for command in commands:
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise PolicyError(f"验证失败：{' '.join(command)}")


def command_install_hooks() -> None:
    run_git(["config", "core.hooksPath", ".githooks"])


def command_pre_commit() -> None:
    validate_branch_name(run_git(["branch", "--show-current"]))
    validate_commit_identities(
        [
            ("author", _extract_name(run_git(["var", "GIT_AUTHOR_IDENT"]))),
            ("committer", _extract_name(run_git(["var", "GIT_COMMITTER_IDENT"]))),
        ]
    )
    paths = _paths_from_git(["diff", "--cached", "--name-only"])
    validate_staged_paths(paths)
    records = [parse_change_record(Path(path)) for path in paths if path.startswith("docs/changes/") and path.endswith(".md") and not path.endswith("template.md")]
    _require_change_record(paths, records)


def command_commit_msg(path: str) -> None:
    message = Path(path).read_text(encoding="utf-8")
    validate_commit_message(message)
    identities = [
        ("author", _extract_name(run_git(["var", "GIT_AUTHOR_IDENT"]))),
        ("committer", _extract_name(run_git(["var", "GIT_COMMITTER_IDENT"]))),
    ]
    identities.extend(("co-author", name) for name in _coauthor_names(message))
    validate_commit_identities(identities)


def command_pre_push(stdin: str) -> None:
    validate_branch_name(run_git(["branch", "--show-current"]))
    for line in stdin.splitlines():
        parts = line.split()
        if len(parts) >= 3 and (parts[0].startswith("refs/tags/") or parts[2] == "refs/heads/main"):
            raise PolicyError("禁止直接推送 main 或标签；必须使用功能分支和自动发布")
    run_git(["fetch", "origin", "main"])
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"],
        check=False,
    )
    if result.returncode != 0:
        raise PolicyError("当前分支落后 origin/main；必须先 rebase 并重新验证")
    validate_pr("origin/main", "HEAD")
    run_full_verification()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repository delivery policy")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("install-hooks")
    subparsers.add_parser("pre-commit")
    commit_parser = subparsers.add_parser("commit-msg")
    commit_parser.add_argument("path")
    subparsers.add_parser("pre-push")
    pr_parser = subparsers.add_parser("validate-pr")
    pr_parser.add_argument("--base", required=True)
    pr_parser.add_argument("--head", required=True)
    ruleset_parser = subparsers.add_parser("configure-ruleset")
    ruleset_parser.add_argument("--repo", required=True)
    ruleset_parser.add_argument("--dry-run", action="store_true")
    verify_parser = subparsers.add_parser("verify-ruleset")
    verify_parser.add_argument("--repo", required=True)
    release_plan_parser = subparsers.add_parser("release-plan")
    release_plan_parser.add_argument("--base-tag", required=True)
    release_plan_parser.add_argument("--head", required=True)
    release_notes_parser = subparsers.add_parser("release-notes")
    release_notes_parser.add_argument("--base-tag", required=True)
    release_notes_parser.add_argument("--head", required=True)
    release_notes_parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "install-hooks":
            command_install_hooks()
        elif args.command == "pre-commit":
            command_pre_commit()
        elif args.command == "commit-msg":
            command_commit_msg(args.path)
        elif args.command == "pre-push":
            command_pre_push(sys.stdin.read())
        elif args.command == "validate-pr":
            validate_pr(args.base, args.head)
        elif args.command == "configure-ruleset":
            configure_ruleset(args.repo, dry_run=args.dry_run)
        elif args.command == "release-plan":
            command_release_plan(args.base_tag, args.head)
        elif args.command == "release-notes":
            command_release_notes(args.base_tag, args.head, args.output)
        else:
            token = os.environ.get("GITHUB_TOKEN", "").strip()
            if not token:
                raise PolicyError("verify-ruleset requires GITHUB_TOKEN")
            print(json.dumps(verify_ruleset(args.repo, token=token), ensure_ascii=False))
    except PolicyError as exc:
        print(f"policy error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
