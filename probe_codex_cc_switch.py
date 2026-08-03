#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from codex_app_server_client import (
    AppServerProtocolError,
    AppServerTurnResult,
    CodexAppServerClient,
)


NETWORK_ENV_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "TERM",
    "NO_COLOR",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


TYPE_CHECKERS: dict[str, tuple[type, ...]] = {
    "str": (str,),
    "int": (int,),
    "list": (list,),
}


STATUS_LABELS: dict[str, str] = {
    "healthy": "正常",
    "timeout": "超时",
    "auth_fail": "失败",
    "rate_limited": "限流",
    "model_unavailable": "模型不可用",
    "provider_error": "服务异常",
    "route_fail": "路由异常",
    "bad_output": "输出异常",
    "network_error": "连接异常",
    "client_blocked": "客户端受限",
    "app_server_error": "客户端异常",
    "exec_failed": "失败",
}


@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    title: str
    body: str
    required_keys: tuple[str, ...]
    type_expectations: dict[str, str]


@dataclass(frozen=True)
class ProviderRecord:
    provider_id: str
    name: str
    is_current: bool
    endpoint_url: str | None
    common_config_enabled: bool
    raw_config: str
    auth: dict[str, Any]
    meta: dict[str, Any]

    @property
    def is_api_provider(self) -> bool:
        return bool(self.endpoint_url) or bool(self.raw_config.strip())


@dataclass(frozen=True)
class ModelRunSpec:
    model: str
    reasoning_effort: str


PROMPT_POOL: tuple[PromptSpec, ...] = (
    PromptSpec(
        prompt_id="pagination_bug",
        title="Python pagination bug",
        required_keys=("bug", "fixed_code", "tests"),
        type_expectations={"bug": "str", "fixed_code": "str", "tests": "list"},
        body=textwrap.dedent(
            """
            你在帮我排查一个分页 bug。不要调用工具，直接基于下面内容回答，并且只返回 JSON。

            返回格式：
            {"bug":"", "fixed_code":"", "tests":["",""]}

            Python 代码：
            def page(items, page_no, page_size):
                start = page_no * page_size
                end = start + page_size
                return items[start:end]

            要求：
            1. 指出 bug
            2. 给出修复后的函数
            3. 给出 2 个边界测试点
            """
        ).strip(),
    ),
    PromptSpec(
        prompt_id="log_root_cause",
        title="API log root cause",
        required_keys=("root_cause", "evidence", "next_step"),
        type_expectations={"root_cause": "str", "evidence": "list", "next_step": "list"},
        body=textwrap.dedent(
            """
            你在做一次日志排查。不要调用工具，直接基于下面内容回答，并且只返回 JSON。

            返回格式：
            {"root_cause":"", "evidence":["",""], "next_step":["",""]}

            日志：
            2026-07-04T10:12:01Z GET /api/orders?page=1&page_size=20 200 35ms
            2026-07-04T10:12:03Z GET /api/orders?page=2&page_size=20 200 37ms
            2026-07-04T10:12:06Z GET /api/orders?page=3&page_size=20 500 12ms
            2026-07-04T10:12:06Z ERROR ValueError: invalid literal for int() with base 10: ''
            2026-07-04T10:12:06Z at parse_page_size(request.query.page_size)

            要求：
            1. 判断最可能根因
            2. 给出两条证据
            3. 给出两个下一步处理建议
            """
        ).strip(),
    ),
    PromptSpec(
        prompt_id="order_summary",
        title="Order data summary",
        required_keys=("total_orders", "paid_orders", "total_paid_amount", "top_user_by_paid_amount"),
        type_expectations={
            "total_orders": "int",
            "paid_orders": "int",
            "total_paid_amount": "int",
            "top_user_by_paid_amount": "str",
        },
        body=textwrap.dedent(
            """
            你在做一个小型数据汇总。不要调用工具，直接基于下面内容回答，并且只返回 JSON。

            返回格式：
            {"total_orders":0, "paid_orders":0, "total_paid_amount":0, "top_user_by_paid_amount":""}

            数据：
            [
              {"user":"alice","status":"paid","amount":120},
              {"user":"bob","status":"pending","amount":80},
              {"user":"alice","status":"paid","amount":30},
              {"user":"carol","status":"paid","amount":200},
              {"user":"bob","status":"paid","amount":50}
            ]

            要求：
            1. 统计总订单数
            2. 统计 paid 订单数
            3. 统计 paid 总金额
            4. 找出 paid 金额最高的用户
            """
        ).strip(),
    ),
    PromptSpec(
        prompt_id="coupon_api_design",
        title="Coupon API design",
        required_keys=("endpoint", "method", "required_fields", "validation_rules", "error_cases"),
        type_expectations={
            "endpoint": "str",
            "method": "str",
            "required_fields": "list",
            "validation_rules": "list",
            "error_cases": "list",
        },
        body=textwrap.dedent(
            """
            你在帮我补一个接口设计说明。不要调用工具，直接基于下面内容回答，并且只返回 JSON。

            返回格式：
            {"endpoint":"", "method":"", "required_fields":["",""], "validation_rules":["",""], "error_cases":["",""]}

            需求：
            要新增“创建优惠券”接口。字段包括：
            - code: 优惠券码，必填，长度 6 到 12
            - discount_percent: 折扣百分比，必填，范围 1 到 80
            - expires_at: 过期时间，必填，必须晚于当前时间
            - user_id: 可选，不传表示全站可用

            要求：
            1. 设计一个合理的 endpoint 和 method
            2. 列出必填字段
            3. 列出 3 条校验规则
            4. 列出 2 个错误场景
            """
        ).strip(),
    ),
    PromptSpec(
        prompt_id="javascript_review",
        title="JavaScript async review",
        required_keys=("problem", "fixed_code", "why", "test_case"),
        type_expectations={
            "problem": "str",
            "fixed_code": "str",
            "why": "str",
            "test_case": "str",
        },
        body=textwrap.dedent(
            """
            你在 review 一段 JavaScript。不要调用工具，直接基于下面内容回答，并且只返回 JSON。

            返回格式：
            {"problem":"", "fixed_code":"", "why":"", "test_case":""}

            代码：
            async function loadUser(id) {
              const resp = fetch(`/api/users/${id}`);
              const data = await resp.json();
              return data.name;
            }

            要求：
            1. 找出问题
            2. 给出修复后的代码
            3. 简述原因
            4. 给出一个测试场景
            """
        ).strip(),
    ),
    PromptSpec(
        prompt_id="sql_validation",
        title="SQL validation",
        required_keys=("correct_query", "reason", "expected_result"),
        type_expectations={"correct_query": "str", "reason": "str", "expected_result": "int"},
        body=textwrap.dedent(
            """
            你在做 SQL 结果校对。不要调用工具，直接基于下面内容回答，并且只返回 JSON。

            返回格式：
            {"correct_query":"", "reason":"", "expected_result":0}

            表结构：
            orders(id, user_id, status, amount)

            目标：
            统计 status='paid' 且 amount > 100 的订单数量

            错误 SQL：
            SELECT SUM(*) FROM orders WHERE status = 'paid' OR amount > 100;

            样例数据：
            1, 10, paid, 120
            2, 11, paid, 50
            3, 12, pending, 180
            4, 13, paid, 220

            要求：
            1. 写出正确 SQL
            2. 说明原 SQL 错在哪
            3. 计算样例数据上的正确结果
            """
        ).strip(),
    ),
    PromptSpec(
        prompt_id="shell_safety",
        title="Shell safety refactor",
        required_keys=("risk", "fixed_script", "notes"),
        type_expectations={"risk": "str", "fixed_script": "str", "notes": "list"},
        body=textwrap.dedent(
            """
            你在做一段 shell 脚本的稳健性修复。不要调用工具，直接基于下面内容回答，并且只返回 JSON。

            返回格式：
            {"risk":"", "fixed_script":"", "notes":["",""]}

            脚本：
            #!/usr/bin/env bash
            set -e
            TARGET_DIR=$1
            rm -rf $TARGET_DIR/*
            cp -r ./dist/* $TARGET_DIR/

            要求：
            1. 指出最主要风险
            2. 给出更稳妥的修复版脚本
            3. 给出 2 条注意事项
            """
        ).strip(),
    ),
    PromptSpec(
        prompt_id="regex_extract",
        title="Regex extraction",
        required_keys=("regex", "matches", "explanation"),
        type_expectations={"regex": "str", "matches": "list", "explanation": "str"},
        body=textwrap.dedent(
            """
            你在帮我整理一个正则提取需求。不要调用工具，直接基于下面内容回答，并且只返回 JSON。

            返回格式：
            {"regex":"", "matches":["",""], "explanation":""}

            文本：
            订单号: ORD-20260704-001
            订单号: ORD-20260704-002
            错误编号: ERR-900
            订单号: ORD-20260705-003

            要求：
            1. 写一个能提取所有订单号的正则
            2. 列出应匹配到的结果
            3. 简述为什么这个正则合适
            """
        ).strip(),
    ),
    PromptSpec(
        prompt_id="python_refactor",
        title="Python function refactor",
        required_keys=("issue", "refactored_code", "benefit", "test_case"),
        type_expectations={"issue": "list", "refactored_code": "str", "benefit": "str", "test_case": "str"},
        body=textwrap.dedent(
            """
            你在做一个 Python 函数重构。不要调用工具，直接基于下面内容回答，并且只返回 JSON。

            返回格式：
            {"issue":["",""], "refactored_code":"", "benefit":"", "test_case":""}

            代码：
            def normalize_name(name):
                name = name.strip()
                name = name.lower()
                if name == "":
                    return ""
                return name[0].upper() + name[1:]

            要求：
            1. 说出 2 个可以改进的点
            2. 给出重构后的代码
            3. 说明收益
            4. 给出 1 个测试用例
            """
        ).strip(),
    ),
    PromptSpec(
        prompt_id="feature_breakdown",
        title="Feature breakdown",
        required_keys=("tasks", "risks", "acceptance"),
        type_expectations={"tasks": "list", "risks": "list", "acceptance": "list"},
        body=textwrap.dedent(
            """
            你在做一次小型需求拆解。不要调用工具，直接基于下面内容回答，并且只返回 JSON。

            返回格式：
            {"tasks":["","",""], "risks":["",""], "acceptance":["","",""]}

            需求：
            要给后台订单列表增加“按支付状态筛选”和“按下单时间倒序排序”功能。前端已有筛选栏，后端已有基础分页接口，但当前不支持这两个条件。

            要求：
            1. 拆成 3 个实施任务
            2. 列出 2 个主要风险
            3. 给出 3 条验收标准
            """
        ).strip(),
    ),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe Codex providers stored by CC Switch via isolated Codex app-server calls."
    )
    parser.add_argument(
        "--db-path",
        default="~/.cc-switch/cc-switch.db",
        help="Path to the CC Switch sqlite database.",
    )
    parser.add_argument(
        "--catalog-path",
        default="~/.codex/cc-switch-model-catalog.json",
        help="Optional model catalog copied into isolated CODEX_HOME when present.",
    )
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex CLI executable to invoke.",
    )
    parser.add_argument(
        "--base-dir",
        default="~/.cache/codex-cc-switch-probe",
        help="Directory used for temporary probe homes and workspaces.",
    )
    parser.add_argument(
        "--provider",
        action="append",
        default=[],
        help="Filter providers by case-insensitive substring match on name or id. Repeatable.",
    )
    parser.add_argument(
        "--current-only",
        action="store_true",
        help="Probe only the provider currently marked as active in CC Switch.",
    )
    parser.add_argument(
        "--include-non-api",
        action="store_true",
        help="Include non-API providers such as official direct-login entries. Default is to probe API providers only.",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=1,
        help="Maximum attempts per provider-model pair. Default: 1.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Single model override for compatibility. When set, overrides --models.",
    )
    parser.add_argument(
        "--models",
        default="gpt-5.4,gpt-5.5",
        help="Comma-separated model list to probe per provider. Default: gpt-5.4,gpt-5.5.",
    )
    parser.add_argument(
        "--reasoning-effort",
        default="high",
        help="Reasoning effort passed to the isolated app-server turn. Default: high.",
    )
    parser.add_argument(
        "--sandbox",
        default="read-only",
        choices=("read-only", "workspace-write", "danger-full-access"),
        help="Sandbox mode for isolated app-server threads. Default: read-only.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
        help="Timeout in seconds for each app-server attempt. Default: 90.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed for deterministic prompt selection.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep isolated run directories for inspection.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the final report as JSON when --output is not set. When --output is set, JSON is written to file and stdout prints a human-readable summary.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional file path to write the final JSON report.",
    )
    parser.add_argument(
        "--list-providers",
        action="store_true",
        help="List Codex providers found in CC Switch and exit.",
    )
    parser.add_argument(
        "--list-prompts",
        action="store_true",
        help="List the built-in realistic prompt pool and exit.",
    )
    return parser


def expand_path(raw: str) -> Path:
    return Path(os.path.expanduser(raw)).resolve()


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered)
    return slug.strip("-") or "provider"


def load_codex_common_config(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'common_config_codex'"
        ).fetchone()
    return (row["value"] if row else "") or ""


def load_codex_providers(db_path: Path) -> list[ProviderRecord]:
    query = """
    SELECT
      p.id AS provider_id,
      p.name AS provider_name,
      p.is_current AS is_current,
      p.settings_config AS settings_config,
      p.meta AS provider_meta,
      pe.url AS endpoint_url
    FROM providers p
    LEFT JOIN provider_endpoints pe
      ON pe.provider_id = p.id AND pe.app_type = p.app_type
    WHERE p.app_type = 'codex'
    ORDER BY p.sort_index IS NULL, p.sort_index, p.created_at, p.name
    """
    providers: list[ProviderRecord] = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query).fetchall()
    for row in rows:
        payload = json.loads(row["settings_config"])
        auth = payload.get("auth") or {}
        if not isinstance(auth, dict):
            raise ValueError(f"Provider {row['provider_name']} has non-dict auth payload")
        meta = json.loads(row["provider_meta"]) if row["provider_meta"] else {}
        providers.append(
            ProviderRecord(
                provider_id=row["provider_id"],
                name=row["provider_name"],
                is_current=bool(row["is_current"]),
                endpoint_url=row["endpoint_url"],
                common_config_enabled=bool(meta.get("commonConfigEnabled")),
                raw_config=(payload.get("config") or "").strip(),
                auth=auth,
                meta=meta,
            )
        )
    return providers


def filter_providers(
    providers: list[ProviderRecord],
    filters: list[str],
    current_only: bool,
    include_non_api: bool,
) -> list[ProviderRecord]:
    selected = providers
    if not include_non_api:
        selected = [provider for provider in selected if provider.is_api_provider]
    if current_only:
        selected = [provider for provider in selected if provider.is_current]
    if filters:
        lowered_filters = [item.casefold() for item in filters]
        selected = [
            provider
            for provider in selected
            if any(
                needle in provider.name.casefold() or needle in provider.provider_id.casefold()
                for needle in lowered_filters
            )
        ]
    return selected


def list_providers(providers: list[ProviderRecord]) -> int:
    if not providers:
        print("No codex providers found in CC Switch.")
        return 1
    for provider in providers:
        current = "yes" if provider.is_current else "no"
        common = "yes" if provider.common_config_enabled else "no"
        endpoint = provider.endpoint_url or "-"
        api = "yes" if provider.is_api_provider else "no"
        print(
            f"{provider.name}\n"
            f"  id: {provider.provider_id}\n"
            f"  api_provider: {api}\n"
            f"  current: {current}\n"
            f"  common_config_enabled: {common}\n"
            f"  endpoint: {endpoint}\n"
        )
    return 0


def list_prompts() -> int:
    for prompt in PROMPT_POOL:
        print(f"{prompt.prompt_id}: {prompt.title}")
    return 0


def build_effective_config(provider: ProviderRecord, common_config: str) -> str:
    if not provider.common_config_enabled:
        return provider.raw_config.strip() + ("\n" if provider.raw_config.strip() else "")
    merged = merge_toml_documents(common_config, provider.raw_config)
    return serialize_toml_document(merged)


def merge_toml_documents(base_text: str, overlay_text: str) -> dict[str, Any]:
    base_data = tomllib.loads(base_text) if base_text.strip() else {}
    overlay_data = tomllib.loads(overlay_text) if overlay_text.strip() else {}
    return deep_merge_dicts(base_data, overlay_data)


def deep_merge_dicts(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def serialize_toml_document(data: dict[str, Any]) -> str:
    lines = emit_toml_table(data, path=())
    if not lines:
        return ""
    return "\n".join(lines).rstrip() + "\n"


def emit_toml_table(table: dict[str, Any], path: tuple[str, ...]) -> list[str]:
    lines: list[str] = []
    scalar_items: list[tuple[str, Any]] = []
    table_items: list[tuple[str, dict[str, Any]]] = []

    for key, value in table.items():
        if isinstance(value, dict):
            table_items.append((key, value))
        else:
            scalar_items.append((key, value))

    if path:
        lines.append(f"[{format_table_path(path)}]")

    for key, value in scalar_items:
        lines.append(f"{format_key(key)} = {format_toml_value(value)}")

    for key, child in table_items:
        child_lines = emit_toml_table(child, path + (key,))
        if child_lines:
            if lines:
                lines.append("")
            lines.extend(child_lines)

    return lines


def format_table_path(path: tuple[str, ...]) -> str:
    return ".".join(format_key(part) for part in path)


def format_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(format_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f'{format_key(k)} = {format_toml_value(v)}' for k, v in value.items()) + " }"
    raise TypeError(f"Unsupported TOML value type: {type(value)!r}")


def prepare_run_directory(
    base_dir: Path,
    provider: ProviderRecord,
    common_config: str,
    catalog_path: Path,
) -> tuple[Path, Path]:
    base_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(
        tempfile.mkdtemp(
            prefix=f"{slugify(provider.name)}-",
            dir=str(base_dir),
        )
    )
    codex_home = run_dir / "codex-home"
    workspace = run_dir / "workspace"
    codex_home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    config_text = build_effective_config(provider, common_config)
    (codex_home / "config.toml").write_text(config_text, encoding="utf-8")
    (codex_home / "auth.json").write_text(
        json.dumps(provider.auth, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if catalog_path.is_file():
        shutil.copy2(catalog_path, codex_home / catalog_path.name)
    return run_dir, workspace


def build_env(codex_home: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in NETWORK_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            env[key] = value
    env["HOME"] = str(codex_home)
    env["CODEX_HOME"] = str(codex_home)
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    env.setdefault("TERM", "xterm-256color")
    env["NO_COLOR"] = "1"
    return env


def strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def extract_json_payload(raw_text: str) -> tuple[dict[str, Any] | None, str | None]:
    text = strip_code_fence(raw_text)
    candidates: list[str] = []
    if text:
        candidates.append(text)
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            candidate = text[first_brace : last_brace + 1]
            if candidate != text:
                candidates.append(candidate)
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed, None
        return None, "response JSON is not an object"
    return None, "response is not valid JSON"


def validate_payload(prompt: PromptSpec, payload: dict[str, Any]) -> str | None:
    missing = [key for key in prompt.required_keys if key not in payload]
    if missing:
        return "missing keys: " + ", ".join(missing)
    for key, kind in prompt.type_expectations.items():
        expected = TYPE_CHECKERS[kind]
        value = payload[key]
        if not isinstance(value, expected):
            return f"key {key!r} is not of type {kind}"
        if kind == "str" and not value.strip():
            return f"key {key!r} is empty"
        if kind == "list" and not value:
            return f"key {key!r} is empty"
    return None


def mask_sensitive_text(text: str) -> str:
    masked = re.sub(
        r"sk-[A-Za-z0-9_-]{10,}",
        lambda match: match.group(0)[:6] + "***" + match.group(0)[-4:],
        text,
    )
    return masked


def compact_text(text: str, limit: int = 600) -> str:
    flattened = " ".join(text.split())
    masked = mask_sensitive_text(flattened)
    if len(masked) <= limit:
        return masked
    head = max(120, limit // 2 - 20)
    tail = max(120, limit - head - 5)
    return masked[:head] + " ... " + masked[-tail:]


def emit_progress(message: str) -> None:
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", file=sys.stderr, flush=True)


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def summarize_model_status(model_run: dict[str, Any]) -> str:
    return f"{model_run['model']} {status_label(model_run['status'])}"


def append_unique(values: list[str], value: str) -> None:
    normalized = " ".join(value.split())
    if normalized and normalized not in values:
        values.append(normalized)


def extract_reconnect_progress(detail_text: str) -> tuple[int, int] | None:
    matches = re.findall(
        r"ERROR:\s*Reconnecting\.\.\.\s*(\d+)/(\d+)",
        detail_text,
        flags=re.IGNORECASE,
    )
    if not matches:
        return None
    parsed = [(int(current), int(total)) for current, total in matches]
    return max(parsed, key=lambda item: item[0])


def extract_common_error_notes(detail_text: str) -> list[str]:
    notes: list[str] = []

    if re.search(r"currently experiencing high demand", detail_text, flags=re.IGNORECASE):
        append_unique(notes, "上游当前高负载，可能导致临时失败")

    for matched in re.finditer(
        r"stream disconnected before completion:\s*error sending request for url\s*\((https?://[^)]+)\)",
        detail_text,
        flags=re.IGNORECASE,
    ):
        append_unique(
            notes,
            f"连接异常：响应完成前连接已断开（{matched.group(1)}）",
        )

    for matched in re.finditer(
        r"No available channel for model\s+([^\s]+)\s+under group\s+(.+?)(?:\s+\(distributor\)|\s+\(request id:|,|\n|$)",
        detail_text,
        flags=re.IGNORECASE,
    ):
        model = matched.group(1).strip()
        group = matched.group(2).strip()
        append_unique(notes, f"服务商无可用通道：分组 {group} 下没有 {model} 可用通道")

    if re.search(r"401 Unauthorized|auth error:\s*401|Invalid token|invalid_api_key|incorrect api key", detail_text, flags=re.IGNORECASE):
        append_unique(notes, "鉴权失败：API Key 或 Token 无效")

    if re.search(r"429|rate limit", detail_text, flags=re.IGNORECASE):
        append_unique(notes, "请求被限流：触发频率限制")

    if re.search(r"\bquota\b|insufficient_quota", detail_text, flags=re.IGNORECASE):
        append_unique(notes, "额度不足：账号或通道配额不可用")

    if re.search(r"INSUFFICIENT_BALANCE", detail_text, flags=re.IGNORECASE):
        append_unique(notes, "余额不足：账号或通道余额不可用")

    if re.search(r"model_not_found|unsupported model", detail_text, flags=re.IGNORECASE):
        append_unique(notes, "模型不可用：服务商不支持当前模型名")

    if re.search(r"unexpected status 530|error code:\s*1033", detail_text, flags=re.IGNORECASE):
        append_unique(notes, "上游网关异常：Cloudflare 530/1033 连接失败")

    if re.search(r"This channel does not allow the current client", detail_text, flags=re.IGNORECASE):
        append_unique(notes, "渠道限制：当前客户端不允许使用该通道")

    if "无可用账号" in detail_text:
        append_unique(notes, "服务商无可用账号")

    if "请勿发送探测请求和无意义内容" in detail_text:
        append_unique(notes, "服务商拒绝疑似探测或无意义请求")

    return notes


def extract_attempt_notes(
    detail_text: str,
    validation_error: str | None = None,
    timed_out: bool = False,
) -> list[str]:
    notes: list[str] = []
    for note in extract_common_error_notes(detail_text):
        append_unique(notes, note)

    concrete_patterns = (
        r"unexpected status \d+ [^:,\n]*(?:: [^,\n]+)?",
        r"\b\d{3} Unauthorized: [^,\n]+",
        r"\b\d{3} Forbidden: [^,\n]+",
        r"auth error: 401[^,\n]*",
        r"invalid_api_key[^,\n]*",
        r"incorrect api key[^,\n]*",
        r"Invalid token",
        r"error code: \d+",
        r"This channel does not allow the current client",
        r"无可用账号",
        r"请勿发送探测请求和无意义内容",
        r"rate limit[^,\n]*",
        r"quota[^,\n]*",
        r"model_not_found[^,\n]*",
        r"unsupported model[^,\n]*",
    )
    if not notes:
        for pattern in concrete_patterns:
            for matched in re.finditer(pattern, detail_text, flags=re.IGNORECASE):
                append_unique(notes, matched.group(0).strip())

    reconnect = extract_reconnect_progress(detail_text)
    if reconnect:
        current, total = reconnect
        if timed_out:
            append_unique(
                notes,
                f"Codex 重连到 {current}/{total} 时被脚本超时截断，最终后端错误尚未吐出；可把 --timeout 调到 60 或 90 再看完整错误",
            )
        else:
            append_unique(notes, f"Codex 出现重连，最后进度 {current}/{total}")

    if validation_error and validation_error != "response is not valid JSON":
        append_unique(notes, validation_error)
    return notes[:4]


def extract_attempt_note(attempt: dict[str, Any]) -> str:
    notes = attempt.get("error_summary")
    if isinstance(notes, list) and notes:
        return str(notes[0])
    extracted = extract_attempt_notes(
        detail_text=attempt.get("detail_excerpt") or "",
        validation_error=attempt.get("validation_error"),
        timed_out=bool(attempt.get("timed_out")),
    )
    return extracted[0] if extracted else ""


def collect_provider_notes(result: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    for model_run in result["model_runs"]:
        if model_run["status"] == "healthy":
            continue
        for attempt in reversed(model_run["attempts"]):
            attempt_notes = attempt.get("error_summary")
            if not isinstance(attempt_notes, list):
                attempt_notes = extract_attempt_notes(
                    detail_text=attempt.get("detail_excerpt") or "",
                    validation_error=attempt.get("validation_error"),
                    timed_out=bool(attempt.get("timed_out")),
                )
            before_count = len(notes)
            for note in attempt_notes:
                append_unique(notes, str(note))
            if len(notes) > before_count:
                break
    return notes


def summarize_provider_result(result: dict[str, Any]) -> str:
    model_parts = [summarize_model_status(model_run) for model_run in result["model_runs"]]
    return f"{result['provider_name']}：{'，'.join(model_parts)}"


def classify_failure(
    returncode: int,
    output_valid: bool,
    validation_error: str | None,
    detail_text: str,
    timed_out: bool,
) -> tuple[str, bool]:
    lowered = detail_text.casefold()
    if timed_out:
        return "timeout", True
    if output_valid and returncode == 0:
        return "healthy", False
    if "this channel does not allow the current client" in lowered:
        return "client_blocked", False
    if "app-server 协议错误" in lowered or "无法启动 codex app-server" in lowered:
        return "app_server_error", False
    if "stream disconnected before completion" in lowered or "error sending request for url" in lowered:
        return "network_error", True
    if "invalid_api_key" in lowered or "incorrect api key" in lowered:
        return "auth_fail", False
    if "401 unauthorized" in lowered or "auth error: 401" in lowered:
        return "auth_fail", False
    if "429" in lowered or "rate limit" in lowered or "quota" in lowered or "insufficient_balance" in lowered:
        return "rate_limited", False
    if "model_not_found" in lowered or "unsupported model" in lowered:
        return "model_unavailable", False
    if "does not exist" in lowered and "model" in lowered:
        return "model_unavailable", False
    if "unexpected status 530" in lowered or "error code: 1033" in lowered:
        return "provider_error", False
    if "404" in lowered and ("responses" in lowered or "completions" in lowered):
        return "route_fail", False
    if "currently experiencing high demand" in lowered:
        return "provider_error", True
    if "500" in lowered or "502" in lowered or "503" in lowered or "504" in lowered:
        return "provider_error", True
    if validation_error:
        return "bad_output", True
    if returncode == 0:
        return "bad_output", True
    return "exec_failed", True


def sample_prompts(rng: random.Random, attempts: int) -> list[PromptSpec]:
    if attempts <= 0:
        return []
    if attempts <= len(PROMPT_POOL):
        return rng.sample(list(PROMPT_POOL), k=attempts)
    prompts = list(PROMPT_POOL)
    rng.shuffle(prompts)
    expanded = prompts[:]
    while len(expanded) < attempts:
        extra = list(PROMPT_POOL)
        rng.shuffle(extra)
        expanded.extend(extra)
    return expanded[:attempts]


def run_single_attempt(
    args: argparse.Namespace,
    provider: ProviderRecord,
    prompt: PromptSpec,
    workspace: Path,
    codex_home: Path,
    attempt_index: int,
    model_run: ModelRunSpec,
    app_server_client: CodexAppServerClient,
) -> dict[str, Any]:
    model_slug = slugify(model_run.model)
    output_file = workspace.parent / f"{model_slug}-attempt-{attempt_index:02d}-response.txt"
    emit_progress(
        f"{provider.name} | {model_run.model} | 第 {attempt_index}/{args.attempts} 次尝试 | 题目 {prompt.prompt_id}"
    )
    started = time.monotonic()
    try:
        app_result = app_server_client.run_turn(prompt.body, timeout=args.timeout)
    except AppServerProtocolError as exc:
        app_result = AppServerTurnResult(
            output_text="",
            turn_status="failed",
            error_text=f"app-server 协议错误：{exc}",
            diagnostics=app_server_client.diagnostics,
            timed_out=False,
            http_status_code=None,
            user_agent="",
        )
    except KeyboardInterrupt:
        app_server_client.interrupt_active_turn()
        app_server_client.close()
        raise
    elapsed = round(time.monotonic() - started, 2)
    output_text = app_result.output_text
    if output_text:
        output_file.write_text(output_text, encoding="utf-8")
    payload, parse_error = extract_json_payload(output_text)
    validation_error = parse_error
    output_valid = payload is not None
    if payload is not None:
        validation_error = validate_payload(prompt, payload)
        output_valid = validation_error is None
    detail_parts = [app_result.error_text, app_result.diagnostics]
    if app_result.http_status_code is not None:
        detail_parts.append(f"HTTP {app_result.http_status_code}")
    if app_result.user_agent:
        detail_parts.append(f"client: {app_result.user_agent}")
    if output_text:
        detail_parts.append(output_text)
    detail_text = "\n".join(part for part in detail_parts if part)
    status, retryable = classify_failure(
        returncode=app_result.returncode,
        output_valid=output_valid,
        validation_error=validation_error,
        detail_text=detail_text,
        timed_out=app_result.timed_out,
    )
    error_summary = extract_attempt_notes(
        detail_text=detail_text,
        validation_error=validation_error,
        timed_out=app_result.timed_out,
    )
    progress_line = (
        f"{provider.name} | {model_run.model} | 第 {attempt_index}/{args.attempts} 次尝试完成 | "
        f"状态 {status_label(status)} | rc={app_result.returncode} | elapsed={elapsed}s"
    )
    if error_summary:
        progress_line += f" | 关键信息 {'；'.join(error_summary)}"
    emit_progress(progress_line)
    return {
        "provider_id": provider.provider_id,
        "provider_name": provider.name,
        "model": model_run.model,
        "reasoning_effort": model_run.reasoning_effort,
        "prompt_id": prompt.prompt_id,
        "prompt_title": prompt.title,
        "status": status,
        "retryable": retryable,
        "returncode": app_result.returncode,
        "timed_out": app_result.timed_out,
        "elapsed_seconds": elapsed,
        "output_valid": output_valid,
        "validation_error": validation_error,
        "output_file": str(output_file),
        "response_excerpt": compact_text(output_text) if output_text else "",
        "detail_excerpt": compact_text(detail_text) if detail_text else "",
        "error_summary": error_summary,
        "response_payload": payload,
        "app_server_turn_status": app_result.turn_status,
        "http_status_code": app_result.http_status_code,
        "client_user_agent": app_result.user_agent,
    }


def run_provider_model(
    args: argparse.Namespace,
    provider: ProviderRecord,
    rng: random.Random,
    workspace: Path,
    codex_home: Path,
    model_run: ModelRunSpec,
) -> dict[str, Any]:
    attempt_reports: list[dict[str, Any]] = []
    final_status = "exec_failed"
    prompts = sample_prompts(rng, args.attempts)
    emit_progress(f"{provider.name} | 开始模型 {model_run.model}")
    config_path = codex_home / "config.toml"
    model_provider: str | None = None
    if config_path.is_file():
        try:
            config_data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            config_data = {}
        configured_provider = config_data.get("model_provider")
        if isinstance(configured_provider, str) and configured_provider.strip():
            model_provider = configured_provider.strip()
    with CodexAppServerClient(
        codex_bin=getattr(args, "codex_bin", "codex"),
        env=build_env(codex_home),
        workspace=workspace,
        sandbox=getattr(args, "sandbox", "read-only"),
        model=model_run.model,
        reasoning_effort=model_run.reasoning_effort,
        model_provider=model_provider,
    ) as app_server_client:
        for index, prompt in enumerate(prompts, start=1):
            attempt = run_single_attempt(
                args=args,
                provider=provider,
                prompt=prompt,
                workspace=workspace,
                codex_home=codex_home,
                attempt_index=index,
                model_run=model_run,
                app_server_client=app_server_client,
            )
            attempt_reports.append(attempt)
            final_status = attempt["status"]
            if final_status == "healthy" or not attempt["retryable"]:
                break
    emit_progress(
        f"{provider.name} | 模型 {model_run.model} 完成 | 最终状态 {status_label(final_status)}"
    )
    return {
        "model": model_run.model,
        "reasoning_effort": model_run.reasoning_effort,
        "status": final_status,
        "attempts": attempt_reports,
    }


def summarize_provider_status(model_results: list[dict[str, Any]]) -> str:
    if model_results and all(item["status"] == "healthy" for item in model_results):
        return "healthy"
    failing = [item["status"] for item in model_results if item["status"] != "healthy"]
    return failing[0] if failing else "exec_failed"


def run_provider(
    args: argparse.Namespace,
    provider: ProviderRecord,
    common_config: str,
    catalog_path: Path,
    rng: random.Random,
    model_runs: list[ModelRunSpec],
) -> dict[str, Any]:
    emit_progress(f"{provider.name} | 准备隔离环境")
    run_dir, workspace = prepare_run_directory(
        base_dir=expand_path(args.base_dir),
        provider=provider,
        common_config=common_config,
        catalog_path=catalog_path,
    )
    codex_home = run_dir / "codex-home"
    model_results: list[dict[str, Any]] = []
    try:
        for model_run in model_runs:
            result = run_provider_model(
                args=args,
                provider=provider,
                rng=rng,
                workspace=workspace,
                codex_home=codex_home,
                model_run=model_run,
            )
            model_results.append(result)
    finally:
        if not args.keep_temp:
            shutil.rmtree(run_dir, ignore_errors=True)
    result = {
        "provider_id": provider.provider_id,
        "provider_name": provider.name,
        "is_current": provider.is_current,
        "endpoint_url": provider.endpoint_url,
        "common_config_enabled": provider.common_config_enabled,
        "run_directory": str(run_dir) if args.keep_temp else "",
        "status": summarize_provider_status(model_results),
        "model_runs": model_results,
    }
    emit_progress(f"{provider.name} | 探测完成 | {summarize_provider_result(result)}")
    return result


def write_report(output_path: Path, report: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_human_report(report: dict[str, Any], output_path: Path | None = None) -> None:
    current_provider = next(
        (item["provider_name"] for item in report["results"] if item["is_current"]),
        "",
    )
    print("==== Codex API 探测结果 ====")
    print(f"时间：{report['generated_at']}")
    print(f"模型：{', '.join(report['models'])}")
    print(f"推理强度：{report['reasoning_effort_override']}")
    print(f"待测源数量：{report['provider_count']}")
    print(f"随机种子：{report['seed']}")
    if current_provider:
        print(f"当前源：{current_provider}")
    if output_path:
        print(f"JSON 报告：{output_path}")
    print("")
    for item in report["results"]:
        print(summarize_provider_result(item))
        for note in collect_provider_notes(item):
            print(f"  关键信息：{note}")
        if item["is_current"]:
            print("  当前 is_current: true")


def parse_model_runs(args: argparse.Namespace) -> list[ModelRunSpec]:
    raw_models = args.model if args.model else args.models
    models = [item.strip() for item in raw_models.split(",") if item.strip()]
    if not models:
        raise ValueError("at least one model must be provided")
    return [ModelRunSpec(model=model, reasoning_effort=args.reasoning_effort) for model in models]


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.attempts < 1:
        parser.error("--attempts must be >= 1")

    db_path = expand_path(args.db_path)
    if not db_path.is_file():
        parser.error(f"CC Switch db not found: {db_path}")

    catalog_path = expand_path(args.catalog_path)
    emit_progress(f"读取 CC Switch 数据库: {db_path}")
    common_config = load_codex_common_config(db_path)
    providers = load_codex_providers(db_path)
    providers = filter_providers(
        providers,
        args.provider,
        args.current_only,
        args.include_non_api,
    )

    if args.list_prompts:
        return list_prompts()
    if args.list_providers:
        return list_providers(providers)
    if not providers:
        print("No matching codex providers found.", file=sys.stderr)
        return 2
    try:
        model_runs = parse_model_runs(args)
    except ValueError as exc:
        parser.error(str(exc))

    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1, 2**31)
    rng = random.Random(seed)
    emit_progress(
        f"开始探测，共 {len(providers)} 个源，模型 {', '.join(item.model for item in model_runs)}，最大尝试 {args.attempts} 次，单次超时 {args.timeout}s"
    )

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "db_path": str(db_path),
        "catalog_path": str(catalog_path),
        "models": [item.model for item in model_runs],
        "reasoning_effort_override": args.reasoning_effort,
        "provider_count": len(providers),
        "seed": seed,
        "results": [],
    }

    exit_code = 0
    for index, provider in enumerate(providers, start=1):
        emit_progress(f"开始探测源 {index}/{len(providers)}: {provider.name}")
        result = run_provider(
            args=args,
            provider=provider,
            common_config=common_config,
            catalog_path=catalog_path,
            rng=rng,
            model_runs=model_runs,
        )
        report["results"].append(result)
        if any(model_run["status"] != "healthy" for model_run in result["model_runs"]):
            exit_code = 1

    output_path = expand_path(args.output) if args.output else None
    if args.output:
        write_report(output_path, report)
        emit_progress(f"JSON 报告已写入: {output_path}")

    if args.json and not output_path:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human_report(report, output_path=output_path)
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n探测已由用户取消。", file=sys.stderr)
        sys.exit(130)
