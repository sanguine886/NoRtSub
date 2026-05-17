"""
Session 转 Sub2API / CPA 格式转换器

从 index.html 提取的转换逻辑，将 ChatGPT session.json 转换为 sub2api 格式
"""

import base64
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _b64url_decode(s: str) -> bytes:
    """Base64URL 解码"""
    s += "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _parse_jwt_payload(token: str) -> Optional[Dict]:
    """解析 JWT payload（不验证签名）"""
    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        payload = _b64url_decode(parts[1])
        return json.loads(payload)
    except Exception:
        return None


def _strip_unavailable(obj: Any) -> Any:
    """递归移除 None、空字符串、空字典、空列表"""
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            cv = _strip_unavailable(v)
            if cv is not None and cv != "" and cv != {} and cv != []:
                cleaned[k] = cv
        return cleaned
    if isinstance(obj, list):
        cleaned = []
        for item in obj:
            cv = _strip_unavailable(item)
            if cv is not None and cv != "" and cv != {} and cv != []:
                cleaned.append(cv)
        return cleaned
    return obj


def _to_email_key(email: str) -> str:
    """邮箱转 key 格式: 小写，非字母数字替换为下划线"""
    return re.sub(r"[^a-z0-9]", "_", email.lower())


def _first_non_empty(*values) -> str:
    """返回第一个非空值"""
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _build_synthetic_id_token(
    account_id: str,
    user_id: str,
    plan_type: str,
    email: str,
    now: datetime = None,
) -> str:
    """
    构建合成 id_token (JWT, alg=none)

    对应 index.html 的 buildSyntheticCodexIdToken()
    """
    if now is None:
        now = datetime.now(timezone.utc)
    iat = int(now.timestamp())
    exp = iat + 3600

    header = {"alg": "none", "typ": "JWT", "cpa_synthetic": True}
    payload = {
        "iat": iat,
        "exp": exp,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": plan_type,
            "chatgpt_user_id": user_id,
            "user_id": user_id,
        },
        "email": email,
    }

    def b64url_encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    h = b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    return f"{h}.{p}."


def _normalize_timestamp(dt: datetime = None) -> str:
    """ISO 格式时间戳"""
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def convert_session(session_data: Dict) -> Optional[Dict]:
    """
    将 ChatGPT session.json 转换为 sub2api 格式

    对应 index.html 的 convertSession()

    Args:
        session_data: 原始 session JSON

    Returns:
        {"sub2apiAccount": {...}, "cpa": {...}} 或 None
    """
    if not session_data or not isinstance(session_data, dict):
        return None

    now = datetime.now(timezone.utc)
    exported_at = _normalize_timestamp(now)

    # 提取 token - 兼容多种字段名
    accessToken = (
        session_data.get("accessToken")
        or session_data.get("access_token")
        or ""
    )
    sessionToken = (
        session_data.get("sessionToken")
        or session_data.get("session_token")
        or ""
    )
    refreshToken = (
        session_data.get("refreshToken")
        or session_data.get("refresh_token")
        or ""
    )
    idToken = (
        session_data.get("idToken")
        or session_data.get("id_token")
        or ""
    )

    if not accessToken:
        return None

    # 解析 JWT
    jwt_payload = _parse_jwt_payload(accessToken) or {}

    # 提取字段
    email = _first_non_empty(
        jwt_payload.get("https://api.openai.com/auth", {}).get("email"),
        jwt_payload.get("email"),
        session_data.get("email"),
        session_data.get("user", {}).get("email") if isinstance(session_data.get("user"), dict) else None,
    )

    auth_ns = jwt_payload.get("https://api.openai.com/auth", {})
    account_id = _first_non_empty(
        auth_ns.get("chatgpt_account_id"),
        jwt_payload.get("chatgpt_account_id"),
        session_data.get("accountId"),
        session_data.get("account_id"),
    )

    user_id = _first_non_empty(
        auth_ns.get("chatgpt_user_id"),
        auth_ns.get("user_id"),
        jwt_payload.get("chatgpt_user_id"),
        jwt_payload.get("user_id"),
        session_data.get("userId"),
        session_data.get("user_id"),
    )

    plan_type = _first_non_empty(
        auth_ns.get("chatgpt_plan_type"),
        jwt_payload.get("chatgpt_plan_type"),
        session_data.get("planType"),
        session_data.get("plan_type"),
        "free",
    )

    name = _first_non_empty(
        session_data.get("user", {}).get("name") if isinstance(session_data.get("user"), dict) else None,
        session_data.get("name"),
        email.split("@")[0] if email else "",
    )

    # 计算过期时间
    exp = jwt_payload.get("exp")
    expires_at = ""
    expires_in = 0
    if exp:
        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"
        expires_in = max(0, int(exp - time.time()))

    # 生成合成 id_token（如果没有）
    synthetic_id_token = False
    if not idToken:
        idToken = _build_synthetic_id_token(account_id, user_id, plan_type, email, now)
        synthetic_id_token = True

    # 来源类型
    source_type = "session_import"

    # ========== Sub2API 格式 ==========
    sub2api_account = _strip_unavailable({
        "name": _first_non_empty(name, email, "ChatGPT Account"),
        "platform": "openai",
        "type": "oauth",
        "concurrency": 10,
        "priority": 1,
        "credentials": {
            "access_token": accessToken,
            "chatgpt_account_id": account_id,
            "chatgpt_user_id": user_id,
            "email": email,
            "expires_at": expires_at,
            "expires_in": expires_in,
            "plan_type": plan_type,
        },
        "extra": {
            "email": email,
            "email_key": _to_email_key(email) if email else "",
            "name": name,
            "source": source_type,
            "last_refresh": exported_at,
        },
    })

    # ========== CPA 格式 ==========
    cpa = _strip_unavailable({
        "type": "codex",
        "account_id": account_id,
        "chatgpt_account_id": account_id,
        "email": email,
        "name": name,
        "plan_type": plan_type,
        "chatgpt_plan_type": plan_type,
        "id_token": idToken,
        "id_token_synthetic": True if synthetic_id_token else None,
        "access_token": accessToken,
        "refresh_token": refreshToken or "",
        "session_token": sessionToken,
        "last_refresh": exported_at,
        "expired": expires_at,
    })

    return {"sub2apiAccount": sub2api_account, "cpa": cpa}


def build_sub2api_document(converted_list: List[Dict]) -> Dict:
    """
    构建 sub2api 完整文档

    对应 index.html 的 buildSub2apiDocument()

    Args:
        converted_list: convert_session() 返回值的列表

    Returns:
        {"exported_at": "...", "proxies": [], "accounts": [...]}
    """
    return {
        "exported_at": _normalize_timestamp(),
        "proxies": [],
        "accounts": [item["sub2apiAccount"] for item in converted_list if item],
    }


def convert_and_save_sub2(session_data: Dict, email: str, output_dir: str) -> Optional[str]:
    """
    转换 session 并保存为 sub2api 格式文件

    Args:
        session_data: 原始 session JSON
        email: 账号邮箱
        output_dir: 输出目录

    Returns:
        保存的文件路径 或 None
    """
    import os

    converted = convert_session(session_data)
    if not converted:
        return None

    doc = build_sub2api_document([converted])

    # 文件名: {email}-sub2.json
    safe_email = re.sub(r"[^\w\-.]", "_", email)
    filepath = os.path.join(output_dir, f"{safe_email}-sub2.json")

    try:
        os.makedirs(output_dir, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        return filepath
    except Exception as e:
        print(f"[ERROR] 保存 sub2api 文件失败: {e}")
        return None


def convert_and_save_combined_sub2(session_data_list: List[Dict], output_dir: str, filename: str = "combined-sub2.json") -> Optional[str]:
    """
    批量转换多个 session 并合并保存为一个 sub2api 文件

    Args:
        session_data_list: session 数据列表
        output_dir: 输出目录
        filename: 输出文件名

    Returns:
        保存的文件路径 或 None
    """
    import os

    converted_list = []
    for sd in session_data_list:
        if sd:
            c = convert_session(sd)
            if c:
                converted_list.append(c)

    if not converted_list:
        return None

    doc = build_sub2api_document(converted_list)
    filepath = os.path.join(output_dir, filename)

    try:
        os.makedirs(output_dir, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        return filepath
    except Exception as e:
        print(f"[ERROR] 保存合并 sub2api 文件失败: {e}")
        return None
