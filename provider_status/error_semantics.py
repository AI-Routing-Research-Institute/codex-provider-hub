from __future__ import annotations


_USAGE_LIMIT_PHRASES = (
    "usage limit",
    "rate limit",
    "too many requests",
    "quota exceeded",
    "insufficient quota",
    "insufficient credit",
    "insufficient balance",
    "额度不足",
    "余额不足",
    "配额不足",
    "额度已用尽",
    "余额已用尽",
    "配额已用尽",
)


def has_usage_limit_semantics(value: str) -> bool:
    normalized = value.casefold()
    return any(phrase in normalized for phrase in _USAGE_LIMIT_PHRASES)


__all__ = ["has_usage_limit_semantics"]
