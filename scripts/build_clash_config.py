"""Build Clash Meta config from template and subscription feeds."""

from __future__ import annotations

import base64
import copy
import datetime as dt
import json
import os
import re
import subprocess
import sys
from typing import Optional
import urllib.parse
import urllib.request
import ssl
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # fallback to JSON/PowerShell parsing when PyYAML is unavailable

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "test.yaml"
OUTPUT_CONFIG = REPO_ROOT / "dist" / "config.yaml"
PROVIDER_PATH = REPO_ROOT / "providers" / "all.yaml"
SUBSCRIPTION_REPORT_PATH = REPO_ROOT / "dist" / "subscriptions_report.md"
BUILD_STATUS_PATH = REPO_ROOT / "dist" / "build_status.json"

BYTES_IN_GIB = 1024**3


def relpath_posix(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def load_yaml(text: str):
    if yaml:
        try:
            return yaml.safe_load(text)
        except Exception:
            pass
    try:
        return json.loads(text)
    except Exception:
        pass
    # PowerShell fallback (local env) using ConvertFrom-Yaml -> JSON
    try:
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "ConvertFrom-Yaml @'\n"
            + text
            + "\n'@ | ConvertTo-Json -Depth 20",
        ]
        output = subprocess.check_output(cmd, text=True)
        if output:
            return json.loads(output)
    except Exception:
        pass
    return None


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def dump_yaml(data) -> str:
    if yaml:
        return yaml.safe_dump(
            data,
            allow_unicode=True,
            sort_keys=False,
            indent=2,
            default_flow_style=False,
        )
    return json.dumps(data, ensure_ascii=False, indent=2)


def persist_pruned_subscriptions_yaml(
    template_path: Path, original_template: dict, kept_subscriptions: list
) -> bool:
    if not yaml:
        return False

    updated = copy.deepcopy(original_template)
    updated["subscriptions"] = kept_subscriptions
    text = dump_yaml(updated)
    if template_path.exists():
        original_text = template_path.read_text(encoding="utf-8")
        if original_text == text:
            return False
    template_path.write_text(text, encoding="utf-8")
    return True


def persist_updated_subscriptions_yaml(
    template_path: Path, original_template: dict, subscriptions: list
) -> bool:
    """
    Write back the `subscriptions` section only (keep the rest unchanged).

    This is used to persist pause/prune state without accidentally committing
    generated proxy-groups into `test.yaml`.
    """

    if not yaml:
        return False

    updated = copy.deepcopy(original_template)
    updated["subscriptions"] = subscriptions
    text = dump_yaml(updated)
    if template_path.exists():
        original_text = template_path.read_text(encoding="utf-8")
        if original_text == text:
            return False
    template_path.write_text(text, encoding="utf-8")
    return True


def redact_url(raw_url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(raw_url)
    except Exception:
        return "<invalid-url>"

    if not parsed.scheme or not parsed.netloc:
        return "<invalid-url>"

    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    for key in list(qs.keys()):
        if key.lower() in {"token", "key", "auth", "password"}:
            qs[key] = ["***"]
    return parsed._replace(query=urllib.parse.urlencode(qs, doseq=True)).geturl()


def parse_subscription_userinfo(value: str) -> dict[str, int]:
    """
    Parse `subscription-userinfo` header, commonly like:
    `upload=123; download=456; total=789; expire=1700000000`
    Values are bytes.
    """

    info: dict[str, int] = {}
    for chunk in re.split(r"[;,]\s*", value.strip()):
        if "=" not in chunk:
            continue
        key, raw_val = chunk.split("=", 1)
        key = key.strip().lower()
        raw_val = raw_val.strip()
        if not raw_val.isdigit():
            continue
        info[key] = int(raw_val)
    return info


def remaining_bytes_from_headers(headers) -> Optional[int]:
    if not headers:
        return None

    for key, value in getattr(headers, "items", lambda: [])():
        if key.lower() not in {"subscription-userinfo", "subscription-user-info"}:
            continue
        parsed = parse_subscription_userinfo(value)
        total = parsed.get("total")
        if total is None:
            return None
        upload = parsed.get("upload", 0)
        download = parsed.get("download", 0)
        return max(0, total - upload - download)

    return None


def expire_from_headers(headers) -> Optional[int]:
    if not headers:
        return None

    for key, value in getattr(headers, "items", lambda: [])():
        if key.lower() not in {"subscription-userinfo", "subscription-user-info"}:
            continue
        parsed = parse_subscription_userinfo(value)
        expire = parsed.get("expire")
        if expire is None:
            return None
        return expire

    return None


def reset_at_from_headers(headers) -> Optional[dt.datetime]:
    """
    Try to extract "reset time/date" from common subscription headers.

    Some providers extend `subscription-userinfo` with fields like:
    - reset=1735689600 (unix timestamp, seconds)
    - reset=2026-01-01
    Or use standalone headers like `subscription-reset`.
    """

    if not headers:
        return None

    candidates = []
    for key, value in getattr(headers, "items", lambda: [])():
        k = key.lower()
        if k in {
            "subscription-reset",
            "subscription-reset-time",
            "reset",
            "x-subscription-reset",
        }:
            candidates.append(value)
            continue
        if k in {"subscription-userinfo", "subscription-user-info"} and isinstance(
            value, str
        ):
            for chunk in re.split(r"[;,]\s*", value.strip()):
                if "=" not in chunk:
                    continue
                raw_k, raw_v = chunk.split("=", 1)
                raw_k = raw_k.strip().lower()
                raw_v = raw_v.strip()
                if raw_k in {"reset", "reset_at", "resetat", "resettime", "reset_time"}:
                    candidates.append(raw_v)

    for raw in candidates:
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text:
            continue
        if text.isdigit():
            try:
                return dt.datetime.fromtimestamp(int(text), tz=dt.timezone.utc)
            except Exception:
                continue
        parsed = parse_date_like_utc(text)
        if parsed is not None:
            return parsed

    return None


def parse_utc_rfc3339(value: str) -> Optional[dt.datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


def parse_date_like_utc(value: str) -> Optional[dt.datetime]:
    """
    Parse a date/datetime string that might appear in provider "reset" hints.

    Supported (examples):
    - 2026-01-31
    - 2026/01/31
    - 2026-01-31 12:34:56
    - 2026-01-31T12:34:56Z
    """

    if not isinstance(value, str) or not value.strip():
        return None

    raw = value.strip()

    parsed = parse_utc_rfc3339(raw)
    if parsed is not None:
        return parsed

    m = re.search(
        r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?",
        raw,
    )
    if not m:
        return None

    year = int(m.group(1))
    month = int(m.group(2))
    day = int(m.group(3))
    hour = int(m.group(4) or 0)
    minute = int(m.group(5) or 0)
    second = int(m.group(6) or 0)

    try:
        return dt.datetime(
            year, month, day, hour, minute, second, tzinfo=dt.timezone.utc
        )
    except ValueError:
        return None


def format_utc_rfc3339(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def human_bytes_to_int(value: str) -> Optional[int]:
    """
    Parse common subscription "remaining" strings like:
    - "370.5 GB" / "0.00GiB" / "1234 MiB" / "1.2 TB"

    Treat GB/MB/TB as binary units to align with GiB display.
    """

    if not isinstance(value, str):
        return None
    text = value.strip()
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B)", text, re.IGNORECASE)
    if not m:
        return None
    try:
        num = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2).lower()
    mul = {
        "kb": 1024,
        "kib": 1024,
        "mb": 1024**2,
        "mib": 1024**2,
        "gb": 1024**3,
        "gib": 1024**3,
        "tb": 1024**4,
        "tib": 1024**4,
    }.get(unit)
    if not mul:
        return None
    return max(0, int(num * mul))


def extract_subscription_display_hints(
    proxies: list[dict],
) -> tuple[list[dict], dict[str, object]]:
    """
    Some providers embed remaining/reset hints as "fake nodes" (proxy entries) so they show in UI.
    We remove them from usable proxies and extract hint values for display groups.
    """

    kept: list[dict] = []
    hints: dict[str, object] = {}

    for proxy in proxies or []:
        name = proxy.get("name")
        if not isinstance(name, str):
            kept.append(proxy)
            continue
        normalized = name.strip()

        m_rem = re.search(r"^\s*剩余流量\s*[:：]\s*(.+?)\s*$", normalized)
        if m_rem:
            remaining_bytes = human_bytes_to_int(m_rem.group(1))
            if remaining_bytes is not None:
                hints["remaining_bytes_hint"] = remaining_bytes
            continue

        m_reset = re.search(r"(?:距离)?下次重置.*?[:：]\s*(\d+)\s*天", normalized)
        if not m_reset:
            m_reset = re.search(r"重置剩余\s*[:：]\s*(\d+)\s*天", normalized)
        if m_reset:
            try:
                hints["reset_days_hint"] = int(m_reset.group(1))
            except Exception:
                pass
            continue

        m_reset_date = re.search(
            r"(下次重置|重置日期|重置时间)\s*[:：]\s*(.+?)\s*$", normalized
        )
        if m_reset_date:
            reset_dt = parse_date_like_utc(m_reset_date.group(2))
            if reset_dt is not None:
                hints["reset_at_utc"] = format_utc_rfc3339(reset_dt)
            continue

        kept.append(proxy)

    return kept, hints


def load_proxies_from_provider(path: Path):
    if not path.exists():
        return []
    try:
        data = load_yaml(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict) and isinstance(data.get("proxies"), list):
        return data["proxies"]
    return []


def parse_subscription_payload(text: str):
    parsed = load_yaml(text)
    if isinstance(parsed, dict):
        return parsed
    try:
        decoded = base64.b64decode(text.strip()).decode()
    except Exception:
        return None
    return load_yaml(decoded)


def normalize_subscriptions(raw_subscriptions) -> list[dict[str, object]]:
    if not raw_subscriptions:
        return []
    if not isinstance(raw_subscriptions, list):
        return []

    normalized: list[dict[str, object]] = []

    def derive_name(url: str, index: int) -> str:
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.netloc:
                return parsed.netloc
        except Exception:
            pass
        return f"subscription-{index}"

    for idx, entry in enumerate(raw_subscriptions, start=1):
        if isinstance(entry, str):
            normalized.append({"name": derive_name(entry, idx), "url": entry})
            continue

        if isinstance(entry, dict):
            raw_url = entry.get("url") or entry.get("link") or entry.get("subscription")
            if not isinstance(raw_url, str) or not raw_url.strip():
                continue
            url = raw_url.strip()
            raw_name = entry.get("name")
            name = (
                raw_name.strip()
                if isinstance(raw_name, str) and raw_name.strip()
                else derive_name(url, idx)
            )
            min_remaining_gb = entry.get("min_remaining_gb")
            normalized_entry: dict[str, object] = {"name": name, "url": url}
            if isinstance(min_remaining_gb, (int, float)) and min_remaining_gb >= 0:
                normalized_entry["min_remaining_bytes"] = int(
                    float(min_remaining_gb) * BYTES_IN_GIB
                )
            # Persisted state fields (written back by CI) to support pause/until-reset.
            for key in (
                "pause_until_utc",
                "last_remaining_bytes",
                "last_total_bytes",
                "last_used_bytes",
                "last_expire",
                "last_reset_days",
            ):
                if key in entry:
                    normalized_entry[key] = entry.get(key)
            normalized.append(normalized_entry)

    return normalized


def format_gib(n: Optional[int]) -> str:
    if n is None:
        return "-"
    return f"{n / BYTES_IN_GIB:.2f}"


def write_subscription_report(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return

    lines = [
        "# Subscription Traffic Report",
        "",
        "| Name | Remaining (GiB) | Total (GiB) | Used (GiB) | Expire (UTC) | Status |",
        "|---|---:|---:|---:|---|---|",
    ]

    for row in rows:
        name = str(row.get("name") or "-")
        remaining = row.get("remaining_bytes")
        total = row.get("total_bytes")
        used = row.get("used_bytes")
        expire = row.get("expire")
        status = str(row.get("status") or "-")

        expire_str = "-"
        if isinstance(expire, int) and expire > 0:
            expire_str = dt.datetime.fromtimestamp(
                expire, tz=dt.timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")

        lines.append(
            f"| {name} | {format_gib(remaining if isinstance(remaining, int) else None)} | "
            f"{format_gib(total if isinstance(total, int) else None)} | "
            f"{format_gib(used if isinstance(used, int) else None)} | "
            f"{expire_str} | {status} |"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_build_status(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def unique_name(name: str, existing: set[str]) -> str:
    if name not in existing:
        existing.add(name)
        return name
    i = 2
    while f"{name} ({i})" in existing:
        i += 1
    unique = f"{name} ({i})"
    existing.add(unique)
    return unique


def inject_subscription_traffic_groups(
    template: dict,
    report_rows: list[dict[str, object]],
    *,
    include_parent_group: bool,
):
    """
    Add a dedicated proxy-group that exposes per-subscription remaining traffic in UI.

    This avoids renaming real nodes (which would break grouping/rules) and keeps the
    traffic display groups isolated (not referenced by routing rules).
    """

    if not report_rows:
        return

    # Remove previously generated traffic display groups, so output stays clean even if
    # someone committed old 📊 groups into `test.yaml`.
    removed_names: set[str] = set()
    groups = template.get("proxy-groups") or []
    if isinstance(groups, list):
        kept_groups = []
        for g in groups:
            if not isinstance(g, dict):
                kept_groups.append(g)
                continue
            name = g.get("name")
            if (
                isinstance(name, str)
                and name.startswith("📊")
                and ("剩余" in name or "订阅流量" in name or "暂停" in name)
            ):
                removed_names.add(name)
                continue
            kept_groups.append(g)
        template["proxy-groups"] = kept_groups
        if removed_names:
            for g in template["proxy-groups"]:
                if isinstance(g, dict) and isinstance(g.get("proxies"), list):
                    g["proxies"] = [p for p in g["proxies"] if p not in removed_names]

    existing_names = set()
    for g in template.get("proxy-groups", []) or []:
        if isinstance(g, dict) and isinstance(g.get("name"), str):
            existing_names.add(g["name"])

    children = []
    child_names = []

    for row in report_rows:
        sub_name = str(row.get("name") or "-")
        remaining = row.get("remaining_bytes")
        status = str(row.get("status") or "")
        reset_days = row.get("reset_days")
        pause_until_utc = row.get("pause_until_utc")

        if isinstance(remaining, int):
            rem = f"{remaining / BYTES_IN_GIB:.2f}GiB"
        else:
            rem = "未知"

        label = f"📊{sub_name}"
        if status.startswith("paused"):
            label += f" 暂停中（剩余 {rem}"
        elif status.startswith("depleted"):
            label += f" 已用尽（剩余 {rem}"
        else:
            label += f" 剩余 {rem}"

        # Prefer dynamic "days left" computed from pause_until_utc; fall back to provider hint.
        days_left: Optional[int] = None
        pause_until_dt = (
            parse_utc_rfc3339(str(pause_until_utc)) if pause_until_utc else None
        )
        if pause_until_dt is not None:
            now = dt.datetime.now(tz=dt.timezone.utc)
            delta = pause_until_dt - now
            days_left = max(0, int((delta.total_seconds() + 86399) // 86400))
        elif isinstance(reset_days, int) and reset_days >= 0:
            days_left = int(reset_days)

        if isinstance(days_left, int):
            if "（" in label:
                label += f"，距重置 {days_left} 天）"
            else:
                label += f"（距重置 {days_left} 天）"
        elif "（" in label:
            label += "）"

        if status.startswith("removed"):
            label += "（已移除）"

        child_name = unique_name(label, existing_names)
        child_names.append(child_name)
        children.append(
            {
                "name": child_name,
                "type": "select",
                "proxies": ["DIRECT"],
            }
        )

    template.setdefault("proxy-groups", [])
    template["proxy-groups"].extend(children)

    # Optional: add a parent group as a "directory" only when it adds value.
    if include_parent_group and len(child_names) > 1:
        parent_name = unique_name("📊订阅流量", existing_names)
        parent_group = {
            "name": parent_name,
            "type": "select",
            "proxies": child_names + ["DIRECT"],
        }
        template["proxy-groups"].append(parent_group)


def compile_exclude_name_patterns() -> list[re.Pattern]:
    """
    Subscription feeds sometimes include "instruction/advertisement" entries that look like nodes
    but are not meant to be used (e.g. 文档/更换客户端/订阅更多节点).

    Keep patterns conservative and readable; users can override/extend via env var.
    """

    patterns = [
        # Common instruction/advertisement keywords (CN)
        r"(文档|教程|说明|使用说明|帮助|官网|网址|下载|客户端|更换客户端|请更换|更新客户端)",
        r"(订阅更多|更多节点|获取节点|获取更多|购买|续费|充值|到期|过期)",
        r"(客服|工单|反馈|联系|QQ群|QQ|微信|telegram|tg群|频道|公告)",
        # Traffic/reset hints injected by some providers (should not become usable nodes)
        r"(剩余流量|流量剩余|下次重置|重置剩余|距离下次重置)",
    ]

    extra = os.getenv("SUBSCRIPTION_EXCLUDE_NAME_REGEX", "").strip()
    if extra:
        for part in re.split(r"[;\n]+", extra):
            part = part.strip()
            if part:
                patterns.append(part)

    compiled = []
    for pat in patterns:
        try:
            compiled.append(re.compile(pat, re.IGNORECASE))
        except re.error:
            continue
    return compiled


def filter_proxies_by_name_noise(proxies: list[dict]) -> tuple[list[dict], list[str]]:
    compiled = compile_exclude_name_patterns()
    if not compiled:
        return proxies, []

    kept: list[dict] = []
    removed_names: list[str] = []

    for proxy in proxies:
        name = proxy.get("name")
        if not isinstance(name, str) or not name.strip():
            kept.append(proxy)
            continue
        if any(p.search(name) for p in compiled):
            removed_names.append(name)
            continue
        kept.append(proxy)

    return kept, removed_names


def fetch_proxies(subscriptions, *, min_remaining_bytes: Optional[int] = None):
    proxies = []
    seen_names = set()
    proxy_sources: dict[str, str] = {}
    prunable_subscriptions = []
    report_rows: list[dict[str, object]] = []
    updated_subscriptions: list[dict[str, object]] = []
    now = dt.datetime.now(tz=dt.timezone.utc)

    def candidates(raw_url: str):
        yield raw_url
        parsed = urllib.parse.urlparse(raw_url)
        qs = urllib.parse.parse_qs(parsed.query)
        token = qs.get("token", [None])[0]

        # base subscribe url
        sub_base = f"{parsed.scheme}://{parsed.netloc}/api/v1/client/subscribe"

        def build(params: dict):
            return sub_base + "?" + urllib.parse.urlencode(params, doseq=True)

        base_params = {}
        if token:
            base_params["token"] = token
        if "types" in qs:
            base_params["types"] = qs["types"]

        flag_candidates = [
            None,
            "clashmeta",
            "clash",
            "meta",
            "clashr",
            "v2ray",
            "v2rayn",
        ]

        path_candidates = []
        if parsed.path.endswith("verify_mode.htm"):
            path_candidates.append(parsed._replace(path="/api/v1/client/subscribe"))
            path_candidates.append(parsed._replace(path="/api/client/subscribe"))
        path_candidates.append(parsed._replace(path="/api/v1/client/subscribe"))

        for p in path_candidates:
            # same scheme
            for flag in flag_candidates:
                params = dict(base_params)
                if flag:
                    params["flag"] = flag
                yield p._replace(query=urllib.parse.urlencode(params, doseq=True)).geturl()
            # fallback: force http scheme
            if p.scheme == "https":
                p_http = p._replace(scheme="http")
                for flag in flag_candidates:
                    params = dict(base_params)
                    if flag:
                        params["flag"] = flag
                    yield p_http._replace(query=urllib.parse.urlencode(params, doseq=True)).geturl()

    def fetch_once(url: str):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "ClashMeta/1.18",
                "Referer": url,
            },
        )
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            body = resp.read().decode(
                resp.headers.get_content_charset() or "utf-8", errors="replace"
            )
            headers = resp.headers
        return parse_subscription_payload(body), headers

    def fetch_with_fallback(raw_url: str):
        for cand in candidates(raw_url):
            try:
                data, headers = fetch_once(cand)
                if data:
                    return data, headers, cand
            except Exception:
                continue
        raise ValueError(f"订阅解析失败: {redact_url(raw_url)}")

    for sub in subscriptions:
        url = str(sub.get("url") or "")
        subscription_name = str(sub.get("name") or redact_url(url))
        per_sub_min = sub.get("min_remaining_bytes")
        threshold = (
            int(per_sub_min)
            if isinstance(per_sub_min, int) and per_sub_min >= 0
            else min_remaining_bytes
        )

        pause_until_dt = parse_utc_rfc3339(str(sub.get("pause_until_utc") or ""))
        if pause_until_dt is not None and now < pause_until_dt:
            updated_subscriptions.append(dict(sub))
            report_rows.append(
                {
                    "name": subscription_name,
                    "url_redacted": redact_url(url),
                    "remaining_bytes": sub.get("last_remaining_bytes")
                    if isinstance(sub.get("last_remaining_bytes"), int)
                    else None,
                    "total_bytes": sub.get("last_total_bytes")
                    if isinstance(sub.get("last_total_bytes"), int)
                    else None,
                    "used_bytes": sub.get("last_used_bytes")
                    if isinstance(sub.get("last_used_bytes"), int)
                    else None,
                    "expire": sub.get("last_expire")
                    if isinstance(sub.get("last_expire"), int)
                    else None,
                    "status": "paused(until-reset)",
                    "pause_until_utc": sub.get("pause_until_utc"),
                    "reset_days": sub.get("last_reset_days")
                    if isinstance(sub.get("last_reset_days"), int)
                    else None,
                }
            )
            continue

        data, headers, used_url = fetch_with_fallback(url)
        remaining = remaining_bytes_from_headers(headers)
        expire = expire_from_headers(headers)

        total_bytes = None
        used_bytes = None
        if remaining is not None:
            # Re-parse once to avoid double header scanning logic.
            for key, value in getattr(headers, "items", lambda: [])():
                if key.lower() in {"subscription-userinfo", "subscription-user-info"}:
                    parsed = parse_subscription_userinfo(value)
                    total_bytes = parsed.get("total")
                    if total_bytes is not None:
                        used_bytes = max(
                            0, parsed.get("upload", 0) + parsed.get("download", 0)
                        )
                    break

        if not data or "proxies" not in data or not isinstance(data["proxies"], list):
            raise ValueError(
                f"订阅格式不符合预期或缺少 proxies: {redact_url(used_url)}"
            )

        cleaned_proxies, hints = extract_subscription_display_hints(data["proxies"])
        remaining_hint = hints.get("remaining_bytes_hint")
        reset_days_hint = hints.get("reset_days_hint")
        reset_at_dt = reset_at_from_headers(headers)
        if reset_at_dt is None:
            reset_at_dt = parse_utc_rfc3339(str(hints.get("reset_at_utc") or ""))
        if reset_at_dt is not None:
            delta = reset_at_dt - now
            computed_days = max(0, int((delta.total_seconds() + 86399) // 86400))
            reset_days_hint = computed_days
        if remaining is None and isinstance(remaining_hint, int):
            remaining = remaining_hint

        status = "ok"
        pause_until_utc: Optional[str] = None
        should_skip_proxies = False
        if threshold is not None and remaining is not None and remaining < threshold:
            should_skip_proxies = True
            if reset_at_dt is not None and reset_at_dt > now:
                status = "paused(until-reset)"
                pause_until_utc = format_utc_rfc3339(reset_at_dt)
            elif isinstance(reset_days_hint, int) and int(reset_days_hint) > 0:
                status = "paused(until-reset)"
                pause_until_utc = format_utc_rfc3339(
                    now + dt.timedelta(days=int(reset_days_hint))
                )
            else:
                # Keep subscription info (and keep trying on next run) unless it has expired.
                status = "depleted(unknown-reset)"
        elif threshold is not None and remaining is None:
            status = "ok(unknown-remaining)"

        prune = False
        if status.startswith("depleted") and isinstance(expire, int) and expire > 0:
            # If reset info is unknown, only remove subscription info after it expires.
            prune = int(now.timestamp()) >= expire
            if prune:
                status = "removed(expired)"

        updated_sub = dict(sub)
        if pause_until_utc:
            updated_sub["pause_until_utc"] = pause_until_utc
        else:
            updated_sub.pop("pause_until_utc", None)
        if isinstance(remaining, int):
            updated_sub["last_remaining_bytes"] = int(remaining)
        if isinstance(total_bytes, int):
            updated_sub["last_total_bytes"] = int(total_bytes)
        if isinstance(used_bytes, int):
            updated_sub["last_used_bytes"] = int(used_bytes)
        if isinstance(expire, int):
            updated_sub["last_expire"] = int(expire)
        if isinstance(reset_days_hint, int):
            updated_sub["last_reset_days"] = int(reset_days_hint)
        updated_subscriptions.append(updated_sub)

        report_rows.append(
            {
                "name": subscription_name,
                "url_redacted": redact_url(url),
                "remaining_bytes": remaining,
                "total_bytes": total_bytes,
                "used_bytes": used_bytes,
                "expire": expire,
                "status": status,
                "reset_days": int(reset_days_hint)
                if isinstance(reset_days_hint, int)
                else None,
                "pause_until_utc": pause_until_utc,
            }
        )

        if (
            status.startswith("paused")
            or status.startswith("removed")
            or status.startswith("depleted")
            or should_skip_proxies
        ):
            if prune:
                prunable_subscriptions.append(updated_sub)
            continue
        for proxy in cleaned_proxies:
            proxy_name = proxy.get("name")
            if not proxy_name or proxy_name in seen_names:
                continue
            proxies.append(proxy)
            seen_names.add(proxy_name)
            proxy_sources[str(proxy_name)] = subscription_name
    if not proxies:
        raise ValueError("未收集到任何代理节点")
    return proxies, prunable_subscriptions, report_rows, updated_subscriptions, proxy_sources


def build() -> dict:
    started_at = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status: dict[str, object] = {
        "started_at_utc": started_at,
        "success": False,
        "prune_low_traffic": False,
        "subscription_min_remaining_gb": None,
        "removed_subscriptions": [],
        "subscription_report_path": relpath_posix(SUBSCRIPTION_REPORT_PATH),
        "generated_config_path": relpath_posix(OUTPUT_CONFIG),
        "generated_provider_path": relpath_posix(PROVIDER_PATH),
    }

    template_text = TEMPLATE_PATH.read_text(encoding="utf-8")
    template = load_yaml(template_text)
    if not isinstance(template, dict):
        hint = (
            "（本地请先安装 PyYAML: python -m pip install pyyaml）"
            if yaml is None
            else ""
        )
        raise SystemExit(f"模板 test.yaml 解析失败 {hint}".strip())

    use_local_provider = env_flag("USE_LOCAL_PROVIDER")
    prune_low_traffic = env_flag("PRUNE_SUBSCRIPTIONS_LOW_TRAFFIC")
    report_traffic = env_flag("REPORT_SUBSCRIPTION_TRAFFIC") or prune_low_traffic
    status["prune_low_traffic"] = bool(prune_low_traffic and not use_local_provider)

    min_remaining_bytes: Optional[int] = None
    if prune_low_traffic and not use_local_provider:
        try:
            min_gb = float(os.getenv("SUBSCRIPTION_MIN_REMAINING_GB", "1"))
        except ValueError:
            min_gb = 1.0
        status["subscription_min_remaining_gb"] = min_gb
        min_remaining_bytes = max(0, int(min_gb * BYTES_IN_GIB))

    raw_subscriptions = template.get("subscriptions") or []
    subscriptions = normalize_subscriptions(raw_subscriptions)
    proxies = []
    prunable_subscriptions = []
    report_rows: list[dict[str, object]] = []
    updated_subscriptions: list[dict[str, object]] = []
    proxy_sources: dict[str, str] = {}

    if use_local_provider:
        proxies = load_proxies_from_provider(PROVIDER_PATH)
        if not proxies and subscriptions:
            (
                proxies,
                prunable_subscriptions,
                report_rows,
                updated_subscriptions,
                proxy_sources,
            ) = fetch_proxies(subscriptions, min_remaining_bytes=min_remaining_bytes)
        elif not proxies:
            raise SystemExit(
                "USE_LOCAL_PROVIDER=1 但 providers/all.yaml 不存在或缺少 proxies，且模板也未提供可用订阅"
            )
    elif subscriptions:
        (
            proxies,
            prunable_subscriptions,
            report_rows,
            updated_subscriptions,
            proxy_sources,
        ) = fetch_proxies(subscriptions, min_remaining_bytes=min_remaining_bytes)
    else:
        raise SystemExit("请在 test.yaml 的 subscriptions 中填写至少一个 Clash 订阅链接")

    # Persist subscription state (pause_until / last_remaining / prune-expired) back to `test.yaml`.
    # IMPORTANT: do this before we inject generated display groups into `template`.
    if (
        yaml
        and (not use_local_provider)
        and updated_subscriptions
        and (prune_low_traffic or any("pause_until_utc" in s for s in updated_subscriptions))
    ):
        new_subscriptions = []
        for s in updated_subscriptions:
            entry: dict[str, object] = {"name": s.get("name"), "url": s.get("url")}
            if isinstance(s.get("pause_until_utc"), str) and str(s.get("pause_until_utc")).strip():
                entry["pause_until_utc"] = s.get("pause_until_utc")
            for key in (
                "last_remaining_bytes",
                "last_total_bytes",
                "last_used_bytes",
                "last_expire",
                "last_reset_days",
            ):
                if isinstance(s.get(key), int):
                    entry[key] = int(s.get(key))
            new_subscriptions.append(entry)

        prunable_urls = {
            str(s.get("url") or "") for s in prunable_subscriptions if s.get("url")
        }
        kept_subscriptions = [
            s for s in new_subscriptions if str(s.get("url") or "") not in prunable_urls
        ]

        if kept_subscriptions:
            wrote_back = persist_updated_subscriptions_yaml(
                TEMPLATE_PATH, template, kept_subscriptions
            )
            status["subscriptions_state_written_back"] = bool(wrote_back)
        else:
            status["subscriptions_state_written_back"] = False

    if env_flag("FILTER_SUBSCRIPTION_NOISE"):
        proxies, removed_names = filter_proxies_by_name_noise(proxies)
        if removed_names:
            status["filtered_noise_proxy_count"] = len(removed_names)
            status["filtered_noise_proxy_samples"] = removed_names[:10]
            print(f"已过滤疑似引导/广告条目: {len(removed_names)} 个")

    if report_traffic and report_rows:
        write_subscription_report(SUBSCRIPTION_REPORT_PATH, report_rows)
        print(f"订阅流量报告: {SUBSCRIPTION_REPORT_PATH}")

    removed_for_status = []
    for row in report_rows:
        if str(row.get("status", "")).startswith("removed"):
            removed_for_status.append(
                {
                    "name": row.get("name"),
                    "url_redacted": row.get("url_redacted"),
                    "remaining_gib": format_gib(
                        row.get("remaining_bytes")
                        if isinstance(row.get("remaining_bytes"), int)
                        else None
                    ),
                    "status": row.get("status"),
                }
            )
    status["removed_subscriptions"] = removed_for_status

    if prunable_subscriptions:
        prunable_urls = [str(s.get("url") or "") for s in prunable_subscriptions]
        prunable_hint = ", ".join(redact_url(u) for u in prunable_urls[:3])
        suffix = (
            ""
            if len(prunable_urls) <= 3
            else f" ... (+{len(prunable_urls) - 3})"
        )
        print(f"检测到订阅已到期，将从 test.yaml 移除: {prunable_hint}{suffix}")

    if env_flag("EMBED_SUBSCRIPTION_TRAFFIC_GROUPS") and report_rows:
        include_parent = env_flag("EMBED_SUBSCRIPTION_TRAFFIC_PARENT_GROUP")
        inject_subscription_traffic_groups(
            template, report_rows, include_parent_group=include_parent
        )
        status["embedded_subscription_traffic_groups"] = True
        status["embedded_subscription_traffic_parent_group"] = bool(include_parent)

    proxy_names = [p.get("name") for p in proxies if p.get("name")]
    nano_proxy_names = {
        name
        for name, source in proxy_sources.items()
        if isinstance(source, str) and source.strip().lower() == "nano"
    }

    template.pop("subscriptions", None)
    template.setdefault("proxy-providers", {})
    template["proxy-providers"]["all"] = {
        "type": "file",
        "path": relpath_posix(PROVIDER_PATH),
        "health-check": None,
    }
    # 同步写入 proxies，兼容不读取 provider 的客户端
    template["proxies"] = proxies

    # 根据名称分类,将节点名称平铺进分组
    name_sets = {
        "全节点": list(dict.fromkeys(proxy_names)),
        "香港": [],
        "东南亚": [],
        "欧美": [],
        "其他": [],
        "币安": [],
        "pikpak": [],
        "ai组": list(dict.fromkeys(proxy_names)),
        "twitter": list(dict.fromkeys(proxy_names)),
        "Microsoft": [],
    }
    hk_re = re.compile(r"(hk|hong|港|香江|xiangjiang|gp(?!t)|gp\d+)", re.IGNORECASE)
    proxy_keyword_re = re.compile(r"(hong\s*kong|singapore|japan)", re.IGNORECASE)
    sg_re = re.compile(r"(sg|singapore|新加坡)", re.IGNORECASE)
    sea_re = re.compile(
        r"(sg|singapore|sea|vn|vietnam|th|thailand|my|malaysia|ph|phil|id|indo|jp|japan|tw|taiwan|越南|泰国|马来|菲|印尼|新加坡|日本|台|臺)",
        re.IGNORECASE,
    )
    eu_re = re.compile(
        r"(us|usa|uk|gb|eu|europe|de|fr|nl|ca|america|美|英|欧|德|法|荷|加)",
        re.IGNORECASE,
    )
    ai_noise_re = re.compile(r"(expire\s*date|剩余流量|套餐到期|官网|流量|GB\s*\||bit\.ly|github|t\.me)", re.IGNORECASE)
    # ai组默认节点: 同时包含"香港"和"3.5倍"
    ai_default_re = re.compile(r"(香港.*3\.5倍|3\.5倍.*香港)", re.IGNORECASE)

    # pikpak默认节点: 同时包含"美国"和"1.1倍"
    pikpak_default_re = re.compile(r"(美国.*1\.1倍|1\.1倍.*美国)", re.IGNORECASE)
    # 币安组筛选: 同时包含"香港"和"3.5倍"
    binance_re = re.compile(r"(香港.*3\.5倍|3\.5倍.*香港)", re.IGNORECASE)
    # Microsoft组筛选: 包含"香港"或"3.5倍"
    microsoft_re = re.compile(r"(香港|3\.5倍)", re.IGNORECASE)

    # 为 ai组 排序：符合条件的节点排在前面
    ai_group_conf = next((g for g in template.get("proxy-groups", []) if isinstance(g, dict) and g.get("name") == "ai组"), {})
    ai_exclude_subs = ai_group_conf.get("exclude-subscriptions", [])
    ai_exclude_filter = ai_group_conf.get("exclude-filter", "")
    ai_exclude_re = re.compile(str(ai_exclude_filter), re.IGNORECASE) if ai_exclude_filter else None

    ai_candidates = [
        n
        for n in proxy_names
        if (proxy_sources.get(n) not in ai_exclude_subs) 
        and (not ai_exclude_re or not ai_exclude_re.search(n))
        and not ai_noise_re.search(n)
    ]
    ai_default_nodes = [n for n in ai_candidates if ai_default_re.search(n)]
    ai_other_nodes = [n for n in ai_candidates if not ai_default_re.search(n)]
    name_sets["ai组"] = list(dict.fromkeys(ai_default_nodes + ai_other_nodes))

    hk_group_conf = next((g for g in template.get("proxy-groups", []) if isinstance(g, dict) and g.get("name") == "香港"), {})
    hk_exclude_subs = hk_group_conf.get("exclude-subscriptions", [])

    for n in proxy_names:
        matched_hk = bool(hk_re.search(n))
        matched_sg = bool(sg_re.search(n))
        matched_sea = bool(sea_re.search(n))
        matched_eu = bool(eu_re.search(n))
        matched_binance = bool(binance_re.search(n))
        matched_microsoft = bool(microsoft_re.search(n))

        if matched_hk and proxy_sources.get(n) not in hk_exclude_subs:
            name_sets["香港"].append(n)

        
        # 币安组: 只保留同时包含"香港"和"3.5倍"的节点
        if matched_binance:
            name_sets["币安"].append(n)
        
        # Microsoft组: 包含"香港"或"3.5倍"的节点
        if matched_microsoft:
            name_sets["Microsoft"].append(n)
        
        if not matched_hk:
            # pikpak: 除香港外的全节点
            name_sets["pikpak"].append(n)
        
        if matched_sg:
            # 移除新加坡节点加入币安组的逻辑
            pass
        if matched_sea:
            name_sets["东南亚"].append(n)
        elif matched_eu:
            # 如果命中东南亚，则不放入欧美，避免新加坡等误入
            name_sets["欧美"].append(n)
        elif not matched_hk:
            # 未匹配任何区域则归入其他
            name_sets["其他"].append(n)

    # 为 pikpak 排序：符合"美国+1.1倍"条件的节点排在前面
    pikpak_default_nodes = [n for n in name_sets["pikpak"] if pikpak_default_re.search(n)]
    pikpak_other_nodes = [n for n in name_sets["pikpak"] if not pikpak_default_re.search(n)]
    name_sets["pikpak"] = list(dict.fromkeys(pikpak_default_nodes + pikpak_other_nodes))

    def dedup(seq):
        return list(dict.fromkeys(seq))

    # 筛选出包含 "3.5倍" 的节点
    x35_nodes = [n for n in proxy_names if "3.5倍" in n or "3.5x" in n.lower()]
    proxy_group_conf = next((g for g in template.get("proxy-groups", []) if g.get("name") == "Proxy"), {})
    extra_keywords = proxy_group_conf.get("extra-keywords", [])
    if extra_keywords:
        kw_re = re.compile(f"({'|'.join(re.escape(k) for k in extra_keywords)})", re.IGNORECASE)
        keyword_nodes = [n for n in proxy_names if kw_re.search(n)]
    else:
        keyword_nodes = []

    for group in template.get("proxy-groups", []):
        if not isinstance(group, dict):
            continue
        gname = group.get("name")
        extra_keywords = group.get("extra-keywords", [])
        
        # 提取关键字节点逻辑通用化
        keyword_nodes = []
        if extra_keywords:
            kw_re = re.compile(f"({'|'.join(re.escape(k) for k in extra_keywords)})", re.IGNORECASE)
            keyword_nodes = [n for n in proxy_names if kw_re.search(n)]

        if gname == "Proxy":
            # 在 Proxy 组中追加 3.5倍 节点，以及 extra-keywords 节点，然后是子组
            group["proxies"] = dedup(
                x35_nodes
                + keyword_nodes
                + ["香港", "东南亚", "欧美", "全节点", "其他"]
            )
        elif gname in name_sets:
            # 基础节点 + 关键字提取节点
            group["proxies"] = dedup(name_sets[gname] + keyword_nodes)
        
        # 移除自定义字段
        group.pop("extra-keywords", None)
        group.pop("exclude-subscriptions", None)
        group.pop("exclude-filter", None)


    PROVIDER_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_CONFIG.parent.mkdir(parents=True, exist_ok=True)

    def dump(data):
        return dump_yaml(data)

    # 按照 Clash 标准顺序重新组织配置
    ordered_config = {}

    # 1. 基础配置
    if "mode" in template:
        ordered_config["mode"] = template["mode"]
    if "port" in template:
        ordered_config["port"] = template["port"]
    if "socks-port" in template:
        ordered_config["socks-port"] = template["socks-port"]
    if "allow-lan" in template:
        ordered_config["allow-lan"] = template["allow-lan"]
    if "bind-address" in template:
        ordered_config["bind-address"] = template["bind-address"]
    if "log-level" in template:
        ordered_config["log-level"] = template["log-level"]
    if "external-controller" in template:
        ordered_config["external-controller"] = template["external-controller"]

    # 2. 代理提供者和节点(必须在 proxy-groups 之前)
    if "proxy-providers" in template:
        ordered_config["proxy-providers"] = template["proxy-providers"]
    if "proxies" in template:
        ordered_config["proxies"] = template["proxies"]

    # 3. 代理组
    if "proxy-groups" in template:
        ordered_config["proxy-groups"] = template["proxy-groups"]

    # 4. 规则提供者
    if "rule-providers" in template:
        ordered_config["rule-providers"] = template["rule-providers"]

    # 5. 路由规则
    if "rules" in template:
        ordered_config["rules"] = template["rules"]

    # 6. 其他未处理的字段
    for key, value in template.items():
        if key not in ordered_config:
            ordered_config[key] = value

    PROVIDER_PATH.write_text(dump({"proxies": proxies}), encoding="utf-8")
    OUTPUT_CONFIG.write_text(dump(ordered_config), encoding="utf-8")
    print(f"生成完成: {OUTPUT_CONFIG} (provider: {PROVIDER_PATH})")

    status["success"] = True
    status["proxy_count"] = len(proxy_names)
    finished_at = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status["finished_at_utc"] = finished_at
    return status


def main():
    status: dict[str, object] = {}
    try:
        status = build()
    except SystemExit as e:
        status = status or {
            "started_at_utc": dt.datetime.now(tz=dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "success": False,
        }
        status["error"] = str(e)
        raise
    except Exception as e:
        status = status or {
            "started_at_utc": dt.datetime.now(tz=dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "success": False,
        }
        status["error"] = str(e)
        raise
    finally:
        try:
            write_build_status(BUILD_STATUS_PATH, status)
            print(f"构建状态: {BUILD_STATUS_PATH}")
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
