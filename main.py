"""NoRtSub - 邮箱验证码登录 ChatGPT 并获取 Session"""

import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests

from utils import config as cfg
from utils.exchange_api import get_account_info, get_email_code, wait_for_code
from utils.auth import submit_email, send_otp, verify_otp, create_account_info
from utils.http_utils import ssl_verify, follow_redirect_chain
from utils.session import save_session_to_file
from utils.converter import convert_and_save_sub2, convert_and_save_combined_sub2

try:
    import io as _io
    _stdout, sys.stdout = sys.stdout, _io.StringIO()
    from utils.auth_core import init_auth, generate_payload
    sys.stdout = _stdout
except ImportError:
    print("[ERROR] auth_core 模块加载失败")
    sys.exit(1)


def _mask(email):
    if "@" not in email:
        return email
    local, domain = email.split("@", 1)
    return f"{local[:2]}***@{domain}"


def _silent(fn, *args, **kwargs):
    import io
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        return fn(*args, **kwargs)
    finally:
        sys.stdout = old


def do_login(email, proxy):
    proxies = {"http": proxy, "https": proxy} if proxy else None
    s = requests.Session(proxies=proxies, impersonate="chrome110")
    s.headers["Connection"] = "close"
    s.timeout = 30

    try:
        did, ua = _silent(init_auth, session=s, email=email, masked_email=_mask(email),
                          proxies=proxies, verify=ssl_verify())

        ctx = {}
        sentinel = _silent(generate_payload, did=did, flow="authorize_continue", proxy=proxy,
                           user_agent=ua, impersonate="chrome110", ctx=ctx)
        resp = submit_email(s, email, did, proxies, sentinel_token=sentinel)
        if resp.status_code != 200:
            print(f"[{cfg.ts()}] 提交邮箱失败: {resp.status_code}")
            return None

        continue_url = resp.json().get("continue_url", "")
        if "log-in" not in continue_url and "/email-verification" not in continue_url:
            print(f"[{cfg.ts()}] 不支持无密码登录")
            return None

        old_code = get_email_code(email) or ""
        otp_ctx = ctx.copy()
        sentinel_otp = _silent(generate_payload, did=did, flow="authorize_continue", proxy=proxy,
                               user_agent=ua, impersonate="chrome110", ctx=otp_ctx)
        if not send_otp(s, did, proxies, sentinel_token=sentinel_otp):
            print(f"[{cfg.ts()}] 发送 OTP 失败")
            return None

        time.sleep(5)
        code = wait_for_code(email, timeout_sec=120, interval=5, ignore_code=old_code)
        if not code:
            print(f"[{cfg.ts()}] 获取验证码超时")
            return None

        print(f"[{cfg.ts()}] 验证码: {code}")
        sentinel_v = _silent(generate_payload, did=did, flow="authorize_continue", proxy=proxy,
                             user_agent=ua, impersonate="chrome110", ctx={})
        code_resp = verify_otp(s, code, did, proxies, sentinel_token=sentinel_v)
        if code_resp.status_code != 200:
            print(f"[{cfg.ts()}] 验证失败: {code_resp.status_code}")
            return None

        code_url = str(code_resp.json().get("continue_url") or "").strip()
        if code_url.endswith("/about-you"):
            create_account_info(s, did, proxies)
        if code_url and "code=" in code_url:
            follow_redirect_chain(s, code_url, proxies)

        session_resp = s.get("https://chatgpt.com/api/auth/session",
                             proxies=proxies, verify=ssl_verify(), timeout=15)
        if session_resp.status_code != 200:
            print(f"[{cfg.ts()}] Session 获取失败: {session_resp.status_code}")
            return None

        return session_resp.json()
    except Exception as e:
        print(f"[{cfg.ts()}] 登录异常: {e}")
        return None
    finally:
        s.close()


def _parse_key_input(raw):
    """解析输入，支持完整URL和纯Key两种格式，返回 (key, api_host) 元组列表"""
    lines = [l.strip() for l in re.split(r"[,;\n]+", raw) if l.strip()]
    result = []
    for line in lines:
        m = re.match(r'https?://(plus\d+\.yhmoai\.online)/.*[?&]key=([A-Z0-9-]+)', line)
        if m:
            result.append((m.group(2), f"https://{m.group(1)}"))
        else:
            result.append((line, None))
    return result


def process_key(key, proxy, output_dir, api_host=None):
    if api_host:
        import utils.exchange_api as eapi
        eapi._working_host = api_host
    info = get_account_info(key)
    if not info:
        return {"key": key, "success": False, "error": "获取账号信息失败"}
    email = info.get("accountEmail", "")
    if not email:
        return {"key": key, "success": False, "error": "未获取到邮箱"}

    print(f"[{cfg.ts()}] {_mask(email)}")
    session_data = do_login(email, proxy)
    if not session_data:
        return {"key": key, "success": False, "email": email, "error": "登录失败"}

    filepath = save_session_to_file(email, session_data, output_dir)
    sub2_path = convert_and_save_sub2(session_data, email, output_dir)
    user = session_data.get("user", {})
    return {
        "key": key, "success": True, "email": email,
        "user_name": user.get("name", ""), "session_file": filepath,
        "sub2_file": sub2_path, "expires": session_data.get("expires", ""),
        "session_data": session_data,
    }


def main():
    cfg.load_config()
    proxy = cfg.get("proxy", "")
    output_dir = cfg.get("output_dir", "data/sessions")

    raw_lines = []
    k = cfg.get("keys", [])
    if isinstance(k, list) and k:
        raw_lines.extend(str(x).strip() for x in k if str(x).strip())
    elif cfg.get("key", ""):
        raw_lines.append(cfg.get("key", ""))

    for arg in sys.argv[1:]:
        if arg.startswith("--keys-file="):
            fp = arg.split("=", 1)[1]
            if os.path.exists(fp):
                with open(fp, "r", encoding="utf-8") as f:
                    raw_lines.extend(l.strip() for l in f if l.strip() and not l.startswith("#"))

    if not raw_lines:
        raw = input("Key/URL: ").strip()
        if not raw:
            return
        raw_lines = raw.splitlines()

    key_pairs = _parse_key_input("\n".join(raw_lines))
    total = len(key_pairs)
    results = []
    all_session = []

    max_workers = 10 if total > 10 else total

    if max_workers > 1:
        print(f"[{cfg.ts()}] 并发模式: {max_workers} 线程处理 {total} 个 Key")
        sem = threading.Semaphore(max_workers)
        done_count = [0]
        count_lock = threading.Lock()

        def _worker(key, api_host, idx):
            sem.acquire()
            try:
                r = process_key(key, proxy, output_dir, api_host=api_host)
                with count_lock:
                    done_count[0] += 1
                    cur = done_count[0]
                tag = "OK" if r["success"] else "FAIL"
                info = r.get("email", r.get("error", ""))
                print(f"[{cfg.ts()}] [{cur}/{total}] {tag} - {info}")
                return idx, r
            finally:
                sem.release()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_worker, key, api_host, i): i
                for i, (key, api_host) in enumerate(key_pairs)
            }
            for future in as_completed(futures):
                idx, result = future.result()
                results.append((idx, result))

        results.sort(key=lambda x: x[0])
        results = [r for _, r in results]
    else:
        for idx, (key, api_host) in enumerate(key_pairs, 1):
            print(f"[{cfg.ts()}] [{idx}/{total}] {key[:8]}...")
            result = process_key(key, proxy, output_dir, api_host=api_host)
            results.append(result)
            if result["success"]:
                print(f"[{cfg.ts()}] [{idx}/{total}] OK - {result['email']}")
            else:
                print(f"[{cfg.ts()}] [{idx}/{total}] FAIL - {result['error']}")

    for r in results:
        if r["success"]:
            all_session.append(r.pop("session_data"))

    if len(all_session) > 1:
        p = convert_and_save_combined_sub2(all_session, output_dir)
        if p:
            print(f"[{cfg.ts()}] 合并导出: {p}")

    ok = sum(1 for r in results if r["success"])
    print(f"\n完成: {ok}/{total}")


if __name__ == "__main__":
    main()
