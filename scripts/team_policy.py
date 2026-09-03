from __future__ import annotations

import re
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
    "style",
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
UNITTEST_FAILURE_RE = re.compile(r"^(?:FAIL|ERROR):\s+\S+\s+\(([^)\]]+)\)", re.MULTILINE)
NODE_FAILURE_RE = re.compile(r"(?m)^[ \t]*not ok[ \t]+\d+[ \t]+-[ \t]+(.+?)[ \t]*(?:#.*)?$")
PYTHON_SUITE_SENTINEL = "<python-suite-exit-nonzero>"
NODE_SUITE_SENTINEL = "<node-suite-exit-nonzero>"


class PolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChangeRecord:
    path: Path
    metadata: dict[str, str]
    sections: dict[str, str]
    title: str = ""


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
    title_match = re.search(r"(?m)^#\s+(?!\#)([^\r\n]+?)\s*$", body)
    return ChangeRecord(
        path=path,
        metadata=metadata,
        sections=sections,
        title=title_match.group(1).strip() if title_match else "",
    )


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


RELEASE_REPO_URL = "https://github.com/AI-Routing-Research-Institute/codex-provider-hub"
RELEASE_NOTE_GROUPS = (
    ("✨ 新功能", ("feature",)),
    ("🐞 问题修复", ("fix",)),
    ("🛠️ 其他改进", ("chore", "docs", "refactor", "style", "performance", "build", "test")),
)


def _plain_sentence(text: str, limit: int = 60) -> str:
    """Collapse a section into one short plain sentence for release notes."""
    collapsed = re.sub(r"\s+", " ", text).strip()
    collapsed = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", collapsed)
    collapsed = collapsed.replace("**", "").replace("`", "")
    collapsed = re.sub(r"[A-Za-z]:\\[^\s，。）)（]*", "", collapsed)
    collapsed = re.sub(r"(?:~|／|/)[^\s，。）)（]*\.(?:md|html|py|js|vue|yml)", "", collapsed)
    collapsed = re.sub(r"（[，、\s]*）", "", collapsed)
    collapsed = collapsed.replace("（，", "（").replace("（ ", "（")
    collapsed = re.sub(r"^[-*•\d.\s]+", "", collapsed)
    cut = len(collapsed)
    for separator in ("。", "；", "：", "！", "？"):
        index = collapsed.find(separator)
        if index != -1:
            cut = min(cut, index)
    sentence = collapsed[:cut].strip(" ，,")
    if len(sentence) > limit:
        trimmed = sentence[:limit]
        depth = trimmed.count("（") + trimmed.count("(") - trimmed.count("）") - trimmed.count(")")
        if depth > 0:
            bracket = max(trimmed.rfind("（", 0, limit), trimmed.rfind("(", 0, limit))
            if bracket > 20:
                trimmed = trimmed[:bracket]
        sentence = trimmed.rstrip(" ，,（(") + "…"
    return sentence


def _record_headline(record: ChangeRecord) -> str:
    headline = record.metadata.get("headline", "").strip()
    if headline:
        return _plain_sentence(headline, limit=80)
    return _plain_sentence(record.sections.get("目标", ""))


def _record_title(record: ChangeRecord) -> str:
    title = record.title or record.metadata.get("id", "")
    if len(title) > 30:
        title = title[:30].rstrip() + "…"
    return title


def render_release_notes(tag: str, records: list[ChangeRecord]) -> str:
    lines = [
        f"# Codex 本地中转 {tag}",
        "",
        f"本次更新 {len(records)} 项改进。",
        "",
    ]
    for group_title, type_names in RELEASE_NOTE_GROUPS:
        grouped = [record for record in records if record.metadata.get("type") in type_names]
        if not grouped:
            continue
        lines.append(f"## {group_title}")
        for record in grouped:
            lines.append(f"- **{_record_title(record)}**：{_record_headline(record)}")
        lines.append("")
    lines.extend(["## 技术细节", ""])
    for record in records:
        title = _record_title(record)
        path = str(record.path).replace("\\", "/")
        lines.append(f"- [{title}]({RELEASE_REPO_URL}/blob/{tag}/{path})")
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


def _npm_command() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def _syntax_check_commands() -> list[list[str]]:
    commands = [["node", "--check", "proxy_static/classic/app.js"]]
    commands.extend(
        ["node", "--check", str(path)]
        for path in sorted(Path("proxy_static/src").glob("*.js"))
    )
    commands.append(["node", "--check", "provider_status/static/app.js"])
    return commands


def _run_capture(command: list[str], *, cwd: Path) -> tuple[int, str]:
    result = subprocess.run(
        command,
        cwd=str(cwd),
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def parse_unittest_failures(output: str) -> list[str]:
    return sorted(set(UNITTEST_FAILURE_RE.findall(output)))


def parse_node_failures(output: str) -> list[str]:
    return sorted({name.strip() for name in NODE_FAILURE_RE.findall(output)})


def run_comparable_suites(root: Path) -> dict[str, object]:
    """Run the Python and node suites; report failure ids (not exit codes)."""
    code, output = _run_capture(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
        cwd=root,
    )
    python_failures = parse_unittest_failures(output)
    if code != 0 and not python_failures:
        python_failures = [PYTHON_SUITE_SENTINEL]

    node_failures: dict[str, list[str]] = {}
    for test_file in sorted((root / "tests").glob("*.test.js")):
        code, output = _run_capture(["node", "--test", str(test_file)], cwd=root)
        if code == 0:
            continue
        names = parse_node_failures(output)
        node_failures[str(test_file.relative_to(root)).replace("\\", "/")] = (
            names or [NODE_SUITE_SENTINEL]
        )
    return {"python": python_failures, "node": node_failures}


def diff_new_failures(head: dict[str, object], baseline: dict[str, object]) -> list[str]:
    """Failures present on HEAD but absent from the merge-base baseline."""
    new_items: list[str] = []
    base_python = set(baseline.get("python", ()) or ())
    for test_id in sorted(set(head.get("python", ()) or ()) - base_python):
        new_items.append(f"python {test_id}")
    base_node = baseline.get("node", {}) or {}
    head_node = head.get("node", {}) or {}
    for file, names in sorted(head_node.items()):
        base_names = set(base_node.get(file, ()) or ())
        for name in sorted(set(names or ()) - base_names):
            new_items.append(f"node {file} :: {name}")
    return new_items


def _baseline_cache_path(merge_base: str) -> Path:
    git_dir = Path(run_git(["rev-parse", "--absolute-git-dir"]))
    cache_dir = git_dir / "policy-baselines"
    return cache_dir / f"{merge_base}.json"


def read_baseline_cache(merge_base: str) -> dict[str, object] | None:
    cache = _baseline_cache_path(merge_base)
    try:
        loaded = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded


def write_baseline_cache(merge_base: str, baseline: dict[str, object]) -> None:
    cache = _baseline_cache_path(merge_base)
    cache.parent.mkdir(parents=True, exist_ok=True)
    for stale in cache.parent.glob("*.json"):
        stale.unlink(missing_ok=True)
    cache.write_text(json.dumps(baseline, ensure_ascii=False, indent=1), encoding="utf-8")


def collect_baseline(merge_base: str) -> dict[str, object]:
    """Run the comparable suites in a throwaway worktree of merge_base."""
    workdir = tempfile.mkdtemp(prefix="policy-baseline-")
    worktree = Path(workdir) / "baseline"
    run_git(["worktree", "add", "--detach", "--quiet", str(worktree), merge_base])
    try:
        return run_comparable_suites(worktree)
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", "--quiet", str(worktree)],
            check=False,
            capture_output=True,
        )
        shutil.rmtree(workdir, ignore_errors=True)


def load_or_build_baseline(merge_base: str) -> dict[str, object]:
    cached = read_baseline_cache(merge_base)
    if cached is not None:
        return cached
    baseline = collect_baseline(merge_base)
    write_baseline_cache(merge_base, baseline)
    return baseline


def _confirm_new_failures(new_failures: list[str]) -> list[str]:
    """Rerun newly failing targets once to filter out flaky one-offs."""
    confirmed: list[str] = []
    python_ids = [
        item.removeprefix("python ").strip()
        for item in new_failures
        if item.startswith("python ") and not item.endswith(PYTHON_SUITE_SENTINEL)
    ]
    if python_ids:
        code, output = _run_capture(
            [sys.executable, "-m", "unittest", *python_ids], cwd=Path(".")
        )
        if code != 0:
            still = set(parse_unittest_failures(output))
            confirmed.extend(
                f"python {test_id}" for test_id in sorted(still or set(python_ids))
            )
    if any(item == f"python {PYTHON_SUITE_SENTINEL}" for item in new_failures):
        confirmed.append(f"python {PYTHON_SUITE_SENTINEL}")

    node_files = sorted(
        {
            item.removeprefix("node ").split(" :: ")[0]
            for item in new_failures
            if item.startswith("node ") and NODE_SUITE_SENTINEL not in item
        }
    )
    for file in node_files:
        code, output = _run_capture(["node", "--test", file], cwd=Path("."))
        if code != 0:
            names = set(parse_node_failures(output))
            confirmed.extend(
                f"node {file} :: {name}"
                for name in sorted(names) or [NODE_SUITE_SENTINEL]
            )
    confirmed.extend(
        item for item in new_failures if NODE_SUITE_SENTINEL in item and item not in confirmed
    )
    return confirmed


def run_full_verification() -> None:
    """Hard gates must pass; suite failures only block when new vs the baseline."""
    for command in [
        [_npm_command(), "ci", "--prefix", "proxy_static"],
        [_npm_command(), "run", "build", "--prefix", "proxy_static"],
        *_syntax_check_commands(),
    ]:
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise PolicyError(f"验证失败：{' '.join(command)}")

    head = run_comparable_suites(Path("."))
    merge_base = run_git(["merge-base", "origin/main", "HEAD"])
    baseline = load_or_build_baseline(merge_base)
    new_failures = diff_new_failures(head, baseline)
    confirmed: list[str] = []
    if new_failures:
        confirmed = _confirm_new_failures(new_failures)
    if confirmed:
        detail = "\n".join(f"- {item}" for item in confirmed)
        raise PolicyError(
            f"全量验证出现基线之外的新增失败（merge-base {merge_base}），禁止推送：\n{detail}"
        )
    ignored = len(head.get("python", []) or []) + sum(
        len(names) for names in (head.get("node", {}) or {}).values()
    )
    print(
        f"pre-push 全量验证通过：忽略基线既有失败 {ignored} 项，无新增失败。"
    )


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


def _ensure_utf8_stdio() -> None:
    """Force UTF-8 stdio so Chinese policy errors stay readable under git-bash.

    Hooks run with an MSYS locale whose default codepage is not UTF-8; without
    this, PolicyError messages degrade into mojibake. Never raises.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            continue


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
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
