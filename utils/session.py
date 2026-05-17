import json
import os
import re
from typing import Any, Optional

from curl_cffi import requests

from .http_utils import ssl_verify
from .config import ts


def fetch_chatgpt_session(access_token: str, proxies: Any = None) -> Optional[dict]:
    """
    使用 access_token 获取 ChatGPT session
    访问 https://chatgpt.com/api/auth/session 获取 session 内容
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/110.0.0.0 Safari/537.36"
        ),
    }
    try:
        resp = requests.get(
            "https://chatgpt.com/api/auth/session",
            headers=headers,
            proxies=proxies,
            verify=ssl_verify(),
            timeout=30,
            impersonate="chrome110",
        )
        if resp.status_code == 200:
            return resp.json()
        print(f"[{ts()}] [ERROR] Session 请求失败: HTTP {resp.status_code}")
        return None
    except Exception as e:
        print(f"[{ts()}] [ERROR] Session 请求异常: {e}")
        return None


def save_session_to_file(
    email: str, session_data: dict, output_dir: str = "data/sessions"
) -> Optional[str]:
    """
    将 session 数据保存到以账号命名的文件
    返回保存的文件路径
    """
    os.makedirs(output_dir, exist_ok=True)
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', email)
    filepath = os.path.join(output_dir, f"{safe_name}.json")
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
        return filepath
    except Exception as e:
        print(f"[{ts()}] [ERROR] Session 保存失败: {e}")
        return None
