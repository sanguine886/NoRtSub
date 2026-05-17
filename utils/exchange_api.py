"""
Exchange API 客户端
获取账号信息和验证码，支持多域名自动回退
"""

import time
import urllib.parse
from typing import Optional, Tuple

import requests as _requests

from .config import ts, get as cfg_get

# 候选域名列表，按优先级排列
API_HOSTS = [
    "https://plus5.yhmoai.online",
    "https://plus3.yhmoai.online",
    "https://plus2.yhmoai.online",
    "https://plus.yhmoai.online",
]

# 已验证可用的域名缓存
_working_host: Optional[str] = None


def _get_base_url() -> str:
    """获取配置的 API 地址，未配置则使用默认首选"""
    return cfg_get("exchange_api_url", "")


def _try_hosts(path: str, timeout: int = 10) -> Optional[_requests.Response]:
    """
    依次尝试候选域名，返回第一个成功的响应
    """
    global _working_host

    # 优先使用已验证的域名
    hosts = []
    if _working_host:
        hosts.append(_working_host)
    configured = _get_base_url()
    if configured and configured not in hosts:
        hosts.append(configured)
    hosts.extend(h for h in API_HOSTS if h not in hosts)

    for host in hosts:
        url = f"{host}{path}"
        try:
            resp = _requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                _working_host = host
                return resp
            # 404 说明域名不对，继续尝试下一个
            if resp.status_code == 404:
                continue
            # 其他错误也继续
            continue
        except Exception:
            continue

    return None


def get_account_info(key: str, proxy: str = "") -> Optional[dict]:
    """
    通过 key 获取账号信息（自动回退多个域名）
    返回: {"accountEmail": "...", "emailCode": "...", ...}
    """
    path = f"/api/exchange/query?keyword={urllib.parse.quote(key)}"
    resp = _try_hosts(path)
    if resp:
        data = resp.json()
        if data.get("success") and data.get("data"):
            return data["data"]
        print(f"[{ts()}] [ERROR] API 返回失败: {data.get('message', '未知错误')}")
        return None
    print(f"[{ts()}] [ERROR] 所有 API 地址均不可用")
    return None


def get_email_code(email: str, proxy: str = "") -> Optional[str]:
    """
    通过邮箱获取最新验证码
    返回: 验证码字符串 或 None
    """
    path = f"/api/exchange/code?email={urllib.parse.quote(email)}"
    resp = _try_hosts(path)
    if resp:
        data = resp.json()
        if data.get("success") and data.get("data"):
            code = data["data"].get("emailCode", "")
            if code:
                return code
    return None


def wait_for_code(email: str, timeout_sec: int = 120, interval: int = 5, ignore_code: str = "", proxy: str = "") -> Optional[str]:
    """
    轮询等待新验证码
    - ignore_code: 忽略的旧验证码（如果返回的验证码与 ignore_code 相同则继续等待）
    """
    print(f"[{ts()}] [INFO] 等待验证码 ({email})...")
    start = time.time()
    while time.time() - start < timeout_sec:
        code = get_email_code(email, proxy=proxy)
        if code and code != ignore_code:
            print(f"[{ts()}] [SUCCESS] 获取到验证码: {code}")
            return code
        time.sleep(interval)
    print(f"[{ts()}] [ERROR] 等待验证码超时 ({timeout_sec}s)")
    return None
