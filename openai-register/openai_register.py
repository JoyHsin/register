import json
import os
import re
import sys
import time
import random
import string
import secrets
import hashlib
import base64
import argparse
import webbrowser
import threading
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Any, Dict, Optional, List
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import asyncio
import requests as py_requests
try:
    import aiohttp
except ImportError:
    aiohttp = None

from curl_cffi import requests

OUT_DIR = Path(__file__).parent.resolve()

# ========== 自动加载 .env 文件（无需 python-dotenv）==========
def _load_dotenv(env_path: Path):
    if not env_path.is_file():
        return
    with env_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            # 去掉引号包裹
            if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
                val = val[1:-1]
            else:
                # 无引号时裁掉行内注释（空格+#开头）
                val = val.split(" #")[0].split("\t#")[0].strip()
            if key and key not in os.environ:  # 不覆盖已有的环境变量
                os.environ[key] = val

_load_dotenv(OUT_DIR / ".env")


# ========== 提前定义工具函数（模块级常量初始化时需要用到）==========
def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_REDIRECT_URI = "http://localhost:1455/auth/callback"
DEFAULT_SCOPE = "openid email profile offline_access"

SUB2API_BASE_URL = str(os.getenv("SUB2API_BASE_URL") or "").strip().rstrip("/")
SUB2API_ADMIN_API_KEY = str(os.getenv("SUB2API_ADMIN_API_KEY") or "").strip()
SUB2API_BEARER = str(os.getenv("SUB2API_BEARER") or "").strip()
SUB2API_EMAIL = str(os.getenv("SUB2API_EMAIL") or "").strip()
SUB2API_PASSWORD = str(os.getenv("SUB2API_PASSWORD") or "").strip()

# ========== 自定义域名邮箱配置 ==========
CUSTOM_EMAIL_DOMAIN = str(os.getenv("CUSTOM_EMAIL_DOMAIN") or "").strip()
CUSTOM_EMAIL_SUFFIX = str(os.getenv("CUSTOM_EMAIL_SUFFIX") or "").strip()
CUSTOM_EMAIL_RANDOM_LENGTH = max(4, int(os.getenv("CUSTOM_EMAIL_RANDOM_LENGTH") or "6"))
QQ_IMAP_HOST = str(os.getenv("QQ_IMAP_HOST") or "imap.qq.com").strip()
QQ_IMAP_PORT = int(os.getenv("QQ_IMAP_PORT") or "993")
QQ_IMAP_USER = str(os.getenv("QQ_IMAP_USER") or "").strip()
QQ_IMAP_PASS = str(os.getenv("QQ_IMAP_PASS") or "").strip()
QQ_IMAP_FOLDER = str(os.getenv("QQ_IMAP_FOLDER") or "INBOX").strip()

# ========== 运行时行为全局配置（均可通过 .env 配置，CLI 参数优先） ==========
DEFAULT_EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT") or "900")
DEFAULT_OTP_RESEND_INTERVAL = int(os.getenv("OTP_RESEND_INTERVAL") or "300")
DEFAULT_SLEEP_MIN = int(os.getenv("SLEEP_MIN") or "5")
DEFAULT_SLEEP_MAX = int(os.getenv("SLEEP_MAX") or "30")
DEFAULT_MAIL_PROVIDER = str(os.getenv("MAIL_PROVIDER") or "auto").strip().lower()
DEFAULT_PROXY = str(os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or "").strip()

# ========== CPA 全局配置 ==========
DEFAULT_CPA_WORKERS = int(os.getenv("CPA_WORKERS") or "1")
DEFAULT_CPA_TIMEOUT = int(os.getenv("CPA_TIMEOUT") or "12")
DEFAULT_CPA_RETRIES = int(os.getenv("CPA_RETRIES") or "1")
DEFAULT_CPA_USED_THRESHOLD = int(os.getenv("CPA_USED_THRESHOLD") or "95")
DEFAULT_CPA_TARGET_COUNT = int(os.getenv("CPA_TARGET_COUNT") or "300")
DEFAULT_CPA_OAUTH_POLL_INTERVAL = int(os.getenv("CPA_OAUTH_POLL_INTERVAL") or "5")
DEFAULT_CPA_OAUTH_TIMEOUT = int(os.getenv("CPA_OAUTH_TIMEOUT") or "900")
DEFAULT_CPA_OAUTH_OPEN_BROWSER = _as_bool(os.getenv("CPA_OAUTH_OPEN_BROWSER"))
DEFAULT_CPA_OAUTH_NO_PROMPT = _as_bool(os.getenv("CPA_OAUTH_NO_PROMPT"))
DEFAULT_CPA_OAUTH_LISTEN = _as_bool(os.getenv("CPA_OAUTH_LISTEN") if os.getenv("CPA_OAUTH_LISTEN") is not None else "true")
DEFAULT_CPA_UPLOAD = _as_bool(os.getenv("CPA_UPLOAD"))
DEFAULT_CPA_CLEAN = _as_bool(os.getenv("CPA_CLEAN"))
DEFAULT_PRUNE_LOCAL = _as_bool(os.getenv("PRUNE_LOCAL"))
DEFAULT_SUB2API_UPLOAD = _as_bool(os.getenv("AUTO_UPLOAD_SUB2API"))

# ========== 自定义域名邮箱客户端（Cloudflare → QQ IMAP） ==========
import imaplib
import email as _email_lib
from email.header import decode_header as _decode_header
from email.utils import parsedate_to_datetime


def _decode_mime_str(raw) -> str:
    """解码 MIME 编码的头字段（Subject/From/To 等）"""
    if not raw:
        return ""
    parts = []
    for byt, enc in _decode_header(str(raw)):
        if isinstance(byt, bytes):
            try:
                parts.append(byt.decode(enc or "utf-8", errors="replace"))
            except Exception:
                parts.append(byt.decode("utf-8", errors="replace"))
        else:
            parts.append(str(byt))
    return " ".join(parts)


_OTP_PATTERNS = [
    r"your\s+chatgpt\s+code\s+is\s*(\d{6})",
    r"你的\s*chatgpt\s*代码为\s*(\d{6})",
    r"enter\s+this\s+temporary\s+verification\s+code\s+to\s+continue[:：]?\s*(\d{6})",
    r"输入此临时验证码以继续[:：]?\s*(\d{6})",
    r"verification\s+code(?:\s+to\s+continue)?[:：]?\s*(\d{6})",
    r"验证码(?:为|是)?[:：]?\s*(\d{6})",
]

OTP_TIME_SKEW_SEC = 120.0
LOGIN_OTP_INITIAL_SEND_GRACE_SEC = max(0, int(os.getenv("LOGIN_OTP_INITIAL_SEND_GRACE_SEC") or "45"))
LOGIN_WORKSPACE_SETTLE_TIMEOUT_SEC = max(0, int(os.getenv("LOGIN_WORKSPACE_SETTLE_TIMEOUT_SEC") or "45"))
LOGIN_WORKSPACE_SETTLE_POLL_SEC = max(1, int(os.getenv("LOGIN_WORKSPACE_SETTLE_POLL_SEC") or "3"))
CPA_OAUTH_STATUS_ERROR_TOLERANCE = max(1, int(os.getenv("CPA_OAUTH_STATUS_ERROR_TOLERANCE") or "10"))


@dataclass(frozen=True)
class OtpCandidate:
    code: str
    received_ts: Optional[float]
    source: str = ""


@dataclass
class LocalOAuthCallbackCapture:
    callback_url: str = ""
    error: str = ""


@dataclass
class LocalOAuthCallbackServer:
    httpd: ThreadingHTTPServer
    thread: threading.Thread
    capture: LocalOAuthCallbackCapture
    event: threading.Event
    host: str
    port: int

    def shutdown(self) -> None:
        try:
            self.httpd.shutdown()
        except Exception:
            pass
        try:
            self.httpd.server_close()
        except Exception:
            pass
        if self.thread.is_alive():
            self.thread.join(timeout=2)


def _unique_codes_in_order(values: List[str]) -> List[str]:
    ordered: List[str] = []
    for value in values:
        if value and value not in ordered:
            ordered.append(value)
    return ordered


def _extract_ranked_otp_codes(subject: str = "", body: str = "") -> List[str]:
    """优先提取当前 OpenAI 邮件里的有效 OTP，再附带兜底候选。"""
    subject = str(subject or "")
    body = str(body or "")
    combined = f"{subject}\n{body}".strip()
    subject_ranked: List[str] = []
    ranked: List[str] = []

    def _collect_pattern_hits(text: str, bucket: List[str]) -> None:
        if not text:
            return
        for pattern in _OTP_PATTERNS:
            for match in re.findall(pattern, text, flags=re.IGNORECASE):
                bucket.append(match)

    # 主题通常最干净，优先级最高；正文只取前半段，尽量避开引用的历史邮件内容。
    _collect_pattern_hits(subject, subject_ranked)
    subject_ranked.extend(re.findall(r"(?<!\d)(\d{6})(?!\d)", subject))
    subject_ranked = _unique_codes_in_order(subject_ranked)
    if subject_ranked:
        return subject_ranked

    _collect_pattern_hits(body[:1200], ranked)
    _collect_pattern_hits(combined[:1200], ranked)

    fallback_pool = []
    fallback_pool.extend(re.findall(r"(?<!\d)(\d{6})(?!\d)", body[:400]))
    fallback_pool.extend(re.findall(r"(?<!\d)(\d{6})(?!\d)", body))
    ranked.extend(fallback_pool)
    return _unique_codes_in_order(ranked)


def _parse_timestamp_value(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        ts = float(raw)
        return ts / 1000.0 if ts > 10_000_000_000 else ts
    if isinstance(raw, datetime):
        return raw.timestamp()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")

    text = str(raw).strip()
    if not text:
        return None

    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        ts = float(text)
        return ts / 1000.0 if ts > 10_000_000_000 else ts

    try:
        dt = parsedate_to_datetime(text)
        if dt:
            return dt.timestamp()
    except Exception:
        pass

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except Exception:
            pass
    return None


def _parse_provider_message_timestamp(data: Dict[str, Any]) -> Optional[float]:
    preferred_keys = [
        "received_at", "receivedAt", "delivered_at", "deliveredAt",
        "created_at", "createdAt", "date", "sent_at", "sentAt",
        "timestamp", "time", "created", "updated_at", "updatedAt",
    ]
    for key in preferred_keys:
        if key in data:
            ts = _parse_timestamp_value(data.get(key))
            if ts is not None:
                return ts
    for key, value in data.items():
        key_lower = str(key).lower()
        if "date" in key_lower or "time" in key_lower:
            ts = _parse_timestamp_value(value)
            if ts is not None:
                return ts
    return None


def _min_allowed_otp_timestamp(min_received_ts: Optional[float], time_skew_sec: float = OTP_TIME_SKEW_SEC) -> Optional[float]:
    if min_received_ts is None:
        return None
    return min_received_ts - max(0.0, float(time_skew_sec))


def _sort_otp_candidates(candidates: List[OtpCandidate]) -> List[OtpCandidate]:
    return sorted(
        candidates,
        key=lambda item: (item.received_ts if item.received_ts is not None else float("-inf")),
        reverse=True,
    )


def _get_auth_cookie_payload(session: Any) -> Dict[str, Any]:
    auth_cookie = session.cookies.get("oai-client-auth-session", domain=".auth.openai.com") or session.cookies.get("oai-client-auth-session")
    if not auth_cookie:
        return {}
    try:
        return _decode_jwt_segment(auth_cookie.split(".")[0])
    except Exception:
        return {}


def _has_cookie(session: Any, cookie_name: str) -> bool:
    jar = getattr(session.cookies, "jar", None)
    if jar is not None:
        return any((getattr(c, "name", "") or "") == cookie_name for c in list(jar))
    return cookie_name in set(getattr(session.cookies, "keys", lambda: [])())


def _extract_code_from_url(url: str) -> str:
    return str(_parse_callback_url(url).get("code") or "").strip()


def _decode_oauth_session_cookie(session: Any) -> Dict[str, Any]:
    jar = getattr(session.cookies, "jar", None)
    cookie_items = list(jar) if jar is not None else []
    for cookie in cookie_items:
        if "oai-client-auth-session" not in str(getattr(cookie, "name", "") or ""):
            continue

        raw_val = str(getattr(cookie, "value", "") or "").strip()
        if not raw_val:
            continue

        candidates = [raw_val]
        try:
            decoded = urllib.parse.unquote(raw_val)
            if decoded != raw_val:
                candidates.append(decoded)
        except Exception:
            pass

        for candidate in candidates:
            try:
                if (candidate.startswith('"') and candidate.endswith('"')) or (candidate.startswith("'") and candidate.endswith("'")):
                    candidate = candidate[1:-1]
                part = candidate.split(".")[0] if "." in candidate else candidate
                pad = "=" * ((4 - (len(part) % 4)) % 4)
                data = json.loads(base64.urlsafe_b64decode((part + pad).encode("ascii")).decode("utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                continue
    return {}


def _oauth_json_headers(referer: str, did: str, user_agent: str = UA) -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://auth.openai.com",
        "Referer": referer,
        "User-Agent": user_agent,
        "oai-device-id": did,
    }


def _oauth_allow_redirect_extract_code(session: Any, url: str, referer: str | None = None, user_agent: str = UA) -> Optional[str]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": user_agent,
    }
    if referer:
        headers["Referer"] = referer

    try:
        resp = session.get(url, headers=headers, allow_redirects=True, timeout=30)
        final_url = str(getattr(resp, "url", "") or "")
        code = _extract_code_from_url(final_url)
        if code:
            print("[OAuth] allow_redirect 命中最终 URL code")
            return code

        for item in getattr(resp, "history", []) or []:
            loc = str(item.headers.get("Location", "") or "")
            code = _extract_code_from_url(loc) or _extract_code_from_url(str(getattr(item, "url", "") or ""))
            if code:
                print("[OAuth] allow_redirect 命中 history code")
                return code
    except Exception as e:
        localhost_match = re.search(r'(https?://localhost[^\s\'\"]+)', str(e))
        if localhost_match:
            code = _extract_code_from_url(localhost_match.group(1))
            if code:
                print("[OAuth] allow_redirect 从 localhost 异常提取 code")
                return code
        print(f"[OAuth] allow_redirect 异常: {e}")
    return None


def _oauth_follow_for_code(session: Any, start_url: str, referer: str | None = None, user_agent: str = UA, max_hops: int = 16) -> tuple[Optional[str], str]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": user_agent,
    }
    if referer:
        headers["Referer"] = referer

    current_url = str(start_url or "").strip()
    last_url = current_url

    for hop in range(max_hops):
        try:
            resp = session.get(current_url, headers=headers, allow_redirects=False, timeout=30)
        except Exception as e:
            localhost_match = re.search(r'(https?://localhost[^\s\'\"]+)', str(e))
            if localhost_match:
                code = _extract_code_from_url(localhost_match.group(1))
                if code:
                    print(f"[OAuth] follow[{hop + 1}] 命中 localhost 回调")
                    return code, localhost_match.group(1)
            print(f"[OAuth] follow[{hop + 1}] 请求异常: {e}")
            return None, last_url

        last_url = str(getattr(resp, "url", "") or current_url)
        print(f"[OAuth] follow[{hop + 1}] {resp.status_code} {last_url[:140]}")
        code = _extract_code_from_url(last_url)
        if code:
            return code, last_url

        if resp.status_code not in (301, 302, 303, 307, 308):
            return None, last_url

        loc = str(resp.headers.get("Location", "") or "").strip()
        if not loc:
            return None, last_url
        if loc.startswith("/"):
            loc = f"https://auth.openai.com{loc}"

        code = _extract_code_from_url(loc)
        if code:
            return code, loc

        current_url = loc
        headers["Referer"] = last_url

    return None, last_url


def _oauth_submit_workspace_and_org(session: Any, consent_url: str, did: str, user_agent: str = UA) -> Optional[str]:
    session_data = _decode_oauth_session_cookie(session)
    if not session_data:
        cookie_names = [getattr(c, "name", "") for c in list(getattr(session.cookies, "jar", []) or [])]
        print(f"[OAuth] 无法解码 oai-client-auth-session, cookies={cookie_names[:12]}")
        return None

    workspaces = session_data.get("workspaces", []) or []
    if not workspaces:
        print("[OAuth] session 中没有 workspace 信息")
        return None

    workspace_id = str((workspaces[0] or {}).get("id") or "").strip()
    if not workspace_id:
        print("[OAuth] workspace_id 为空")
        return None

    headers = _oauth_json_headers(consent_url, did, user_agent=user_agent)
    resp = session.post(
        "https://auth.openai.com/api/accounts/workspace/select",
        json={"workspace_id": workspace_id},
        headers=headers,
        allow_redirects=False,
        timeout=30,
    )
    print(f"[OAuth] workspace/select -> {resp.status_code}")

    if resp.status_code in (301, 302, 303, 307, 308):
        loc = str(resp.headers.get("Location", "") or "").strip()
        if loc.startswith("/"):
            loc = f"https://auth.openai.com{loc}"
        code = _extract_code_from_url(loc)
        if code:
            return code
        code, _ = _oauth_follow_for_code(session, loc, referer=consent_url, user_agent=user_agent)
        return code or _oauth_allow_redirect_extract_code(session, loc, referer=consent_url, user_agent=user_agent)

    if resp.status_code != 200:
        print(f"[OAuth] workspace/select 失败: {resp.status_code}")
        return None

    try:
        ws_data = resp.json() or {}
    except Exception:
        print("[OAuth] workspace/select 响应不是 JSON")
        return None

    ws_next = str(ws_data.get("continue_url") or "").strip()
    page_payload = ((ws_data.get("page") or {}).get("payload") or {})
    orgs = (
        ((ws_data.get("data") or {}).get("orgs"))
        or (((page_payload.get("data") or {}).get("orgs")))
        or []
    )
    ws_page = str((ws_data.get("page") or {}).get("type") or "")
    print(f"[OAuth] workspace/select page={ws_page or '-'} next={(ws_next or '-')[:140]}")

    org_id = ""
    project_id = ""
    if orgs:
        org = orgs[0] or {}
        org_id = str(org.get("id") or "").strip()
        project_id = str(org.get("default_project_id") or "").strip()
        if not project_id:
            projects = org.get("projects") or []
            if projects:
                project_id = str((projects[0] or {}).get("id") or "").strip()

    if org_id:
        org_body = {"org_id": org_id}
        if project_id:
            org_body["project_id"] = project_id
        org_referer = ws_next if ws_next.startswith("http") else (f"https://auth.openai.com{ws_next}" if ws_next.startswith("/") else consent_url)
        org_headers = _oauth_json_headers(org_referer or consent_url, did, user_agent=user_agent)

        resp_org = session.post(
            "https://auth.openai.com/api/accounts/organization/select",
            json=org_body,
            headers=org_headers,
            allow_redirects=False,
            timeout=30,
        )
        print(f"[OAuth] organization/select -> {resp_org.status_code}")

        if resp_org.status_code in (301, 302, 303, 307, 308):
            loc = str(resp_org.headers.get("Location", "") or "").strip()
            if loc.startswith("/"):
                loc = f"https://auth.openai.com{loc}"
            code = _extract_code_from_url(loc)
            if code:
                return code
            code, _ = _oauth_follow_for_code(session, loc, referer=org_referer, user_agent=user_agent)
            return code or _oauth_allow_redirect_extract_code(session, loc, referer=org_referer, user_agent=user_agent)

        if resp_org.status_code == 200:
            try:
                org_data = resp_org.json() or {}
            except Exception:
                print("[OAuth] organization/select 响应不是 JSON")
                return None

            org_next = str(org_data.get("continue_url") or "").strip()
            org_page = str((org_data.get("page") or {}).get("type") or "")
            print(f"[OAuth] organization/select page={org_page or '-'} next={(org_next or '-')[:140]}")
            if org_next:
                if org_next.startswith("/"):
                    org_next = f"https://auth.openai.com{org_next}"
                code, _ = _oauth_follow_for_code(session, org_next, referer=org_referer, user_agent=user_agent)
                return code or _oauth_allow_redirect_extract_code(session, org_next, referer=org_referer, user_agent=user_agent)

    if ws_next:
        if ws_next.startswith("/"):
            ws_next = f"https://auth.openai.com{ws_next}"
        code, _ = _oauth_follow_for_code(session, ws_next, referer=consent_url, user_agent=user_agent)
        return code or _oauth_allow_redirect_extract_code(session, ws_next, referer=consent_url, user_agent=user_agent)

    return None


def _follow_redirect_chain(session: Any, start_url: str, timeout: int = 15, max_hops: int = 20) -> Dict[str, Any]:
    current_url = str(start_url or "").strip()
    if not current_url:
        return {"callback_url": "", "final_url": "", "status_code": 0, "error": "empty_start_url"}

    try:
        resp = session.get(current_url, allow_redirects=False, timeout=timeout)
        final_url = str(getattr(resp, "url", "") or current_url)
        for _ in range(max_hops):
            loc = str(resp.headers.get("Location", "") or "").strip()
            if loc.startswith("http://localhost"):
                return {"callback_url": loc, "final_url": loc, "status_code": resp.status_code, "error": ""}
            if resp.status_code not in (301, 302, 303) or not loc:
                return {"callback_url": "", "final_url": final_url, "status_code": resp.status_code, "error": ""}
            resp = session.get(loc, allow_redirects=False, timeout=timeout)
            final_url = str(getattr(resp, "url", "") or loc)
        return {"callback_url": "", "final_url": final_url, "status_code": resp.status_code, "error": "max_hops"}
    except Exception as e:
        return {"callback_url": "", "final_url": current_url, "status_code": 0, "error": str(e)}


def _stabilize_login_session(
    session: Any,
    oauth_auth_url: str,
    consent_url: str,
    timeout_sec: int = LOGIN_WORKSPACE_SETTLE_TIMEOUT_SEC,
    poll_sec: int = LOGIN_WORKSPACE_SETTLE_POLL_SEC,
) -> Dict[str, Any]:
    current_consent_url = str(consent_url or "").strip()
    last_auth_json = _get_auth_cookie_payload(session)
    last_consent_data: Dict[str, Any] = {}
    callback_url = ""
    final_oauth_url = ""

    if timeout_sec <= 0:
        return {
            "auth_json": last_auth_json,
            "consent_url": current_consent_url,
            "consent_data": last_consent_data,
            "callback_url": callback_url,
            "oauth_final_url": final_oauth_url,
        }

    deadline = time.monotonic() + timeout_sec
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        auth_json = _get_auth_cookie_payload(session)
        if auth_json:
            last_auth_json = auth_json
        if last_auth_json.get("workspaces"):
            break

        oauth_trace = _follow_redirect_chain(session, oauth_auth_url, timeout=15)
        final_oauth_url = str(oauth_trace.get("final_url") or final_oauth_url or "")
        callback_url = str(oauth_trace.get("callback_url") or "").strip()
        if callback_url:
            break

        candidate_url = ""
        if final_oauth_url.startswith("https://auth.openai.com/"):
            candidate_url = final_oauth_url
        elif current_consent_url:
            candidate_url = current_consent_url

        if candidate_url:
            current_consent_url = candidate_url
            try:
                consent_resp = session.get(current_consent_url, timeout=15)
                current_consent_url = str(getattr(consent_resp, "url", "") or current_consent_url)
                try:
                    last_consent_data = consent_resp.json() or {}
                except Exception:
                    last_consent_data = {}
            except Exception:
                pass

        auth_json = _get_auth_cookie_payload(session)
        if auth_json:
            last_auth_json = auth_json
        if last_auth_json.get("workspaces"):
            break

        remaining = max(0, int(deadline - time.monotonic()))
        if remaining <= 0:
            break
        print(f"[*] 登录会话稳定化轮询 #{attempt}，仍无 workspaces / callback，剩余约 {remaining}s...")
        time.sleep(min(poll_sec, remaining))

    return {
        "auth_json": last_auth_json,
        "consent_url": current_consent_url,
        "consent_data": last_consent_data,
        "callback_url": callback_url,
        "oauth_final_url": final_oauth_url,
    }


def _start_local_oauth_callback_server(host: str = "localhost", port: int = 1455) -> LocalOAuthCallbackServer:
    capture = LocalOAuthCallbackCapture()
    done_event = threading.Event()

    class _CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def do_GET(self) -> None:
            raw_path = str(self.path or "")
            callback_url = f"http://localhost:{port}{raw_path}"
            parsed = _parse_callback_url(callback_url)
            if parsed.get("code") and parsed.get("state"):
                capture.callback_url = callback_url
                done_event.set()
                body = (
                    "<html><body><h1>Codex OAuth callback captured</h1>"
                    "<p>You can close this tab and return to the terminal.</p></body></html>"
                ).encode("utf-8")
                self.send_response(200)
            else:
                capture.error = raw_path
                body = (
                    "<html><body><h1>Callback received but missing code/state</h1>"
                    "<p>Please return to the terminal to inspect logs.</p></body></html>"
                ).encode("utf-8")
                self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True

    httpd = ThreadingHTTPServer((host, port), _CallbackHandler)
    thread = threading.Thread(target=httpd.serve_forever, name="cpa-oauth-callback-server", daemon=True)
    thread.start()
    return LocalOAuthCallbackServer(
        httpd=httpd,
        thread=thread,
        capture=capture,
        event=done_event,
        host=host,
        port=port,
    )


class CustomDomainMailClient:
    """通过 IMAP 读取 QQ 邮箱中 Cloudflare 转发的验证码邮件。"""

    def __init__(
        self,
        domain: str = "",
        suffix: str = "",
        random_length: int = 6,
        imap_host: str = "imap.qq.com",
        imap_port: int = 993,
        imap_user: str = "",
        imap_pass: str = "",
        imap_folder: str = "INBOX",
    ):
        self.domain = (domain or CUSTOM_EMAIL_DOMAIN).strip().lstrip("@")
        self.suffix = (suffix or CUSTOM_EMAIL_SUFFIX).strip()
        self.random_length = random_length or CUSTOM_EMAIL_RANDOM_LENGTH
        self.imap_host = imap_host or QQ_IMAP_HOST
        self.imap_port = imap_port or QQ_IMAP_PORT
        self.imap_user = imap_user or QQ_IMAP_USER
        self.imap_pass = imap_pass or QQ_IMAP_PASS
        self.imap_folder = imap_folder or QQ_IMAP_FOLDER

        if not self.domain:
            raise ValueError("CUSTOM_EMAIL_DOMAIN 未配置")
        if not self.imap_user or not self.imap_pass:
            raise ValueError("QQ_IMAP_USER / QQ_IMAP_PASS 未配置")

    def generate_email(self) -> str:
        """生成 {随机N位字母数字}{suffix}@domain 格式邮箱"""
        chars = string.ascii_lowercase + string.digits
        rand_part = "".join(random.choices(chars, k=self.random_length))
        local = f"{rand_part}{self.suffix}" if self.suffix else rand_part
        addr = f"{local}@{self.domain}"
        suffix_label = f"{self.suffix}" if self.suffix else "(无后缀)"
        print(f"[+] 自定义邮箱: {addr}  (随机={rand_part}, 后缀={suffix_label})")
        return addr

    @staticmethod
    def _parse_internaldate(fetch_parts: Any) -> Optional[float]:
        if not fetch_parts:
            return None
        if not isinstance(fetch_parts, (list, tuple)):
            fetch_parts = [fetch_parts]

        for item in fetch_parts:
            candidates = item if isinstance(item, tuple) else (item,)
            for candidate in candidates:
                meta_text = candidate.decode("utf-8", errors="replace") if isinstance(candidate, bytes) else str(candidate)
                match = re.search(r'INTERNALDATE "([^"]+)"', meta_text)
                if not match:
                    continue
                try:
                    return datetime.strptime(match.group(1), "%d-%b-%Y %H:%M:%S %z").timestamp()
                except Exception:
                    continue
        return None

    def _fetch_folder_msg_entries(self, conn: imaplib.IMAP4_SSL, folder: str, n: int) -> List[tuple[_email_lib.message.Message, Optional[float]]]:
        """从指定文件夹取最新 n 封邮件及其接收时间"""
        entries: List[tuple[_email_lib.message.Message, Optional[float]]] = []
        try:
            status, _ = conn.select(folder, readonly=True)
            if status != "OK":
                return entries
            _, data = conn.search(None, "ALL")
            all_ids = (data[0] or b"").split()
            target_ids = list(reversed(all_ids[-n:]))  # 最新在前
            for uid in target_ids:
                _, msg_data = conn.fetch(uid, "(RFC822 INTERNALDATE)")
                received_ts = self._parse_internaldate(msg_data)
                for part in msg_data:
                    if isinstance(part, tuple):
                        msg = _email_lib.message_from_bytes(part[1])
                        entries.append((msg, received_ts))
        except Exception as e:
            err_msg = str(e)
            if "codec can't encode" not in err_msg and "EXAMINE command error" not in err_msg and "NONEXISTENT" not in err_msg:
                print(f"[custom-imap] 文件夹 {folder!r} 读取失败: {e}")
        return entries

    def _fetch_folder_msgs(self, conn: imaplib.IMAP4_SSL, folder: str, n: int) -> List[_email_lib.message.Message]:
        """从指定文件夹取最新 n 封邮件"""
        return [msg for msg, _ in self._fetch_folder_msg_entries(conn, folder, n)]

    def _fetch_latest_msg_entries(self, n: int = 30) -> List[tuple[_email_lib.message.Message, Optional[float]]]:
        """同时取 INBOX 和垃圾邮件文件夹最新 n 封（最新在前），并保留接收时间"""
        entries: List[tuple[_email_lib.message.Message, Optional[float]]] = []
        spam_folders = ["Junk", "垃圾邮件", "Spam", "SPAM", "Bulk Mail"]
        try:
            conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            conn.login(self.imap_user, self.imap_pass)
            try:
                entries.extend(self._fetch_folder_msg_entries(conn, self.imap_folder, n))
                for folder in spam_folders:
                    extra = self._fetch_folder_msg_entries(conn, folder, n)
                    if extra:
                        print(f"[custom-imap] 垃圾箱 {folder!r} 中额外找到 {len(extra)} 封邮件")
                        entries.extend(extra)
                        break
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass
        except Exception as e:
            print(f"[custom-imap] IMAP 连接/读取失败: {e}")
        return entries

    def _fetch_latest_msgs(self, n: int = 30) -> List[_email_lib.message.Message]:
        """同时取 INBOX 和垃圾邮件文件夹最新 n 封（最新在前）"""
        return [msg for msg, _ in self._fetch_latest_msg_entries(n)]

    @staticmethod
    def _get_body(msg: _email_lib.message.Message) -> str:
        """递归提取邮件纯文本正文"""
        parts = []
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct in ("text/plain", "text/html"):
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        parts.append(payload.decode(charset, errors="replace"))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                parts.append(payload.decode(charset, errors="replace"))
        return " ".join(parts)

    def _msg_targets_email(self, msg: _email_lib.message.Message, target_email: str) -> bool:
        """检查邮件是否发给 target_email（To / Delivered-To / X-Forwarded-To / Envelope-To 等头）"""
        target_lower = target_email.lower()
        check_headers = ["To", "Delivered-To", "X-Forwarded-To", "Envelope-To", "X-Original-To", "Cc", "Resent-To", "Apparently-To"]
        for h in check_headers:
            val = _decode_mime_str(msg.get(h, "")).lower()
            if target_lower in val:
                return True
        return False

    def extract_candidates_for(
        self,
        target_email: str,
        n: int = 30,
        min_received_ts: Optional[float] = None,
        time_skew_sec: float = OTP_TIME_SKEW_SEC,
    ) -> List[OtpCandidate]:
        """取最新 n 封邮件，返回发给 target_email 的验证码候选及接收时间"""
        candidates: List[OtpCandidate] = []
        entries = self._fetch_latest_msg_entries(n)
        min_allowed_ts = _min_allowed_otp_timestamp(min_received_ts, time_skew_sec=time_skew_sec)
        for msg, received_ts in entries:
            fr = _decode_mime_str(msg.get("From", ""))
            subj = _decode_mime_str(msg.get("Subject", ""))
            if not self._msg_targets_email(msg, target_email):
                continue
            received_ts = received_ts or _parse_timestamp_value(_decode_mime_str(msg.get("Date", "")))
            if min_allowed_ts is not None and (received_ts is None or received_ts < min_allowed_ts):
                continue
            body = self._get_body(msg)
            found = _extract_ranked_otp_codes(subj, body)
            if found:
                ts_label = datetime.fromtimestamp(received_ts).strftime("%Y-%m-%d %H:%M:%S") if received_ts else "unknown"
                print(f"[custom-imap] ✅ 命中: From={fr[:50]} Subject={subj[:60]} ts={ts_label} codes={found}")
            for code in found:
                candidates.append(OtpCandidate(code=code, received_ts=received_ts, source="custom-imap"))
        return _sort_otp_candidates(candidates)

    def extract_codes_for(
        self,
        target_email: str,
        n: int = 30,
        min_received_ts: Optional[float] = None,
        time_skew_sec: float = OTP_TIME_SKEW_SEC,
    ) -> List[str]:
        """取最新 n 封邮件，返回发给 target_email 的所有 6 位验证码列表"""
        return [
            item.code
            for item in self.extract_candidates_for(
                target_email,
                n=n,
                min_received_ts=min_received_ts,
                time_skew_sec=time_skew_sec,
            )
        ]

    def fetch_code(
        self,
        target_email: str,
        timeout_sec: int = 180,
        poll: float = 6.0,
        exclude_codes: Optional[List[str]] = None,
        min_received_ts: Optional[float] = None,
        time_skew_sec: float = OTP_TIME_SKEW_SEC,
    ) -> Optional[str]:
        exclude = set(exclude_codes or [])
        start = time.monotonic()
        attempt = 0
        while time.monotonic() - start < timeout_sec:
            attempt += 1
            codes = self.extract_codes_for(target_email, min_received_ts=min_received_ts, time_skew_sec=time_skew_sec)
            print(f"[otp][custom] 轮询 #{attempt}, 共匹配 {len(codes)} 个候选码, 收件目标: {target_email}")
            for code in codes:
                if code not in exclude:
                    return code
            time.sleep(poll)
        return None

    def cleanup_email(self, target_email: str) -> None:
        """从最新的 30 封邮件中找出该目标的邮件并删除，防止影响后续流程"""
        spam_folders = ["Junk", "垃圾邮件", "Spam", "SPAM", "Bulk Mail"]
        try:
            conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            conn.login(self.imap_user, self.imap_pass)
            
            def _delete_in(folder):
                try:
                    status, _ = conn.select(folder)
                    if status != "OK":
                        return
                    _, data = conn.search(None, "ALL")
                    all_ids = (data[0] or b"").split()
                    target_ids = list(reversed(all_ids[-30:]))
                    for uid in target_ids:
                        _, msg_data = conn.fetch(uid, "(RFC822)")
                        for part in msg_data:
                            if isinstance(part, tuple):
                                msg = _email_lib.message_from_bytes(part[1])
                                if self._msg_targets_email(msg, target_email):
                                    conn.store(uid, "+FLAGS", "\\Deleted")
                    conn.expunge()
                except Exception:
                    pass
            
            _delete_in(self.imap_folder)
            for f in spam_folders:
                _delete_in(f)
                
            try:
                conn.logout()
            except Exception:
                pass
            print(f"[*] 已清理 {target_email} 相关的验证码收件箱")
        except Exception as e:
            print(f"[custom-imap] 清理邮件失败: {e}")


# ========== 临时邮箱提供商：GPTMail + TempMail.lol ==========

class GPTMailClient:
    def __init__(self, proxies: Any = None):
        self.session = requests.Session(proxies=proxies, impersonate="chrome")
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://mail.chatgpt.org.uk/",
        })
        self.base_url = "https://mail.chatgpt.org.uk"

    def _init_browser_session(self):
        try:
            resp = self.session.get(self.base_url, timeout=15)
            gm_sid = self.session.cookies.get("gm_sid")
            if gm_sid:
                self.session.headers.update({"Cookie": f"gm_sid={gm_sid}"})
            token_match = re.search(r'(eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)', resp.text)
            if token_match:
                self.session.headers.update({"x-inbox-token": token_match.group(1)})
        except Exception:
            pass

    def generate_email(self) -> str:
        self._init_browser_session()
        resp = self.session.get(f"{self.base_url}/api/generate-email", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            email = data["data"]["email"]
            self.session.headers.update({"x-inbox-token": data["auth"]["token"]})
            print(f"[+] 生成邮箱: {email} (GPTMail)")
            print("[*] 自动轮询已启动（GPTMail 会话已准备）")
            return email
        raise RuntimeError(f"GPTMail 生成失败: {resp.status_code}")

    def list_emails(self, email: str) -> List[Dict[str, Any]]:
        encoded_email = urllib.parse.quote(email)
        resp = self.session.get(f"{self.base_url}/api/emails?email={encoded_email}", timeout=15)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("emails", [])
        return []


class Message:
    def __init__(self, data: dict):
        self.from_addr = data.get("from", "")
        self.subject = data.get("subject", "")
        self.body = data.get("body", "") or ""
        self.html_body = data.get("html", "") or ""


class EMail:
    def __init__(self, proxies: Any = None):
        self.s = requests.Session(proxies=proxies, impersonate="chrome")
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        r = self.s.post("https://api.tempmail.lol/v2/inbox/create", json={}, timeout=15)
        r.raise_for_status()
        data = r.json()
        self.address = data["address"]
        self.token = data["token"]
        print(f"[+] 生成邮箱: {self.address} (TempMail.lol)")
        print("[*] 自动轮询已启动（token 已保存）")

    def _get_messages(self) -> List[Dict[str, Any]]:
        r = self.s.get(f"https://api.tempmail.lol/v2/inbox?token={self.token}", timeout=15)
        r.raise_for_status()
        return r.json().get("emails", [])


def get_email_and_code_fetcher(proxies: Any = None, provider: str = "auto"):
    provider = (provider or "auto").strip().lower()
    if provider not in {"auto", "gptmail", "tempmail", "custom"}:
        raise ValueError(f"不支持的邮箱提供商: {provider}")

    def _build_custom_bundle():
        client = CustomDomainMailClient()
        email = client.generate_email()
        print("[*] 自定义域名邮箱已就绪，将轮询 QQ IMAP 接收验证码")

        def _extract_all_codes() -> List[str]:
            try:
                return client.extract_codes_for(email)
            except Exception:
                return []

        def _extract_code_candidates(
            min_received_ts: Optional[float] = None,
            time_skew_sec: float = OTP_TIME_SKEW_SEC,
        ) -> List[OtpCandidate]:
            try:
                return client.extract_candidates_for(email, min_received_ts=min_received_ts, time_skew_sec=time_skew_sec)
            except Exception:
                return []

        def fetch_code(
            timeout_sec: int = 180,
            poll: float = 6.0,
            exclude_codes: Optional[List[str]] = None,
            min_received_ts: Optional[float] = None,
            time_skew_sec: float = OTP_TIME_SKEW_SEC,
        ) -> str | None:
            return client.fetch_code(
                email,
                timeout_sec=timeout_sec,
                poll=poll,
                exclude_codes=exclude_codes,
                min_received_ts=min_received_ts,
                time_skew_sec=time_skew_sec,
            )

        return email, _gen_password(), fetch_code, _extract_all_codes, _extract_code_candidates, "custom", client

    def _build_tempmail_bundle():
        inbox = EMail(proxies)
        email = inbox.address

        def _extract_all_codes() -> List[str]:
            results: List[str] = []
            try:
                msgs = inbox._get_messages()
                for msg_data in msgs:
                    msg = Message(msg_data)
                    body = msg.body or msg.html_body or ""
                    results.extend(_extract_ranked_otp_codes(msg.subject, body))
            except Exception:
                pass
            return _sort_otp_candidates(results)

        def _extract_code_candidates(
            min_received_ts: Optional[float] = None,
            time_skew_sec: float = OTP_TIME_SKEW_SEC,
        ) -> List[OtpCandidate]:
            results: List[OtpCandidate] = []
            try:
                msgs = inbox._get_messages()
                min_allowed_ts = _min_allowed_otp_timestamp(min_received_ts, time_skew_sec=time_skew_sec)
                for msg_data in msgs:
                    received_ts = _parse_provider_message_timestamp(msg_data)
                    if min_allowed_ts is not None and (received_ts is None or received_ts < min_allowed_ts):
                        continue
                    msg = Message(msg_data)
                    body = msg.body or msg.html_body or ""
                    for code in _extract_ranked_otp_codes(msg.subject, body):
                        results.append(OtpCandidate(code=code, received_ts=received_ts, source="tempmail"))
            except Exception:
                pass
            return _sort_otp_candidates(results)

        def fetch_code(
            timeout_sec: int = 180,
            poll: float = 6.0,
            exclude_codes: Optional[List[str]] = None,
            min_received_ts: Optional[float] = None,
            time_skew_sec: float = OTP_TIME_SKEW_SEC,
        ) -> str | None:
            exclude = set(exclude_codes or [])
            start = time.monotonic()
            attempt = 0
            while time.monotonic() - start < timeout_sec:
                attempt += 1
                try:
                    candidates = _extract_code_candidates(min_received_ts=min_received_ts, time_skew_sec=time_skew_sec)
                    print(f"[otp][tempmail] 轮询 #{attempt}, 共匹配 {len(candidates)} 个候选码, 目标: {email}")
                    for candidate in candidates:
                        if candidate.code not in exclude:
                            return candidate.code
                except Exception:
                    pass
                time.sleep(poll)
            return None

        return email, _gen_password(), fetch_code, _extract_all_codes, _extract_code_candidates, "tempmail", inbox

    def _build_gptmail_bundle():
        client = GPTMailClient(proxies)
        email = client.generate_email()

        def _extract_all_codes() -> List[str]:
            results: List[str] = []
            try:
                summaries = client.list_emails(email)
                for s in summaries:
                    subject = str(s.get("subject", "") or "")
                    body = " ".join([
                        str(s.get("text", "") or ""),
                        str(s.get("body", "") or ""),
                        str(s.get("html", "") or ""),
                        json.dumps(s, ensure_ascii=False),
                    ])
                    results.extend(_extract_ranked_otp_codes(subject, body))
            except Exception:
                pass
            return results

        def _extract_code_candidates(
            min_received_ts: Optional[float] = None,
            time_skew_sec: float = OTP_TIME_SKEW_SEC,
        ) -> List[OtpCandidate]:
            results: List[OtpCandidate] = []
            try:
                summaries = client.list_emails(email)
                min_allowed_ts = _min_allowed_otp_timestamp(min_received_ts, time_skew_sec=time_skew_sec)
                for s in summaries:
                    received_ts = _parse_provider_message_timestamp(s)
                    if min_allowed_ts is not None and (received_ts is None or received_ts < min_allowed_ts):
                        continue
                    subject = str(s.get("subject", "") or "")
                    body = " ".join([
                        str(s.get("text", "") or ""),
                        str(s.get("body", "") or ""),
                        str(s.get("html", "") or ""),
                        json.dumps(s, ensure_ascii=False),
                    ])
                    for code in _extract_ranked_otp_codes(subject, body):
                        results.append(OtpCandidate(code=code, received_ts=received_ts, source="gptmail"))
            except Exception:
                pass
            return results

        def fetch_code(
            timeout_sec: int = 180,
            poll: float = 6.0,
            exclude_codes: Optional[List[str]] = None,
            min_received_ts: Optional[float] = None,
            time_skew_sec: float = OTP_TIME_SKEW_SEC,
        ) -> str | None:
            exclude = set(exclude_codes or [])
            start = time.monotonic()
            attempt = 0
            while time.monotonic() - start < timeout_sec:
                attempt += 1
                try:
                    candidates = _extract_code_candidates(min_received_ts=min_received_ts, time_skew_sec=time_skew_sec)
                    print(f"[otp][gptmail] 轮询 #{attempt}, 共匹配 {len(candidates)} 个候选码, 目标: {email}")
                    for candidate in candidates:
                        if candidate.code not in exclude:
                            return candidate.code
                except Exception:
                    pass
                time.sleep(poll)
            return None

        return email, _gen_password(), fetch_code, _extract_all_codes, _extract_code_candidates, "gptmail", client

    if provider == "custom":
        return _build_custom_bundle()
    if provider == "tempmail":
        return _build_tempmail_bundle()
    if provider == "gptmail":
        return _build_gptmail_bundle()

    # auto 模式：有 CUSTOM_EMAIL_DOMAIN 配置则优先走自定义域名
    if CUSTOM_EMAIL_DOMAIN and QQ_IMAP_USER and QQ_IMAP_PASS:
        try:
            return _build_custom_bundle()
        except Exception as e:
            print(f"[邮箱] 自定义域名邮箱初始化失败，回退公共服务: {e}")

    try:
        return _build_tempmail_bundle()
    except Exception as e:
        print(f"[邮箱] TempMail.lol 初始化失败，回退 GPTMail: {e}")
        return _build_gptmail_bundle()

# ========== OAuth 核心逻辑 (对齐原版的完美重定向流) ==========

def _gen_password() -> str:
    alphabet = string.ascii_letters + string.digits
    special = "!@#$%^&*.-"
    base = [random.choice(string.ascii_lowercase), random.choice(string.ascii_uppercase),
            random.choice(string.digits), random.choice(special)]
    base += [random.choice(alphabet + special) for _ in range(12)]
    random.shuffle(base)
    return "".join(base)

COMMON_FIRST_NAMES = [
    "Adrian", "Aiden", "Alexander", "Andrew", "Anthony", "Ariana", "Ava", "Benjamin",
    "Caleb", "Carter", "Charlotte", "Chloe", "Daniel", "David", "Dylan", "Eleanor",
    "Elena", "Elijah", "Elizabeth", "Ella", "Emily", "Emma", "Ethan", "Evelyn",
    "Gabriel", "Grace", "Hannah", "Henry", "Isabella", "Jack", "Jackson", "Jacob",
    "James", "Julian", "Layla", "Leah", "Liam", "Lillian", "Logan", "Lucas",
    "Madison", "Mason", "Mia", "Michael", "Nathan", "Noah", "Nora", "Olivia",
    "Owen", "Samuel", "Scarlett", "Sophia", "Sophie", "Victoria", "William", "Zoe",
]

COMMON_LAST_NAMES = [
    "Anderson", "Baker", "Bennett", "Brooks", "Brown", "Campbell", "Carter", "Clark",
    "Collins", "Cooper", "Davis", "Edwards", "Evans", "Foster", "Garcia", "Gonzalez",
    "Gray", "Green", "Hall", "Harris", "Hayes", "Hill", "Howard", "Hughes",
    "Jackson", "Johnson", "Kelly", "King", "Lee", "Lewis", "Long", "Martinez",
    "Miller", "Mitchell", "Moore", "Morgan", "Murphy", "Nelson", "Parker", "Perry",
    "Peterson", "Phillips", "Price", "Reed", "Richardson", "Rivera", "Roberts", "Robinson",
    "Ross", "Sanchez", "Scott", "Smith", "Stewart", "Taylor", "Thomas", "Turner",
    "Walker", "Ward", "Watson", "White", "Williams", "Wilson", "Wood", "Wright",
]


def _random_name() -> str:
    first_name = random.choice(COMMON_FIRST_NAMES)
    last_name = random.choice(COMMON_LAST_NAMES)
    return f"{first_name} {last_name}"

def _random_birthdate() -> str:
    start = datetime(1975, 1, 1); end = datetime(1999, 12, 31)
    d = start + timedelta(days=random.randrange((end - start).days + 1))
    return d.strftime('%Y-%m-%d')

def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

def _sha256_b64url_no_pad(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())

def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)

def _parse_callback_url(callback_url: str) -> Dict[str, Any]:
    candidate = (callback_url or "").strip()
    if not candidate:
        return {"code": "", "state": "", "error": "", "error_description": ""}
    if "://" not in candidate:
        if candidate.startswith("?"):
            candidate = f"http://localhost{candidate}"
        elif any(ch in candidate for ch in "/?#") or ":" in candidate:
            candidate = f"http://{candidate}"
        elif "=" in candidate:
            candidate = f"http://localhost/?{candidate}"
    parsed = urllib.parse.urlparse(candidate)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)
    for key, values in fragment.items():
        if key not in query or not query[key] or not (query[key][0] or "").strip():
            query[key] = values
    def get1(k: str) -> str:
        return (query.get(k, [""])[0] or "").strip()
    code = get1("code")
    state = get1("state")
    error = get1("error")
    error_description = get1("error_description")
    if code and not state and "#" in code:
        code, state = code.split("#", 1)
    if not error and error_description:
        error, error_description = error_description, ""
    return {"code": code, "state": state, "error": error, "error_description": error_description}

def _decode_jwt_segment(seg: str) -> Dict[str, Any]:
    try:
        pad = "=" * ((4 - (len(seg) % 4)) % 4)
        return json.loads(base64.urlsafe_b64decode((seg + pad).encode("ascii")).decode("utf-8"))
    except Exception:
        return {}

def _jwt_claims_no_verify(token: str) -> Dict[str, Any]:
    if not token or token.count(".") < 2:
        return {}
    return _decode_jwt_segment(token.split(".")[1])

def _post_form(url: str, data: Dict[str, str], timeout: int = 30) -> Dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.status != 200:
                raise RuntimeError(f"Token 交换失败: {resp.status}: {raw.decode('utf-8', 'replace')}")
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise RuntimeError(f"Token 交换失败: {exc.code}: {raw.decode('utf-8', 'replace')}") from exc



def _parse_int_csv(raw: str, default: List[int] | None = None) -> List[int]:
    values = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if part and part.lstrip("-").isdigit():
            values.append(int(part))
    return values or list(default or [])


def _resolve_sub2api_settings(args=None) -> Dict[str, Any]:
    base_url = str((getattr(args, "sub2api_base_url", None) if args else None) or SUB2API_BASE_URL or "").strip().rstrip("/")
    admin_api_key = str((getattr(args, "sub2api_admin_api_key", None) if args else None) or SUB2API_ADMIN_API_KEY or "").strip()
    bearer = str((getattr(args, "sub2api_bearer", None) if args else None) or SUB2API_BEARER or "").strip()
    email = str((getattr(args, "sub2api_email", None) if args else None) or SUB2API_EMAIL or "").strip()
    password = str((getattr(args, "sub2api_password", None) if args else None) or SUB2API_PASSWORD or "").strip()
    group_ids_raw = (getattr(args, "sub2api_group_ids", None) if args else None) or os.getenv("SUB2API_GROUP_IDS") or "2"
    auto_upload = bool(getattr(args, "sub2api_upload", False)) or _as_bool(os.getenv("AUTO_UPLOAD_SUB2API"))
    return {
        "base_url": base_url,
        "admin_api_key": admin_api_key,
        "bearer": bearer,
        "email": email,
        "password": password,
        "group_ids": _parse_int_csv(group_ids_raw, [2]),
        "auto_upload": auto_upload,
    }


def _decode_jwt_payload(token: str) -> Dict[str, Any]:
    return _jwt_claims_no_verify(token)

def _build_sentinel_payload(session, did: str, flow: str) -> str:
    req_body = json.dumps({"p": "", "id": did, "flow": flow})
    resp = session.post(
        "https://sentinel.openai.com/backend-api/sentinel/req",
        headers={
            "origin": "https://sentinel.openai.com",
            "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
            "content-type": "text/plain;charset=UTF-8",
        },
        data=req_body,
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Sentinel 验证失败: {resp.status_code}: {resp.text[:200]}")
    token = (resp.json() or {}).get("token", "")
    return json.dumps({"p": "", "t": "", "c": token, "id": did, "flow": flow})

@dataclass(frozen=True)
class OAuthStart:
    auth_url: str; state: str; code_verifier: str; redirect_uri: str

def generate_oauth_url(redirect_uri: str = DEFAULT_REDIRECT_URI) -> OAuthStart:
    state = secrets.token_urlsafe(16)
    verifier = _pkce_verifier()
    challenge = _sha256_b64url_no_pad(verifier)

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": DEFAULT_SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    return OAuthStart(f"{AUTH_URL}?{urllib.parse.urlencode(params)}", state, verifier, redirect_uri)

def fetch_sentinel_token(flow: str, did: str, proxies: Any = None) -> Optional[str]:
    try:
        session = requests.Session(proxies=proxies, impersonate="chrome")
        payload = _build_sentinel_payload(session, did, flow)
        return (json.loads(payload) or {}).get("c")
    except Exception:
        return None

def submit_callback_url(callback_url: str, expected_state: str, code_verifier: str, redirect_uri: str, session=None) -> str:
    cb = _parse_callback_url(callback_url)
    if cb.get("error"):
        raise RuntimeError(f"OAuth 错误: {cb['error']}: {cb.get('error_description', '')}".strip())
    if not cb.get("code"):
        raise ValueError("Callback URL 缺少 ?code=")
    if not cb.get("state"):
        raise ValueError("Callback URL 缺少 ?state=")
    if cb.get("state") != expected_state:
        raise ValueError("State 校验不匹配")
    token_data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": cb["code"],
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    if session is not None:
        resp = session.post(
            TOKEN_URL,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Token 交换失败: {resp.status_code}: {resp.text[:200]}")
        token_resp = resp.json()
    else:
        token_resp = _post_form(TOKEN_URL, token_data)

    return _token_json_from_oauth_response(token_resp)


def _token_json_from_oauth_response(token_resp: Dict[str, Any]) -> str:
    access_token = str(token_resp.get("access_token") or "").strip()
    refresh_token = str(token_resp.get("refresh_token") or "").strip()
    id_token = str(token_resp.get("id_token") or "").strip()
    expires_in = _to_int(token_resp.get("expires_in"))
    claims = _jwt_claims_no_verify(id_token)
    auth_claims = claims.get("https://api.openai.com/auth") or {}

    now = int(time.time())
    config = {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": str(auth_claims.get("chatgpt_account_id") or "").strip(),
        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "email": str(claims.get("email") or "").strip(),
        "type": "codex",
        "expired": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0))),
    }
    return json.dumps(config, ensure_ascii=False, indent=2)


def perform_codex_oauth_login_http(
    email: str,
    password: str,
    extract_code_candidates,
    email_client,
    proxies: Any = None,
    user_agent: str = UA,
    email_timeout: int = 900,
) -> Optional[str]:
    print("[OAuth] 开始执行 Codex OAuth 纯协议流程...")
    session = requests.Session(proxies=proxies, impersonate="chrome")
    oauth = generate_oauth_url()
    authorize_params = {
        key: values[0]
        for key, values in urllib.parse.parse_qs(urllib.parse.urlparse(oauth.auth_url).query, keep_blank_values=True).items()
        if values
    }

    def _bootstrap_oauth_session() -> tuple[bool, str]:
        print("[OAuth] 1/7 GET /oauth/authorize")
        try:
            resp = session.get(
                oauth.auth_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": "https://chatgpt.com/",
                    "Upgrade-Insecure-Requests": "1",
                    "User-Agent": user_agent,
                },
                allow_redirects=True,
                timeout=30,
            )
        except Exception as e:
            print(f"[OAuth] /oauth/authorize 异常: {e}")
            return False, ""

        final_url = str(getattr(resp, "url", "") or "")
        redirects = len(getattr(resp, "history", []) or [])
        print(f"[OAuth] /oauth/authorize -> {resp.status_code}, final={(final_url or '-')[:140]}, redirects={redirects}")

        has_login = _has_cookie(session, "login_session")
        print(f"[OAuth] login_session: {'已获取' if has_login else '未获取'}")
        if has_login:
            return True, final_url

        print("[OAuth] 未拿到 login_session，尝试访问 oauth2 auth 入口")
        try:
            resp2 = session.get(
                "https://auth.openai.com/api/oauth/oauth2/auth",
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": oauth.auth_url,
                    "Upgrade-Insecure-Requests": "1",
                    "User-Agent": user_agent,
                },
                params=authorize_params,
                allow_redirects=True,
                timeout=30,
            )
            final_url = str(getattr(resp2, "url", "") or final_url)
            redirects2 = len(getattr(resp2, "history", []) or [])
            print(f"[OAuth] /api/oauth/oauth2/auth -> {resp2.status_code}, final={(final_url or '-')[:140]}, redirects={redirects2}")
        except Exception as e:
            print(f"[OAuth] /api/oauth/oauth2/auth 异常: {e}")

        has_login = _has_cookie(session, "login_session")
        print(f"[OAuth] login_session(重试): {'已获取' if has_login else '未获取'}")
        return has_login, final_url

    has_login_session, authorize_final_url = _bootstrap_oauth_session()
    if not authorize_final_url:
        return None

    did = session.cookies.get("oai-did")
    if not did:
        print("[OAuth] 未获取到 oai-did")
        return None
    session.cookies.set("oai-did", did, domain=".auth.openai.com")
    session.cookies.set("oai-did", did, domain="auth.openai.com")

    continue_referer = authorize_final_url if authorize_final_url.startswith("https://auth.openai.com") else "https://auth.openai.com/log-in"

    def _post_authorize_continue(referer_url: str):
        headers = _oauth_json_headers(referer_url, did, user_agent=user_agent)
        headers["openai-sentinel-token"] = _build_sentinel_payload(session, did, "authorize_continue")
        return session.post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            json={"username": {"kind": "email", "value": email}},
            headers=headers,
            timeout=30,
            allow_redirects=False,
        )

    print("[OAuth] 2/7 POST /api/accounts/authorize/continue")
    try:
        resp_continue = _post_authorize_continue(continue_referer)
    except Exception as e:
        print(f"[OAuth] authorize/continue 异常: {e}")
        return None
    print(f"[OAuth] /authorize/continue -> {resp_continue.status_code}")

    if resp_continue.status_code == 400 and "invalid_auth_step" in (resp_continue.text or ""):
        print("[OAuth] invalid_auth_step，重新 bootstrap 后重试一次")
        has_login_session, authorize_final_url = _bootstrap_oauth_session()
        if not authorize_final_url:
            return None
        continue_referer = authorize_final_url if authorize_final_url.startswith("https://auth.openai.com") else "https://auth.openai.com/log-in"
        try:
            resp_continue = _post_authorize_continue(continue_referer)
        except Exception as e:
            print(f"[OAuth] authorize/continue(重试) 异常: {e}")
            return None
        print(f"[OAuth] /authorize/continue(重试) -> {resp_continue.status_code}")

    if resp_continue.status_code != 200:
        print(f"[OAuth] 邮箱提交失败: {resp_continue.text[:180]}")
        return None

    try:
        continue_data = resp_continue.json() or {}
    except Exception:
        print("[OAuth] authorize/continue 响应解析失败")
        return None

    continue_url = str(continue_data.get("continue_url") or "").strip()
    page_type = str((continue_data.get("page") or {}).get("type") or "")
    print(f"[OAuth] continue page={page_type or '-'} next={(continue_url or '-')[:140]}")

    print("[OAuth] 3/7 POST /api/accounts/password/verify")
    headers_verify = _oauth_json_headers("https://auth.openai.com/log-in/password", did, user_agent=user_agent)
    try:
        headers_verify["openai-sentinel-token"] = _build_sentinel_payload(session, did, "password_verify")
    except Exception as e:
        print(f"[OAuth] password_verify 的 sentinel token 获取失败: {e}")
        return None

    try:
        resp_verify = session.post(
            "https://auth.openai.com/api/accounts/password/verify",
            json={"password": password},
            headers=headers_verify,
            timeout=30,
            allow_redirects=False,
        )
    except Exception as e:
        print(f"[OAuth] password/verify 异常: {e}")
        return None

    print(f"[OAuth] /password/verify -> {resp_verify.status_code}")
    if resp_verify.status_code != 200:
        print(f"[OAuth] 密码校验失败: {resp_verify.text[:180]}")
        return None

    try:
        verify_data = resp_verify.json() or {}
    except Exception:
        print("[OAuth] password/verify 响应解析失败")
        return None

    continue_url = str(verify_data.get("continue_url") or continue_url or "").strip()
    page_type = str((verify_data.get("page") or {}).get("type") or page_type or "")
    print(f"[OAuth] verify page={page_type or '-'} next={(continue_url or '-')[:140]}")

    need_oauth_otp = (
        page_type == "email_otp_verification"
        or "email-verification" in continue_url
        or "email-otp" in continue_url
    )

    if need_oauth_otp:
        print("[OAuth] 4/7 检测到邮箱 OTP 验证")
        otp_success = False
        tried_codes: set[str] = set()
        otp_deadline = time.time() + max(120, email_timeout)
        otp_headers = _oauth_json_headers("https://auth.openai.com/email-verification", did, user_agent=user_agent)

        while time.time() < otp_deadline and not otp_success:
            # 登录阶段参考原始实现：不做严格时间窗截断。
            # 对当前流程来说邮箱地址是本轮新注册生成的，放宽筛选更稳，避免把真正的新 OTP 误滤掉。
            candidate_items = _sort_otp_candidates(extract_code_candidates())
            fresh_candidates = [item for item in candidate_items if item.code not in tried_codes]
            if not fresh_candidates:
                waited = int(max(0, (max(120, email_timeout)) - (otp_deadline - time.time())))
                print(f"[OAuth] OTP 等待中... ({waited}s/{max(120, email_timeout)}s)")
                time.sleep(2)
                continue

            order_text = ", ".join(
                f"{item.code}@{datetime.fromtimestamp(item.received_ts).strftime('%H:%M:%S') if item.received_ts else 'unknown'}"
                for item in fresh_candidates[:5]
            )
            print(f"[OAuth] OTP 候选顺序(新->旧): {order_text}")

            for candidate in fresh_candidates:
                tried_codes.add(candidate.code)
                print(f"[OAuth] 尝试 OTP: {candidate.code}")
                try:
                    resp_otp = session.post(
                        "https://auth.openai.com/api/accounts/email-otp/validate",
                        json={"code": candidate.code},
                        headers=otp_headers,
                        timeout=30,
                        allow_redirects=False,
                    )
                except Exception as e:
                    print(f"[OAuth] email-otp/validate 异常: {e}")
                    continue

                print(f"[OAuth] /email-otp/validate -> {resp_otp.status_code}")
                if resp_otp.status_code != 200:
                    print(f"[OAuth] OTP 无效，继续尝试下一条: {resp_otp.text[:160]}")
                    continue

                try:
                    otp_data = resp_otp.json() or {}
                except Exception:
                    print("[OAuth] email-otp/validate 响应解析失败")
                    continue

                continue_url = str(otp_data.get("continue_url") or continue_url or "").strip()
                page_type = str((otp_data.get("page") or {}).get("type") or page_type or "")
                print(f"[OAuth] OTP 验证通过 page={page_type or '-'} next={(continue_url or '-')[:140]}")
                otp_success = True
                if hasattr(email_client, "cleanup_email"):
                    email_client.cleanup_email(email)
                break

            if not otp_success:
                time.sleep(2)

        if not otp_success:
            print(f"[OAuth] OAuth 阶段 OTP 验证失败，已尝试 {len(tried_codes)} 个验证码")
            return None

    code = ""
    consent_url = continue_url
    if consent_url.startswith("/"):
        consent_url = f"https://auth.openai.com{consent_url}"
    if not consent_url and "consent" in page_type:
        consent_url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"

    if consent_url:
        code = _extract_code_from_url(consent_url)

    if not code and consent_url:
        print("[OAuth] 5/7 跟随 continue_url 提取 code")
        code, _ = _oauth_follow_for_code(
            session,
            consent_url,
            referer="https://auth.openai.com/log-in/password",
            user_agent=user_agent,
        )
        code = code or ""

    consent_hint = (
        ("consent" in consent_url)
        or ("sign-in-with-chatgpt" in consent_url)
        or ("workspace" in consent_url)
        or ("organization" in consent_url)
        or ("consent" in page_type)
        or ("organization" in page_type)
    )

    if not code and consent_hint:
        if not consent_url:
            consent_url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
        print("[OAuth] 6/7 执行 workspace/org 选择")
        code = _oauth_submit_workspace_and_org(session, consent_url, did, user_agent=user_agent) or ""

    if not code:
        fallback_consent = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
        print("[OAuth] 6/7 回退 consent 路径重试")
        code = _oauth_submit_workspace_and_org(session, fallback_consent, did, user_agent=user_agent) or ""
        if not code:
            code, _ = _oauth_follow_for_code(
                session,
                fallback_consent,
                referer="https://auth.openai.com/log-in/password",
                user_agent=user_agent,
            )
            code = code or ""

    if not code:
        print("[OAuth] 未获取到 authorization code")
        return None

    print("[OAuth] 7/7 POST /oauth/token")
    token_resp = session.post(
        "https://auth.openai.com/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": user_agent},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": oauth.redirect_uri,
            "client_id": CLIENT_ID,
            "code_verifier": oauth.code_verifier,
        },
        timeout=60,
    )
    print(f"[OAuth] /oauth/token -> {token_resp.status_code}")
    if token_resp.status_code != 200:
        print(f"[OAuth] token 交换失败: {token_resp.status_code} {token_resp.text[:200]}")
        return None

    try:
        token_data = token_resp.json() or {}
    except Exception:
        print("[OAuth] token 响应解析失败")
        return None

    if not token_data.get("access_token"):
        print("[OAuth] token 响应缺少 access_token")
        return None

    return _token_json_from_oauth_response(token_data)


def _sub2api_login(settings: Dict[str, Any]) -> str:
    """登录 Sub2API 管理后台，返回 Bearer token。"""
    base_url = str(settings.get("base_url") or "").rstrip("/")
    email = str(settings.get("email") or "").strip()
    password = str(settings.get("password") or "").strip()
    if not base_url or not email or not password:
        return ""

    url = f"{base_url}/api/v1/auth/login"
    try:
        resp = requests.post(
            url,
            json={"email": email, "password": password},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=15,
        )
        data = resp.json() if hasattr(resp, "json") else {}
        token = (
            data.get("token")
            or data.get("access_token")
            or (data.get("data") or {}).get("token")
            or (data.get("data") or {}).get("access_token")
            or ""
        )
        return str(token).strip()
    except Exception as e:
        print(f"[Sub2Api] 登录失败: {e}")
        return ""


def _build_sub2api_account_payload(email: str, tokens: dict, group_ids: List[int]) -> dict:
    access_token = str(tokens.get("access_token") or "").strip()
    refresh_token = str(tokens.get("refresh_token") or "").strip()
    id_token = str(tokens.get("id_token") or "").strip()

    at_payload = _decode_jwt_payload(access_token) if access_token else {}
    at_auth = at_payload.get("https://api.openai.com/auth") or {}
    chatgpt_account_id = str(at_auth.get("chatgpt_account_id") or tokens.get("account_id") or "").strip()
    chatgpt_user_id = str(at_auth.get("chatgpt_user_id") or "").strip()
    exp_timestamp = at_payload.get("exp", 0)
    expires_at = exp_timestamp if isinstance(exp_timestamp, int) and exp_timestamp > 0 else int(time.time()) + 863999

    it_payload = _decode_jwt_payload(id_token) if id_token else {}
    it_auth = it_payload.get("https://api.openai.com/auth") or {}
    organization_id = str(it_auth.get("organization_id") or "").strip()
    if not organization_id:
        orgs = it_auth.get("organizations") or []
        if orgs:
            organization_id = str((orgs[0] or {}).get("id") or "").strip()

    return {
        "name": email,
        "notes": "",
        "platform": "openai",
        "type": "oauth",
        "credentials": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": 863999,
            "expires_at": expires_at,
            "chatgpt_account_id": chatgpt_account_id,
            "chatgpt_user_id": chatgpt_user_id,
            "organization_id": organization_id,
            "client_id": CLIENT_ID,
            "id_token": id_token,
        },
        "extra": {"email": email},
        "group_ids": group_ids,
        "concurrency": 10,
        "priority": 1,
        "auto_pause_on_expired": True,
    }


def _sub2api_auth_headers(settings: Dict[str, Any]) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{settings['base_url']}/admin/accounts",
    }
    admin_api_key = str(settings.get("admin_api_key") or "").strip()
    bearer = str(settings.get("bearer") or "").strip()
    if admin_api_key:
        headers["x-api-key"] = admin_api_key
    elif bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return headers


def _push_account_to_sub2api(email: str, tokens: dict, settings: Dict[str, Any]) -> bool:
    """上传 OAuth 账号到 Sub2API，优先使用 x-api-key。"""
    base_url = str(settings.get("base_url") or "").rstrip("/")
    if not base_url or not tokens.get("refresh_token"):
        return False

    url = f"{base_url}/api/v1/admin/accounts"
    payload = _build_sub2api_account_payload(email, tokens, settings.get("group_ids") or [2])

    def _do_request() -> tuple[int, str]:
        try:
            resp = requests.post(
                url,
                json=payload,
                headers=_sub2api_auth_headers(settings),
                timeout=20,
            )
            return resp.status_code, resp.text
        except Exception as e:
            return 0, str(e)

    status, body = _do_request()

    # 使用 x-api-key 时不需要登录刷新；仅 bearer 模式下 401 再尝试登录一次
    if status == 401 and not settings.get("admin_api_key") and settings.get("email") and settings.get("password"):
        new_token = _sub2api_login(settings)
        if new_token:
            settings["bearer"] = new_token
            status, body = _do_request()

    ok = status in (200, 201)
    if ok:
        print(f"[Sub2Api] 上传成功 (HTTP {status})")
    else:
        print(f"[Sub2Api] 上传失败 (HTTP {status}): {str(body)[:500]}")
    return ok


# ========== 轻量版 CPA 维护实现（内嵌，不依赖项目包） ==========
DEFAULT_MGMT_UA = "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"

def _mgmt_headers(token: str) -> dict:
    clean = str(token or "").strip()
    if clean and not clean.lower().startswith("bearer "):
        clean = f"Bearer {clean}"
    return {"Authorization": clean, "Accept": "application/json"}


def _join_mgmt_url(base_url: str, path: str) -> str:
    base = (base_url or "").rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    if base.endswith("/v0"):
        return f"{base}{suffix}"
    return f"{base}/v0{suffix}"


def _safe_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        return {}


def _extract_account_id(item: dict):
    for key in ("chatgpt_account_id", "chatgptAccountId", "account_id", "accountId"):
        val = item.get(key)
        if val:
            return str(val)
    return None


def _get_item_type(item: dict) -> str:
    return str(item.get("type") or item.get("typo") or "")


def _http_proxies(proxy: str = "") -> Optional[Dict[str, str]]:
    clean = str(proxy or "").strip()
    if not clean:
        return None
    return {"http": clean, "https": clean}


class MiniPoolMaintainer:
    def __init__(self, base_url: str, token: str, target_type: str = "codex", used_percent_threshold: int = 95, user_agent: str = DEFAULT_MGMT_UA):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.target_type = target_type
        self.used_percent_threshold = used_percent_threshold
        self.user_agent = user_agent

    def upload_token(self, filename: str, token_data: dict, proxy: str = "") -> bool:
        if not self.base_url or not self.token:
            return False
        content = json.dumps(token_data, ensure_ascii=False).encode("utf-8")
        files = {"file": (filename, content, "application/json")}
        headers = {"Authorization": f"Bearer {self.token}"}
        proxies = _http_proxies(proxy)
        for attempt in range(3):
            try:
                resp = py_requests.post(_join_mgmt_url(self.base_url, "/management/auth-files"), files=files, headers=headers, timeout=30, verify=False, proxies=proxies)
                if resp.status_code in (200, 201, 204):
                    return True
            except Exception:
                pass
            if attempt < 2:
                time.sleep(2 ** attempt)
        return False

    def fetch_auth_files(self, timeout: int = 15):
        resp = py_requests.get(_join_mgmt_url(self.base_url, "/management/auth-files"), headers=_mgmt_headers(self.token), timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return (data.get("files") if isinstance(data, dict) else []) or []

    def request_codex_oauth_url(self, timeout: int = 30, proxy: str = "", is_webui: bool = True) -> dict:
        headers = {**_mgmt_headers(self.token), "User-Agent": self.user_agent}
        params = {"is_webui": "true"} if is_webui else None
        resp = py_requests.get(
            _join_mgmt_url(self.base_url, "/management/codex-auth-url"),
            headers=headers,
            params=params,
            timeout=timeout,
            proxies=_http_proxies(proxy),
        )
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    def get_oauth_status(self, state: str, timeout: int = 15, proxy: str = "") -> dict:
        headers = {**_mgmt_headers(self.token), "User-Agent": self.user_agent}
        resp = py_requests.get(
            _join_mgmt_url(self.base_url, "/management/get-auth-status"),
            headers=headers,
            params={"state": state},
            timeout=timeout,
            proxies=_http_proxies(proxy),
        )
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    def submit_oauth_callback(self, provider: str, redirect_url: str, timeout: int = 30, proxy: str = "") -> dict:
        headers = {**_mgmt_headers(self.token), "User-Agent": self.user_agent, "Content-Type": "application/json"}
        resp = py_requests.post(
            _join_mgmt_url(self.base_url, "/management/oauth-callback"),
            headers=headers,
            json={"provider": provider, "redirect_url": redirect_url},
            timeout=timeout,
            proxies=_http_proxies(proxy),
        )
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    async def probe_and_clean_async(self, workers: int = 20, timeout: int = 10, retries: int = 1):
        if aiohttp is None:
            raise RuntimeError(
                "当前运行环境缺少 aiohttp。"
                "如果你是用 uv run 启动，请先在项目目录执行: uv sync --extra cpa"
            )
        files = self.fetch_auth_files(timeout)
        candidates = [f for f in files if _get_item_type(f).lower() == self.target_type.lower()]
        if not candidates:
            return {"total": len(files), "candidates": 0, "invalid_count": 0, "deleted_ok": 0, "deleted_fail": 0}

        semaphore = asyncio.Semaphore(max(1, workers))
        connector = aiohttp.TCPConnector(limit=max(1, workers))
        client_timeout = aiohttp.ClientTimeout(total=max(1, timeout))

        async def probe_one(session, item):
            auth_index = item.get("auth_index")
            name = item.get("name") or item.get("id")
            res = {"name": name, "auth_index": auth_index, "invalid_401": False, "invalid_used_percent": False, "used_percent": None}
            if not auth_index:
                res["invalid_401"] = False
                return res
            account_id = _extract_account_id(item)
            header = {"Authorization": "Bearer $TOKEN$", "Content-Type": "application/json", "User-Agent": self.user_agent}
            if account_id:
                header["Chatgpt-Account-Id"] = account_id
            payload = {"authIndex": auth_index, "method": "GET", "url": "https://chatgpt.com/backend-api/wham/usage", "header": header}
            for attempt in range(retries + 1):
                try:
                    async with semaphore:
                        async with session.post(_join_mgmt_url(self.base_url, "/management/api-call"), headers={**_mgmt_headers(self.token), "Content-Type": "application/json"}, json=payload, timeout=timeout) as resp:
                            text = await resp.text()
                            if resp.status >= 400:
                                raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")
                            data = _safe_json(text)
                            sc = data.get("status_code")
                            res["invalid_401"] = sc == 401
                            if sc == 200:
                                body = _safe_json(data.get("body", ""))
                                used_pct = (body.get("rate_limit", {}).get("primary_window", {}).get("used_percent"))
                                if used_pct is not None:
                                    res["used_percent"] = used_pct
                                    res["invalid_used_percent"] = used_pct >= self.used_percent_threshold
                            return res
                except Exception as e:
                    if attempt >= retries:
                        res["error"] = str(e)
                        return res
            return res

        async def delete_one(session, name: str):
            if not name:
                return False
            from urllib.parse import quote
            encoded = quote(name, safe="")
            try:
                async with semaphore:
                    async with session.delete(f"{_join_mgmt_url(self.base_url, '/management/auth-files')}?name={encoded}", headers=_mgmt_headers(self.token), timeout=timeout) as resp:
                        text = await resp.text()
                        data = _safe_json(text)
                        return resp.status == 200 and data.get("status") == "ok"
            except Exception:
                return False

        invalid_list = []
        async with aiohttp.ClientSession(connector=connector, timeout=client_timeout, trust_env=True) as session:
            tasks = [asyncio.create_task(probe_one(session, item)) for item in candidates]
            for task in asyncio.as_completed(tasks):
                r = await task
                if r.get("invalid_401") or r.get("invalid_used_percent"):
                    invalid_list.append(r)

            delete_tasks = [asyncio.create_task(delete_one(session, r.get("name"))) for r in invalid_list if r.get("name")]
            deleted_ok = 0
            deleted_fail = 0
            for task in asyncio.as_completed(delete_tasks):
                if await task:
                    deleted_ok += 1
                else:
                    deleted_fail += 1

        return {
            "total": len(files),
            "candidates": len(candidates),
            "invalid_count": len(invalid_list),
            "deleted_ok": deleted_ok,
            "deleted_fail": deleted_fail,
        }

    def probe_and_clean_sync(self, workers: int = 20, timeout: int = 10, retries: int = 1):
        return asyncio.run(self.probe_and_clean_async(workers, timeout, retries))


def _build_cpa_maintainer(args):
    base_url = (args.cpa_base_url or os.getenv("CPA_BASE_URL") or "").strip()
    token = (args.cpa_token or os.getenv("CPA_TOKEN") or "").strip()
    if not base_url or not token:
        print("[CPA] 未提供 cpa_base_url / cpa_token，跳过 CPA 上传/清理")
        return None
    try:
        return MiniPoolMaintainer(
            base_url,
            token,
            target_type="codex",
            used_percent_threshold=args.cpa_used_threshold,
            user_agent=DEFAULT_MGMT_UA,
        )
    except Exception as e:
        print(f"[CPA] 创建维护器失败: {e}")
        return None


def _upload_token_to_cpa(pm, token_json: str, email: str, proxy: str = "") -> bool:
    if not pm:
        return False
    try:
        data = json.loads(token_json)
    except Exception as e:
        print(f"[CPA] 解析 token_json 失败: {e}")
        return False
    fname_email = email.replace("@", "_")
    filename = f"token_{fname_email}_{int(time.time())}.json"
    ok = pm.upload_token(filename=filename, token_data=data, proxy=proxy or "")
    if ok:
        print(f"[CPA] 已上传 {filename} 到 CPA")
    else:
        print("[CPA] 上传失败")
    return ok


def _clean_invalid_in_cpa(pm, args):
    if not pm:
        return None
    try:
        res = pm.probe_and_clean_sync(
            workers=max(1, args.cpa_workers),
            timeout=max(5, args.cpa_timeout),
            retries=max(0, args.cpa_retries),
        )
        print(
            f"[CPA] 清理完成: total={res.get('total')} candidates={res.get('candidates')} "
            f"invalid={res.get('invalid_count')} deleted_ok={res.get('deleted_ok')} deleted_fail={res.get('deleted_fail')}"
        )
        return res
    except Exception as e:
        print(f"[CPA] 清理失败: {e}")
        return None


def _count_valid_cpa_tokens(pm, args):
    if not pm:
        return 0
    try:
        files = pm.fetch_auth_files(timeout=max(5, args.cpa_timeout))
        target = pm.target_type.lower()
        valid = [f for f in files if _get_item_type(f).lower() == target]
        return len(valid)
    except Exception as e:
        print(f"[CPA] 统计 token 失败: {e}")
        return 0


def _run_cpa_codex_oauth(args, pm) -> int:
    if not pm:
        print("[CPA OAuth] 未配置 cpa_base_url / cpa_token，无法发起 OAuth。")
        return 1

    callback_server = None
    if args.cpa_oauth_listen and not str(args.cpa_oauth_callback_url or "").strip():
        try:
            callback_server = _start_local_oauth_callback_server(
                host=str(args.cpa_oauth_listen_host or "localhost").strip() or "localhost",
                port=max(1, int(args.cpa_oauth_listen_port or 1455)),
            )
            print(f"[CPA OAuth] 已启动本地回调监听: http://{callback_server.host}:{callback_server.port}/auth/callback")
        except Exception as e:
            print(f"[CPA OAuth] 启动本地回调监听失败: {e}")
            if args.cpa_oauth_no_prompt:
                return 1
            print("[CPA OAuth] 将回退到手动粘贴 localhost 回调 URL。")

    callback_url = str(args.cpa_oauth_callback_url or "").strip()
    callback_state = _parse_callback_url(callback_url).get("state", "") if callback_url else ""
    state = str(args.cpa_oauth_state or callback_state or "").strip()
    auth_url = ""
    should_request_auth = bool(args.cpa_codex_oauth) or not state

    if should_request_auth:
        try:
            payload = pm.request_codex_oauth_url(timeout=max(10, args.cpa_timeout), proxy=args.proxy or "", is_webui=True)
        except Exception as e:
            print(f"[CPA OAuth] 获取 Codex 授权链接失败: {e}")
            return 1
        auth_url = str(payload.get("url") or "").strip()
        state = str(payload.get("state") or state).strip()
        if not state and auth_url:
            try:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query, keep_blank_values=True)
                state = (query.get("state", [""])[0] or "").strip()
            except Exception:
                state = state or ""
        print(f"[CPA OAuth] state: {state or '(空)'}")
        print(f"[CPA OAuth] 授权链接:\n{auth_url}")
        print("[CPA OAuth] 完成浏览器登录后，把地址栏里的 localhost 回调 URL 整段粘回来即可。")
        if args.cpa_oauth_open_browser and auth_url:
            try:
                opened = webbrowser.open(auth_url)
                print(f"[CPA OAuth] 已请求打开浏览器: {'成功' if opened else '失败/被忽略'}")
            except Exception as e:
                print(f"[CPA OAuth] 打开浏览器失败: {e}")

    if not state:
        print("[CPA OAuth] 未拿到 state，无法轮询状态。")
        return 1

    try:
        submitted_callback = False
        pending_callback_url = callback_url
        status_error_count = 0
        if callback_url:
            try:
                result = pm.submit_oauth_callback("codex", callback_url, timeout=max(10, args.cpa_timeout), proxy=args.proxy or "")
                print(f"[CPA OAuth] 提交回调成功: {json.dumps(result, ensure_ascii=False)}")
                submitted_callback = True
                pending_callback_url = ""
            except Exception as e:
                print(f"[CPA OAuth] 提交回调失败: {e}")
                return 1
        elif not args.cpa_oauth_no_prompt and not callback_server:
            user_input = input("[CPA OAuth] 粘贴完整 localhost 回调 URL（直接回车则只轮询状态，输入 q 退出）: ").strip()
            if user_input.lower() in {"q", "quit", "exit"}:
                print("[CPA OAuth] 用户取消。")
                return 0
            if user_input:
                try:
                    result = pm.submit_oauth_callback("codex", user_input, timeout=max(10, args.cpa_timeout), proxy=args.proxy or "")
                    print(f"[CPA OAuth] 提交回调成功: {json.dumps(result, ensure_ascii=False)}")
                    submitted_callback = True
                except Exception as e:
                    print(f"[CPA OAuth] 提交回调失败: {e}")
                    return 1

        started_at = time.monotonic()
        poll_interval = max(1, int(args.cpa_oauth_poll_interval))
        timeout_sec = max(30, int(args.cpa_oauth_timeout))

        while time.monotonic() - started_at < timeout_sec:
            elapsed = int(time.monotonic() - started_at)
            if callback_server and not submitted_callback and callback_server.event.is_set():
                auto_callback_url = callback_server.capture.callback_url
                if auto_callback_url:
                    pending_callback_url = auto_callback_url

            if pending_callback_url and not submitted_callback:
                print(f"[CPA OAuth] 已捕获 localhost 回调，尝试提交给 CPA: {pending_callback_url}")
                try:
                    result = pm.submit_oauth_callback("codex", pending_callback_url, timeout=max(10, args.cpa_timeout), proxy=args.proxy or "")
                    print(f"[CPA OAuth] 自动提交回调成功: {json.dumps(result, ensure_ascii=False)}")
                    submitted_callback = True
                    pending_callback_url = ""
                except Exception as e:
                    print(f"[CPA OAuth] 自动提交回调失败（稍后重试）: {e}")

            try:
                status_payload = pm.get_oauth_status(state, timeout=max(10, args.cpa_timeout), proxy=args.proxy or "")
                status_error_count = 0
            except Exception as e:
                status_error_count += 1
                print(f"[CPA OAuth] 查询状态失败（第 {status_error_count}/{CPA_OAUTH_STATUS_ERROR_TOLERANCE} 次，稍后重试）: {e}")
                if status_error_count >= CPA_OAUTH_STATUS_ERROR_TOLERANCE:
                    print("[CPA OAuth] 连续查询状态失败次数过多，终止本轮 OAuth 轮询。")
                    return 1
                if callback_server and not submitted_callback:
                    callback_server.event.wait(timeout=min(poll_interval, 1))
                else:
                    time.sleep(poll_interval)
                continue

            status = str(status_payload.get("status") or "").strip().lower() or "wait"
            error_text = str(status_payload.get("error") or "").strip()
            print(f"[CPA OAuth] 状态: {status} ({elapsed}s/{timeout_sec}s)")
            if error_text:
                print(f"[CPA OAuth] 错误: {error_text}")

            if status == "ok":
                print("[CPA OAuth] OAuth 流程完成，请到 CPA 的 Auth Files 查看新生成的 Codex 凭证。")
                return 0
            if status == "error":
                return 1

            if callback_server and not submitted_callback:
                callback_server.event.wait(timeout=min(poll_interval, 1))
            else:
                time.sleep(poll_interval)

        print(f"[CPA OAuth] 超时：{timeout_sec}s 内未完成 OAuth 流程。")
        return 1
    finally:
        if callback_server:
            callback_server.shutdown()


# 账号行清理：上传成功且开启 prune_local 后使用
# 安全处理：文件不存在直接返回，写入保持末尾换行便于追加

def _remove_account_entry(accounts_path: Path, email: str, real_pwd: str):
    if not accounts_path.exists():
        return
    try:
        lines = accounts_path.read_text(encoding="utf-8").splitlines()
        target = f"{email}----{real_pwd}"
        kept = [ln for ln in lines if ln.strip() != target]
        accounts_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        print(f"[本地清理] 已从 accounts.txt 移除: {email}")
    except Exception as e:
        print(f"[本地清理] 移除账号行失败: {e}")

# ========== 主注册流程 (恢复详细日志与异常捕获) ==========

def run(proxy: Optional[str], mail_provider: str = "auto", email_timeout: int = 900, otp_resend_interval: int = 300):
    proxies = {"http": proxy, "https": proxy} if proxy else None
    s = requests.Session(proxies=proxies, impersonate="chrome")
    s.headers.update({
        "user-agent": UA,
        "accept": "application/json, text/plain, */*",
    })

    print(f"\n{'='*20} 开启注册流程 {'='*20}")
    try:
        print(f"[步骤1] 正在初始化临时邮箱（provider={mail_provider}）...")
        email, password, code_fetcher, extract_all_codes, extract_code_candidates, actual_mail_provider, email_client = get_email_and_code_fetcher(proxies, provider=mail_provider)
        print(f"[*] 当前邮箱提供商: {actual_mail_provider}")
        if not email:
            print("[失败] 未能获取邮箱")
            return None
        print(f"[成功] 邮箱: {email} | 临时密码: {password}")

        print("[步骤2] 访问 OpenAI 授权页获取 Device ID...")
        oauth = generate_oauth_url()
        auth_page = s.get(oauth.auth_url, timeout=15)
        did = s.cookies.get("oai-did")
        if not did:
            print("[失败] 未能从 Cookie 获取 oai-did")
            return None
        print(f"[成功] Device ID: {did}")

        print("[步骤3] 获取 Sentinel 载荷并提交注册邮箱...")
        try:
            authorize_continue_sentinel = _build_sentinel_payload(s, did, "authorize_continue")
        except Exception as e:
            print(f"[失败] 获取 authorize_continue Sentinel 失败: {e}")
            return None

        continue_url = ""
        try:
            auth_json = auth_page.json() if hasattr(auth_page, "json") else {}
            continue_url = str((auth_json or {}).get("continue_url") or "").strip()
        except Exception:
            continue_url = ""
        if continue_url:
            try:
                s.get(continue_url, timeout=15)
            except Exception:
                pass

        signup_res = s.post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers={
                "referer": "https://auth.openai.com/create-account",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": authorize_continue_sentinel,
            },
            data=json.dumps({"username": {"value": email, "kind": "email"}, "screen_hint": "signup"}),
            timeout=15,
        )
        print(f"[日志] 邮箱提交状态: {signup_res.status_code}")
        if signup_res.status_code != 200:
            print(f"[失败] 邮箱提交失败: {signup_res.text[:200]}")
            return None

        print("[步骤4] 设置账户密码...")
        pwd_res = s.post(
            "https://auth.openai.com/api/accounts/user/register",
            headers={
                "referer": "https://auth.openai.com/create-account/password",
                "accept": "application/json",
                "content-type": "application/json",
            },
            json={"password": password, "username": email},
            timeout=15,
        )
        print(f"[日志] 密码设置状态: {pwd_res.status_code}")
        if pwd_res.status_code != 200:
            print(f"[失败] 密码设置失败: {pwd_res.text[:200]}")
            return None

        print("[步骤5] 触发 OpenAI 发送验证邮件...")
        s.get("https://auth.openai.com/create-account/password", timeout=15)
        try:
            email_otp_sentinel = _build_sentinel_payload(s, did, "authorize_continue")
        except Exception as e:
            print(f"[警告] 获取发送验证码 Sentinel 失败（将无 token 继续）: {e}")
            email_otp_sentinel = ""
        otp_send_headers = {
            "referer": "https://auth.openai.com/create-account/password",
            "accept": "application/json",
        }
        if email_otp_sentinel:
            otp_send_headers["openai-sentinel-token"] = email_otp_sentinel
        otp_send_res = s.get(
            "https://auth.openai.com/api/accounts/email-otp/send",
            headers=otp_send_headers,
            timeout=15,
        )
        print(f"[日志] 发送指令状态: {otp_send_res.status_code} | body: {otp_send_res.text[:300]}")
        if otp_send_res.status_code != 200:
            print(f"[失败] 发送验证码失败: {otp_send_res.text[:200]}")
            return None

        # 跟随 continue_url 访问 email-verification 页面 —— 浏览器会自动跳转此处
        # 这个 GET 才是真正触发 OpenAI 后台发信的信号，脚本之前少了这步
        try:
            otp_continue = str((otp_send_res.json() or {}).get("continue_url") or "").strip()
        except Exception:
            otp_continue = ""
        otp_continue = otp_continue or "https://auth.openai.com/email-verification"
        print(f"[步骤5b] 跟随跳转触发邮件发送: GET {otp_continue}")
        s.get(otp_continue, headers={"referer": "https://auth.openai.com/create-account/password"}, timeout=15)

        print(f"[步骤6] 等待邮箱接收 6 位验证码（超时 {email_timeout}s，每 {otp_resend_interval}s 无码自动重发）...")
        code = None
        reg_otp_send_time = time.time()
        reg_start = time.monotonic()
        reg_last_send = reg_start
        while not code and time.monotonic() - reg_start < email_timeout:
            seg = min(otp_resend_interval, int(email_timeout - (time.monotonic() - reg_start)))
            if seg <= 0:
                break
            code = code_fetcher(timeout_sec=seg, min_received_ts=reg_otp_send_time)
            if code:
                break
            elapsed_reg = int(time.monotonic() - reg_start)
            if elapsed_reg < email_timeout:
                print(f"[步骤6] {seg}s 内未收到验证码（已等 {elapsed_reg}s），重新触发发送...")
                try:
                    reg_otp_send_time = time.time()
                    rr = s.get(
                        "https://auth.openai.com/api/accounts/email-otp/send",
                        headers={**otp_send_headers},
                        timeout=15,
                    )
                    print(f"[步骤6] 重发指令状态: {rr.status_code}")
                    s.get(otp_continue, headers={"referer": "https://auth.openai.com/create-account/password"}, timeout=15)
                except Exception as _re:
                    print(f"[步骤6] 重发失败: {_re}")
        if not code:
            print("[失败] 邮箱长时间未收到验证码")
            return None
        print(f"[成功] 捕获验证码: {code}")

        print("[步骤7] 提交验证码至 OpenAI...")
        val_res = s.post(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            headers={
                "referer": "https://auth.openai.com/email-verification",
                "accept": "application/json",
                "content-type": "application/json",
            },
            json={"code": code},
            timeout=15,
        )
        print(f"[日志] 验证码校验状态: {val_res.status_code}")
        if val_res.status_code != 200:
            print(f"[失败] OTP 校验不通过: {val_res.text[:200]}")
            return None
        print("[成功] 注册 OTP 校验完成")
        if hasattr(email_client, "cleanup_email"):
            email_client.cleanup_email(email)

        print("[步骤8] 完善账户基本信息...")
        try:
            create_account_sentinel = _build_sentinel_payload(s, did, "authorize_continue")
        except Exception as e:
            print(f"[失败] 获取 create_account Sentinel 失败: {e}")
            return None

        acc_res = s.post(
            "https://auth.openai.com/api/accounts/create_account",
            headers={
                "referer": "https://auth.openai.com/about-you",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": create_account_sentinel,
            },
            data=json.dumps({"name": _random_name(), "birthdate": _random_birthdate()}),
            timeout=15,
        )
        print(f"[日志] 账户创建状态: {acc_res.status_code}")
        if acc_res.status_code != 200:
            print(f"[失败] 账户创建失败: {acc_res.text[:200]}")
            return None

        print("[步骤9] 注册完成，重新走登录流程获取 Workspace / Token...")
        for login_attempt in range(3):
            try:
                print(f"[*] 正在通过登录流程获取 Token...{f' (重试 {login_attempt}/3)' if login_attempt else ''}")
                token_json = perform_codex_oauth_login_http(
                    email=email,
                    password=password,
                    extract_code_candidates=extract_code_candidates,
                    email_client=email_client,
                    proxies=proxies,
                    user_agent=UA,
                    email_timeout=email_timeout,
                )
                if token_json:
                    print("[大功告成] 账号注册完毕！")
                    return token_json, email, password
            except Exception as e:
                print(f"[失败] 登录补全流程异常: {e}")
            time.sleep(2)

        print("[失败] 登录补全流程 3 次均未完成。")
        return None
    except Exception as e:
        print(f"[致命错误] 流程崩溃: {e}")
        return None

# ========== Main 保持原版完整结构与输出格式 ==========

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy", default=DEFAULT_PROXY or None, help="代理地址 [env: HTTP_PROXY / HTTPS_PROXY]")
    parser.add_argument("--mail-provider", choices=["auto", "gptmail", "tempmail", "custom"], default=DEFAULT_MAIL_PROVIDER, help="邮箱提供商 [env: MAIL_PROVIDER]")
    parser.add_argument("--email-timeout", type=int, default=DEFAULT_EMAIL_TIMEOUT, help="等待验证码邮件的最大超时（秒）[env: EMAIL_TIMEOUT，默认 900]")  
    parser.add_argument("--otp-resend-interval", type=int, default=DEFAULT_OTP_RESEND_INTERVAL, help="OTP 等待超过此秒数无响应则重发（秒）[env: OTP_RESEND_INTERVAL，默认 300]")  
    parser.add_argument("--once", action="store_true", default=_as_bool(os.getenv("RUN_ONCE")), help="只运行一次 [env: RUN_ONCE]")
    parser.add_argument("--sleep-min", type=int, default=DEFAULT_SLEEP_MIN, help="最小间隔(秒) [env: SLEEP_MIN]")  
    parser.add_argument("--sleep-max", type=int, default=DEFAULT_SLEEP_MAX, help="最大间隔(秒) [env: SLEEP_MAX]")

    parser.add_argument("--sub2api-base-url", default=os.getenv("SUB2API_BASE_URL"), help="Sub2API 基础地址")
    parser.add_argument("--sub2api-admin-api-key", default=os.getenv("SUB2API_ADMIN_API_KEY"), help="Sub2API 管理端全局 API Key（优先使用）")
    parser.add_argument("--sub2api-bearer", default=os.getenv("SUB2API_BEARER"), help="Sub2API Bearer token（兼容旧方式）")
    parser.add_argument("--sub2api-email", default=os.getenv("SUB2API_EMAIL"), help="Sub2API 管理员邮箱（旧登录方式）")
    parser.add_argument("--sub2api-password", default=os.getenv("SUB2API_PASSWORD"), help="Sub2API 管理员密码（旧登录方式）")
    parser.add_argument("--sub2api-group-ids", default=os.getenv("SUB2API_GROUP_IDS", "2"), help="Sub2API 绑定分组，逗号分隔")
    parser.add_argument("--sub2api-upload", action="store_true", default=DEFAULT_SUB2API_UPLOAD, help="注册成功后自动上传到 Sub2API [env: AUTO_UPLOAD_SUB2API]")
    parser.add_argument("--cpa-base-url", default=os.getenv("CPA_BASE_URL"), help="CPA 基础地址 [env: CPA_BASE_URL]")
    parser.add_argument("--cpa-token", default=os.getenv("CPA_TOKEN"), help="CPA 管理 token [env: CPA_TOKEN]")
    parser.add_argument("--cpa-workers", type=int, default=DEFAULT_CPA_WORKERS, help="CPA 清理并发 [env: CPA_WORKERS，默认 1]")
    parser.add_argument("--cpa-timeout", type=int, default=DEFAULT_CPA_TIMEOUT, help="CPA 请求超时 [env: CPA_TIMEOUT，默认 12]")
    parser.add_argument("--cpa-retries", type=int, default=DEFAULT_CPA_RETRIES, help="CPA 清理重试次数 [env: CPA_RETRIES，默认 1]")
    parser.add_argument("--cpa-used-threshold", type=int, default=DEFAULT_CPA_USED_THRESHOLD, help="CPA used_percent 阈值 [env: CPA_USED_THRESHOLD，默认 95]")
    parser.add_argument("--cpa-clean", action="store_true", default=DEFAULT_CPA_CLEAN, help="注册后自动清理 CPA 失效账号 [env: CPA_CLEAN]")
    parser.add_argument("--cpa-upload", action="store_true", default=DEFAULT_CPA_UPLOAD, help="注册后自动上传 CPA [env: CPA_UPLOAD]")
    parser.add_argument("--cpa-target-count", type=int, default=DEFAULT_CPA_TARGET_COUNT, help="目标 token 数(有效) [env: CPA_TARGET_COUNT，默认 300]")
    parser.add_argument("--cpa-codex-oauth", action="store_true", help="通过 CPA 发起 Codex OAuth，并等待回调/状态完成")
    parser.add_argument("--cpa-oauth-state", default=os.getenv("CPA_OAUTH_STATE"), help="已有 OAuth state，用于继续轮询状态")
    parser.add_argument("--cpa-oauth-callback-url", default=os.getenv("CPA_OAUTH_CALLBACK_URL"), help="已拿到的 localhost 回调 URL，可直接提交给 CPA")
    parser.add_argument("--cpa-oauth-poll-interval", type=int, default=DEFAULT_CPA_OAUTH_POLL_INTERVAL, help="OAuth 状态轮询间隔秒数 [env: CPA_OAUTH_POLL_INTERVAL，默认 5]")
    parser.add_argument("--cpa-oauth-timeout", type=int, default=DEFAULT_CPA_OAUTH_TIMEOUT, help="OAuth 总等待超时秒数 [env: CPA_OAUTH_TIMEOUT，默认 900]")
    parser.add_argument("--cpa-oauth-open-browser", action="store_true", default=DEFAULT_CPA_OAUTH_OPEN_BROWSER, help="发起授权后尝试自动打开浏览器 [env: CPA_OAUTH_OPEN_BROWSER]")
    parser.add_argument("--cpa-oauth-no-prompt", action="store_true", default=DEFAULT_CPA_OAUTH_NO_PROMPT, help="OAuth 模式下不等待手动粘贴回调 URL，只轮询状态 [env: CPA_OAUTH_NO_PROMPT]")
    parser.add_argument("--cpa-oauth-listen", action="store_true", default=DEFAULT_CPA_OAUTH_LISTEN, help="在 localhost:1455 自动监听 OAuth 回调并提交给 CPA [env: CPA_OAUTH_LISTEN]")
    parser.add_argument("--cpa-oauth-listen-host", default=os.getenv("CPA_OAUTH_LISTEN_HOST", "localhost"), help="本地 OAuth 回调监听 host [env: CPA_OAUTH_LISTEN_HOST，默认 localhost]")
    parser.add_argument("--cpa-oauth-listen-port", type=int, default=int(os.getenv("CPA_OAUTH_LISTEN_PORT") or "1455"), help="本地 OAuth 回调监听端口 [env: CPA_OAUTH_LISTEN_PORT，默认 1455]")
    parser.add_argument("--prune-local", action="store_true", default=DEFAULT_PRUNE_LOCAL, help="上传成功后删除本地 token 文件与账号行 [env: PRUNE_LOCAL]")
    args = parser.parse_args()

    tokens_dir = OUT_DIR / "tokens"
    tokens_dir.mkdir(parents=True, exist_ok=True)

    sub2api_settings = _resolve_sub2api_settings(args)
    pm = _build_cpa_maintainer(args)

    oauth_mode = any([
        bool(args.cpa_codex_oauth),
        bool(str(args.cpa_oauth_state or "").strip()),
        bool(str(args.cpa_oauth_callback_url or "").strip()),
        bool(args.cpa_oauth_open_browser),
        bool(args.cpa_oauth_no_prompt),
    ])
    if oauth_mode:
        raise SystemExit(_run_cpa_codex_oauth(args, pm))

    count = 0
    while True:
        count += 1
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] >>> 流程 #{count} <<<")

        if pm:
            if args.cpa_clean:
                print(
                    f"[CPA] 开始预清理: workers={max(1, args.cpa_workers)} "
                    f"timeout={max(5, args.cpa_timeout)} retries={max(0, args.cpa_retries)}"
                )
                _clean_invalid_in_cpa(pm, args)
            print("[CPA] 开始统计当前有效 token...")
            current_count = _count_valid_cpa_tokens(pm, args)
            print(f"[CPA] 当前有效 token: {current_count} / {args.cpa_target_count}")
            if current_count >= args.cpa_target_count:
                if args.once:
                    break
                wait_time = random.randint(args.sleep_min, args.sleep_max)
                print(f"[*] 随机休息 {wait_time} 秒...")
                time.sleep(wait_time)
                continue

        res = run(args.proxy, args.mail_provider, email_timeout=args.email_timeout, otp_resend_interval=args.otp_resend_interval)
        if res:
            token_json, email, real_pwd = res
            print(f"[🎉] 成功! {email} ---- {real_pwd}")

            # 1. 保存账号密码到 tokens/accounts.txt
            with open(tokens_dir / "accounts.txt", "a", encoding="utf-8") as f:
                f.write(f"{email}----{real_pwd}\n")

            # 2. 保存详细 Token JSON
            fname_email = email.replace("@", "_")
            token_file = tokens_dir / f"token_{fname_email}_{int(time.time())}.json"
            token_file.write_text(token_json, encoding="utf-8")
            print(f"[*] Token 文件已保存: {token_file.name}")

            try:
                tokens = json.loads(token_json)
            except Exception as e:
                print(f"[本地] 解析 token_json 失败: {e}")
                tokens = None

            # 3. 自动上传 Sub2API（可选）
            sub2api_upload_ok = False
            if tokens and sub2api_settings.get("auto_upload") and sub2api_settings.get("base_url") and tokens.get("refresh_token"):
                sub2api_upload_ok = _push_account_to_sub2api(email, tokens, sub2api_settings)

            # 4. 上传 CPA（可选）
            cpa_upload_ok = False
            if args.cpa_upload:
                cpa_upload_ok = _upload_token_to_cpa(pm, token_json, email, proxy=args.proxy or "")

            # 5. 上传成功后按需删除本地文件/账号行（Sub2API / CPA 任一成功即可）
            if args.prune_local and (sub2api_upload_ok or cpa_upload_ok):
                try:
                    if token_file.exists():
                        token_file.unlink()
                        print(f"[本地清理] 已删除 token 文件: {token_file.name}")
                except Exception as e:
                    print(f"[本地清理] 删除 token 文件失败: {e}")
                _remove_account_entry(tokens_dir / "accounts.txt", email, real_pwd)

            # 6. 注册后再清理一次（可选）
            if pm and args.cpa_clean:
                print("[CPA] 开始注册后清理...")
                _clean_invalid_in_cpa(pm, args)
        else:
            print("[-] 本次注册流程未能完成。")

        if args.once:
            break

        wait_time = random.randint(args.sleep_min, args.sleep_max)
        print(f"[*] 随机休息 {wait_time} 秒...")
        time.sleep(wait_time)

if __name__ == "__main__":
    main()
