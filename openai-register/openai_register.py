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
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Any, Dict, Optional, List
import urllib.parse
import urllib.request
import urllib.error

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
DEFAULT_CPA_UPLOAD = _as_bool(os.getenv("CPA_UPLOAD"))
DEFAULT_CPA_CLEAN = _as_bool(os.getenv("CPA_CLEAN"))
DEFAULT_PRUNE_LOCAL = _as_bool(os.getenv("PRUNE_LOCAL"))
DEFAULT_SUB2API_UPLOAD = _as_bool(os.getenv("AUTO_UPLOAD_SUB2API"))

# ========== 自定义域名邮箱客户端（Cloudflare → QQ IMAP） ==========
import imaplib
import email as _email_lib
from email.header import decode_header as _decode_header


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

    def _fetch_folder_msgs(self, conn: imaplib.IMAP4_SSL, folder: str, n: int) -> List[_email_lib.message.Message]:
        """从指定文件夹取最新 n 封邮件"""
        msgs: List[_email_lib.message.Message] = []
        try:
            status, _ = conn.select(folder, readonly=True)
            if status != "OK":
                return msgs
            _, data = conn.search(None, "ALL")
            all_ids = (data[0] or b"").split()
            target_ids = list(reversed(all_ids[-n:]))  # 最新在前
            for uid in target_ids:
                _, msg_data = conn.fetch(uid, "(RFC822)")
                for part in msg_data:
                    if isinstance(part, tuple):
                        msg = _email_lib.message_from_bytes(part[1])
                        msgs.append(msg)
        except Exception as e:
            print(f"[custom-imap] 文件夹 {folder!r} 读取失败: {e}")
        return msgs

    def _fetch_latest_msgs(self, n: int = 30) -> List[_email_lib.message.Message]:
        """同时取 INBOX 和垃圾邮件文件夹最新 n 封（最新在前）"""
        msgs: List[_email_lib.message.Message] = []
        # QQ 邮箱垃圾箱候选文件夹名
        spam_folders = ["Junk", "垃圾邮件", "Spam", "SPAM", "Bulk Mail"]
        try:
            conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            conn.login(self.imap_user, self.imap_pass)
            try:
                # 主收件箱
                msgs.extend(self._fetch_folder_msgs(conn, self.imap_folder, n))
                # 垃圾邮件文件夹（逐个尝试）
                for folder in spam_folders:
                    extra = self._fetch_folder_msgs(conn, folder, n)
                    if extra:
                        print(f"[custom-imap] 垃圾箱 {folder!r} 中额外找到 {len(extra)} 封邮件")
                        msgs.extend(extra)
                        break
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass
        except Exception as e:
            print(f"[custom-imap] IMAP 连接/读取失败: {e}")
        return msgs

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
        check_headers = ["To", "Delivered-To", "X-Forwarded-To", "Envelope-To", "X-Original-To", "Cc"]
        for h in check_headers:
            val = _decode_mime_str(msg.get(h, "")).lower()
            if target_lower in val:
                return True
        # 保底：正文中出现目标邮址也算（应对转发头被完全改写的情况）
        body = self._get_body(msg).lower()
        if target_lower in body:
            return True
        return False

    def extract_codes_for(self, target_email: str, n: int = 30) -> List[str]:
        """取最新 n 封邮件，返回发给 target_email 的所有 6 位验证码列表"""
        codes: List[str] = []
        msgs = self._fetch_latest_msgs(n)
        for msg in msgs:
            fr = _decode_mime_str(msg.get("From", ""))
            subj = _decode_mime_str(msg.get("Subject", ""))
            if not self._msg_targets_email(msg, target_email):
                continue
            body = self._get_body(msg)
            text = f"{subj} {body}"
            found = re.findall(r"(?<!\d)(\d{6})(?!\d)", text)
            if found:
                print(f"[custom-imap] ✅ 命中: From={fr[:50]} Subject={subj[:60]} codes={found}")
            codes.extend(found)
        return codes

    def fetch_code(
        self,
        target_email: str,
        timeout_sec: int = 180,
        poll: float = 6.0,
        exclude_codes: Optional[List[str]] = None,
    ) -> Optional[str]:
        exclude = set(exclude_codes or [])
        start = time.monotonic()
        attempt = 0
        while time.monotonic() - start < timeout_sec:
            attempt += 1
            codes = self.extract_codes_for(target_email)
            print(f"[otp][custom] 轮询 #{attempt}, 共匹配 {len(codes)} 个候选码, 收件目标: {target_email}")
            for code in codes:
                if code not in exclude:
                    return code
            time.sleep(poll)
        return None


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

        def fetch_code(timeout_sec: int = 180, poll: float = 6.0, exclude_codes: Optional[List[str]] = None) -> str | None:
            return client.fetch_code(email, timeout_sec=timeout_sec, poll=poll, exclude_codes=exclude_codes)

        return email, _gen_password(), fetch_code, _extract_all_codes, "custom"

    def _build_tempmail_bundle():
        inbox = EMail(proxies)
        email = inbox.address

        def _extract_all_codes() -> List[str]:
            results: List[str] = []
            try:
                msgs = inbox._get_messages()
                for msg_data in msgs:
                    msg = Message(msg_data)
                    body = msg.body or msg.html_body or msg.subject or ""
                    results.extend(re.findall(r"\b(\d{6})\b", body))
            except Exception:
                pass
            return results

        def fetch_code(timeout_sec: int = 180, poll: float = 6.0, exclude_codes: Optional[List[str]] = None) -> str | None:
            exclude = set(exclude_codes or [])
            start = time.monotonic()
            attempt = 0
            while time.monotonic() - start < timeout_sec:
                attempt += 1
                try:
                    msgs = inbox._get_messages()
                    print(f"[otp][tempmail] 轮询 #{attempt}, 收到 {len(msgs)} 封邮件, 目标: {email}")
                    for msg_data in msgs:
                        msg = Message(msg_data)
                        body = msg.body or msg.html_body or msg.subject or ""
                        for code in re.findall(r"\b(\d{6})\b", body):
                            if code not in exclude:
                                return code
                except Exception:
                    pass
                time.sleep(poll)
            return None

        return email, _gen_password(), fetch_code, _extract_all_codes, "tempmail"

    def _build_gptmail_bundle():
        client = GPTMailClient(proxies)
        email = client.generate_email()

        def _extract_all_codes() -> List[str]:
            regex = r"(?<!\d)(\d{6})(?!\d)"
            results: List[str] = []
            try:
                summaries = client.list_emails(email)
                for s in summaries:
                    body = " ".join([
                        str(s.get("subject", "") or ""),
                        str(s.get("text", "") or ""),
                        str(s.get("body", "") or ""),
                        str(s.get("html", "") or ""),
                        json.dumps(s, ensure_ascii=False),
                    ])
                    results.extend(re.findall(regex, body))
            except Exception:
                pass
            return results

        def fetch_code(timeout_sec: int = 180, poll: float = 6.0, exclude_codes: Optional[List[str]] = None) -> str | None:
            exclude = set(exclude_codes or [])
            start = time.monotonic()
            attempt = 0
            while time.monotonic() - start < timeout_sec:
                attempt += 1
                try:
                    summaries = client.list_emails(email)
                    print(f"[otp][gptmail] 轮询 #{attempt}, 收到 {len(summaries)} 封邮件, 目标: {email}")
                    for s in summaries:
                        body = " ".join([
                            str(s.get("subject", "") or ""),
                            str(s.get("text", "") or ""),
                            str(s.get("body", "") or ""),
                            str(s.get("html", "") or ""),
                            json.dumps(s, ensure_ascii=False),
                        ])
                        for code in re.findall(r"(?<!\d)(\d{6})(?!\d)", body):
                            if code not in exclude:
                                return code
                except Exception:
                    pass
                time.sleep(poll)
            return None

        return email, _gen_password(), fetch_code, _extract_all_codes, "gptmail"

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



def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


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
        proxies = {"http": proxy, "https": proxy} if proxy else None
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
        email, password, code_fetcher, extract_all_codes, actual_mail_provider = get_email_and_code_fetcher(proxies, provider=mail_provider)
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
        reg_start = time.monotonic()
        reg_last_send = reg_start
        while not code and time.monotonic() - reg_start < email_timeout:
            seg = min(otp_resend_interval, int(email_timeout - (time.monotonic() - reg_start)))
            if seg <= 0:
                break
            code = code_fetcher(timeout_sec=seg)
            if code:
                break
            elapsed_reg = int(time.monotonic() - reg_start)
            if elapsed_reg < email_timeout:
                print(f"[步骤6] {seg}s 内未收到验证码（已等 {elapsed_reg}s），重新触发发送...")
                try:
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
            print(f"[失败] 验证码校验失败: {val_res.text[:200]}")
            return None

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
        first_code = code
        for login_attempt in range(3):
            try:
                print(f"[*] 正在通过登录流程获取 Token...{f' (重试 {login_attempt}/3)' if login_attempt else ''}")
                s2 = requests.Session(proxies=proxies, impersonate="chrome")
                oauth2 = generate_oauth_url()
                s2.get(oauth2.auth_url, timeout=15)
                did2 = s2.cookies.get("oai-did")
                if not did2:
                    print("[失败] 登录会话未能获取 oai-did")
                    continue

                lc = s2.post(
                    "https://auth.openai.com/api/accounts/authorize/continue",
                    headers={
                        "referer": "https://auth.openai.com/log-in",
                        "accept": "application/json",
                        "content-type": "application/json",
                        "openai-sentinel-token": _build_sentinel_payload(s2, did2, "authorize_continue"),
                    },
                    data=json.dumps({"username": {"value": email, "kind": "email"}, "screen_hint": "login"}),
                    timeout=15,
                )
                print(f"[日志] 登录邮箱提交状态: {lc.status_code}")
                if lc.status_code != 200:
                    print(f"[失败] 登录邮箱提交失败: {lc.text[:200]}")
                    continue
                s2.get(str((lc.json() or {}).get("continue_url") or ""), timeout=15)

                pw = s2.post(
                    "https://auth.openai.com/api/accounts/password/verify",
                    headers={
                        "referer": "https://auth.openai.com/log-in/password",
                        "accept": "application/json",
                        "content-type": "application/json",
                        "openai-sentinel-token": _build_sentinel_payload(s2, did2, "authorize_continue"),
                    },
                    json={"password": password},
                    timeout=15,
                )
                print(f"[日志] 登录密码验证状态: {pw.status_code}")
                if pw.status_code != 200:
                    print(f"[失败] 登录密码验证失败: {pw.text[:200]}")
                    continue

                # 记录触发登录 OTP 前 IMAP 中已有的所有邮件的验证码（作为排除基线）
                existing_codes = list(extract_all_codes())
                baseline_codes = set(existing_codes)
                baseline_codes.add(first_code)  # 排除注册流程的 OTP

                # 触发登录 OTP 发送 —— 密码验证完成后 OpenAI 会跳转到邮件验证页面
                otp_send_time = time.time()  # 记录触发时间，用于过滤旧邮件
                s2.get(
                    "https://auth.openai.com/email-verification",
                    headers={"referer": "https://auth.openai.com/log-in/password"},
                    timeout=15,
                )
                # 主动触发 OTP 发送接口
                try:
                    _otp_trigger = s2.get(
                        "https://auth.openai.com/api/accounts/email-otp/send",
                        headers={"referer": "https://auth.openai.com/email-verification", "accept": "application/json"},
                        timeout=15,
                    )
                    print(f"[otp-login] 触发登录 OTP 发送状态: {_otp_trigger.status_code}")
                except Exception as _ote:
                    print(f"[otp-login] 触发 OTP 发送失败（将等待自动发送）: {_ote}")

                print(f"[*] 正在等待登录 OTP（超时 {email_timeout}s，每 {otp_resend_interval}s 无码自动重发）...")
                # 等待几秒让邮件到达 IMAP
                time.sleep(8)

                otp2 = None
                otp_start = time.monotonic()
                otp_last_send = otp_start
                otp_attempt = 0
                while time.monotonic() - otp_start < email_timeout:
                    otp_attempt += 1
                    all_codes = extract_all_codes()
                    # 只接受基线快照之后出现的、且不在排除集中的新验证码
                    new_codes = [c for c in all_codes if c not in baseline_codes]
                    if new_codes:
                        otp2 = new_codes[0]  # 取最新（extract_all_codes 最新在前）
                        break
                    elapsed = int(time.monotonic() - otp_start)
                    # 超过重发间隔则重新触发 OTP 发送
                    if time.monotonic() - otp_last_send >= otp_resend_interval:
                        print(f"[otp-login] {otp_resend_interval}s 未收到 OTP，重新触发发送...")
                        try:
                            rr2 = s2.get(
                                "https://auth.openai.com/api/accounts/email-otp/send",
                                headers={"referer": "https://auth.openai.com/email-verification", "accept": "application/json"},
                                timeout=15,
                            )
                            print(f"[otp-login] 重发状态: {rr2.status_code}")
                            s2.get("https://auth.openai.com/email-verification", timeout=15)
                        except Exception as _re2:
                            print(f"[otp-login] 重发失败: {_re2}")
                        otp_last_send = time.monotonic()
                    print(f"[otp-login] 轮询 #{otp_attempt} ({elapsed}s/{email_timeout}s), 还未收到新 OTP...")
                    time.sleep(8)

                if not otp2:
                    print("[失败] 未收到登录 OTP")
                    continue
                print(f"[成功] 捕获登录 OTP: {otp2}")

                val2 = s2.post(
                    "https://auth.openai.com/api/accounts/email-otp/validate",
                    headers={
                        "referer": "https://auth.openai.com/email-verification",
                        "accept": "application/json",
                        "content-type": "application/json",
                    },
                    json={"code": otp2},
                    timeout=15,
                )
                print(f"[日志] 登录 OTP 校验状态: {val2.status_code}")
                if val2.status_code != 200:
                    print(f"[失败] 登录 OTP 校验失败: {val2.text[:200]}")
                    continue
                val2_data = val2.json() or {}
                print("[成功] 登录 OTP 验证成功")

                consent_url = str(val2_data.get("continue_url") or "").strip()
                consent_data = {}
                if consent_url:
                    consent_resp = s2.get(consent_url, timeout=15)
                    try:
                        consent_data = consent_resp.json() or {}
                    except Exception:
                        consent_data = {}

                auth_cookie = s2.cookies.get("oai-client-auth-session", domain=".auth.openai.com") or s2.cookies.get("oai-client-auth-session")
                if not auth_cookie:
                    print("[失败] 登录后未能获取 oai-client-auth-session")
                    continue
                auth_json = _decode_jwt_segment(auth_cookie.split(".")[0])

                # ── workspace 获取（三层兜底）──────────────────────────────
                workspace_id = ""
                sel_data = {}

                # 层1：从 cookie 读（老账号正常路径）
                if auth_json.get("workspaces"):
                    workspace_id = auth_json["workspaces"][0]["id"]
                    print(f"[成功] Workspace ID (from cookie): {workspace_id}")

                # 层2：consent_url 响应 JSON 里直接含 workspace 信息
                if not workspace_id:
                    ws_from_consent = (
                        (consent_data.get("page") or {}).get("payload") or {}
                    ).get("workspace_id") or (consent_data.get("workspace_id") or "")
                    if ws_from_consent:
                        workspace_id = str(ws_from_consent).strip()
                        print(f"[成功] Workspace ID (from consent response): {workspace_id}")

                # 层3：POST workspace/select 不带 ID，让 OpenAI 返回默认 workspace
                if not workspace_id:
                    print("[*] Cookie 无 workspaces（新账号），尝试直接调用 workspace/select...")
                    try:
                        ws_resp = s2.post(
                            "https://auth.openai.com/api/accounts/workspace/select",
                            headers={"referer": consent_url or "https://auth.openai.com/", "accept": "application/json", "content-type": "application/json"},
                            json={},
                            timeout=15,
                        )
                        print(f"[日志] workspace/select(空) 状态: {ws_resp.status_code} | body: {ws_resp.text[:300]}")
                        ws_data = ws_resp.json() if ws_resp.status_code == 200 else {}
                        # 如果直接返回了 continue_url，则跳过 workspace_id 走重定向
                        if ws_data.get("continue_url"):
                            sel_data = ws_data
                            workspace_id = "__skip__"
                        elif ws_data.get("page", {}).get("type") == "organization_select":
                            sel_data = ws_data
                            workspace_id = "__skip__"
                    except Exception as _we:
                        print(f"[*] workspace/select(空) 失败: {_we}")

                # 层4：兜底 —— 直接跟踪 consent_url 的重定向链，碰运气找到 localhost callback
                if not workspace_id:
                    print("[*] 尝试直接跟踪 consent_url 重定向链获取 OAuth callback...")
                    cbk = None  # 预先初始化，避免 NameError
                    try:
                        r0 = s2.get(consent_url, allow_redirects=False, timeout=15)
                        for _i in range(20):
                            loc0 = r0.headers.get("Location", "")
                            if loc0.startswith("http://localhost"):
                                cbk = loc0
                                break
                            if r0.status_code not in (301, 302, 303) or not loc0:
                                break
                            r0 = s2.get(loc0, allow_redirects=False, timeout=15)
                        # for...else: 20次循环用尽仍未找到 callback，cbk 保持 None
                    except Exception as _cbk_ex:
                        print(f"[*] 跟踪重定向链异常: {_cbk_ex}")
                        cbk = None
                    if cbk:
                        token_json = submit_callback_url(
                            callback_url=cbk, expected_state=oauth2.state,
                            code_verifier=oauth2.code_verifier, redirect_uri=oauth2.redirect_uri, session=s2,
                        )
                        print("[大功告成] 账号注册完毕！(consent 直跳 callback)")
                        return token_json, email, password

                    print(f"[失败] 无法获取 Workspace ID，Cookie 字段: {list(auth_json.keys())}")
                    continue
                # ─────────────────────────────────────────────────────────

                # 已有 workspace_id 且还没拿到 sel_data，走正常 workspace/select 流程
                if workspace_id != "__skip__":
                    print(f"[成功] Workspace ID: {workspace_id}")
                    select_resp = s2.post(
                        "https://auth.openai.com/api/accounts/workspace/select",
                        headers={
                            "referer": consent_url,
                            "accept": "application/json",
                            "content-type": "application/json",
                        },
                        json={"workspace_id": workspace_id},
                        timeout=15,
                    )
                    print(f"[日志] Workspace 选择状态: {select_resp.status_code}")
                    if select_resp.status_code != 200:
                        print(f"[失败] Workspace 选择失败: {select_resp.text[:200]}")
                        continue
                    sel_data = select_resp.json() or {}

                if sel_data.get("page", {}).get("type", "") == "organization_select":
                    orgs = sel_data.get("page", {}).get("payload", {}).get("data", {}).get("orgs", [])
                    if orgs:
                        org_sel = s2.post(
                            "https://auth.openai.com/api/accounts/organization/select",
                            headers={"accept": "application/json", "content-type": "application/json"},
                            json={
                                "org_id": orgs[0].get("id", ""),
                                "project_id": orgs[0].get("default_project_id", ""),
                            },
                            timeout=15,
                        )
                        print(f"[日志] Organization 选择状态: {org_sel.status_code}")
                        if org_sel.status_code != 200:
                            print(f"[失败] Organization 选择失败: {org_sel.text[:200]}")
                            continue
                        sel_data = org_sel.json() or {}

                if "continue_url" not in sel_data:
                    print(f"[失败] 未能获取 continue_url: {json.dumps(sel_data, ensure_ascii=False)[:500]}")
                    continue

                print("[步骤10] 跟踪重定向并换取 Token...")
                r = s2.get(str(sel_data["continue_url"]), allow_redirects=False, timeout=15)
                cbk = None
                for i in range(20):
                    loc = r.headers.get("Location", "")
                    print(f"  -> 重定向 #{i+1} 状态: {r.status_code} | 下一跳: {loc[:80] if loc else '无'}")
                    if loc.startswith("http://localhost"):
                        cbk = loc
                        break
                    if r.status_code not in (301, 302, 303) or not loc:
                        break
                    r = s2.get(loc, allow_redirects=False, timeout=15)

                if not cbk:
                    print("[失败] 未能获取到 Callback URL")
                    continue

                token_json = submit_callback_url(
                    callback_url=cbk,
                    expected_state=oauth2.state,
                    code_verifier=oauth2.code_verifier,
                    redirect_uri=oauth2.redirect_uri,
                    session=s2,
                )
                print("[大功告成] 账号注册完毕！")
                return token_json, email, password
            except Exception as e:
                print(f"[失败] 登录补全流程异常: {e}")
                time.sleep(2)
                continue

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
    parser.add_argument("--prune-local", action="store_true", default=DEFAULT_PRUNE_LOCAL, help="上传成功后删除本地 token 文件与账号行 [env: PRUNE_LOCAL]")
    args = parser.parse_args()

    tokens_dir = OUT_DIR / "tokens"
    tokens_dir.mkdir(parents=True, exist_ok=True)

    sub2api_settings = _resolve_sub2api_settings(args)
    pm = _build_cpa_maintainer(args)

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
