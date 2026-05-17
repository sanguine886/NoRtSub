"""NoRtSub Web UI"""

import json
import os
import queue
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, render_template, request, jsonify, Response
from curl_cffi import requests as cffi_requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    init_auth = None
    generate_payload = None

app = Flask(__name__)
tasks = {}


def _mask(email):
    if "@" not in email:
        return email
    local, domain = email.split("@", 1)
    return f"{local[:2]}***@{domain}"


class TaskLog:
    def __init__(self, task_id):
        self.task_id = task_id
        self.logs = []
        self.subscribers = []
        self.lock = threading.Lock()
        self.finished = False
        self.result = None

    def log(self, level, msg):
        entry = {"time": time.strftime("%H:%M:%S"), "level": level, "msg": msg}
        with self.lock:
            self.logs.append(entry)
            for q in self.subscribers:
                try:
                    q.put_nowait(entry)
                except queue.Full:
                    pass

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def finish(self, result=None):
        self.finished = True
        self.result = result
        with self.lock:
            for q in self.subscribers:
                try:
                    q.put_nowait({"_done": True, "result": result})
                except queue.Full:
                    pass


def _parse_key_input(raw):
    """
    解析输入，支持两种格式：
    1. 完整URL: https://plus2.yhmoai.online/?key=XXXX-XXXX
    2. 纯Key: XXXX-XXXX-XXXX-XXXX
    返回: (key, api_host) 元组列表
    """
    lines = [l.strip() for l in re.split(r"[,;\n]+", raw) if l.strip()]
    result = []
    for line in lines:
        m = re.match(r'https?://(plus\d+\.yhmoai\.online)/.*[?&]key=([A-Z0-9-]+)', line)
        if m:
            result.append((m.group(2), f"https://{m.group(1)}"))
        else:
            # 纯 key，不带 host
            result.append((line, None))
    return result


def _silent(fn, *args, **kwargs):
    """静默执行 auth_core 函数，屏蔽其内部输出"""
    import io
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        return fn(*args, **kwargs)
    finally:
        sys.stdout = old


def _do_login(session, email, proxy, proxies, _log):
    try:
        _log("INFO", "[1/6] 伪造身份中... 我现在是 Chrome 110，别拆穿我")
        did, ua = _silent(init_auth, session=session, email=email, masked_email=_mask(email),
                          proxies=proxies, verify=ssl_verify())

        _log("INFO", "[2/6] 向 OpenAI 自报家门... '你好，我是这个邮箱的主人'")
        ctx = {}
        sentinel = _silent(generate_payload, did=did, flow="authorize_continue", proxy=proxy,
                           user_agent=ua, impersonate="chrome110", ctx=ctx)
        resp = submit_email(session, email, did, proxies, sentinel_token=sentinel)
        if resp.status_code != 200:
            _log("ERROR", f"OpenAI 不买账: {resp.status_code}")
            return None

        continue_url = resp.json().get("continue_url", "")
        if "log-in" not in continue_url and "/email-verification" not in continue_url:
            _log("ERROR", "这个账号不让无密码登录，换个试试")
            return None

        old_code = get_email_code(email) or ""
        _log("INFO", "[3/6] 请求发送验证码... '麻烦给我的邮箱发个数字'")
        otp_ctx = ctx.copy()
        sentinel_otp = _silent(generate_payload, did=did, flow="authorize_continue", proxy=proxy,
                               user_agent=ua, impersonate="chrome110", ctx=otp_ctx)
        if not send_otp(session, did, proxies, sentinel_token=sentinel_otp):
            _log("ERROR", "验证码发不出来，OpenAI 可能累了")
            return None

        _log("INFO", "[4/6] 蹲在邮箱门口等验证码... 希望别等太久")
        time.sleep(5)
        code = wait_for_code(email, timeout_sec=120, interval=5, ignore_code=old_code)
        if not code:
            _log("ERROR", "等了两分钟都没验证码，邮箱可能迷路了")
            return None

        _log("SUCCESS", f"验证码到手: {code}，拿来吧你！")
        _log("INFO", "[5/6] 把验证码甩给 OpenAI... '看，我确实是本人'")
        sentinel_v = _silent(generate_payload, did=did, flow="authorize_continue", proxy=proxy,
                             user_agent=ua, impersonate="chrome110", ctx={})
        code_resp = verify_otp(session, code, did, proxies, sentinel_token=sentinel_v)
        if code_resp.status_code != 200:
            _log("ERROR", f"OpenAI 说验证码不对: {code_resp.status_code}")
            return None

        _log("SUCCESS", "验证通过！OpenAI 被忽悠住了")

        code_url = str(code_resp.json().get("continue_url") or "").strip()
        if code_url.endswith("/about-you"):
            create_account_info(session, did, proxies)
        if code_url and "code=" in code_url:
            _log("INFO", "正在跳转 callback... 跟着面包屑走")
            follow_redirect_chain(session, code_url, proxies)

        _log("INFO", "[6/6] 去 ChatGPT 那边拿 Session 令牌...")
        session_resp = session.get("https://chatgpt.com/api/auth/session",
                                   proxies=proxies, verify=ssl_verify(), timeout=15)
        if session_resp.status_code != 200:
            _log("ERROR", f"Session 拿不到: {session_resp.status_code}")
            return None

        data = session_resp.json()
        user = data.get("user", {})
        _log("SUCCESS", f"搞定！欢迎回来，{user.get('name', '陌生人')}！")
        return data
    except Exception as e:
        _log("ERROR", f"翻车了: {e}")
        return None


def run_batch_task(task_id, key_pairs):
    log = tasks[task_id]

    def _log(level, msg):
        log.log(level, msg)

    try:
        cfg.load_config()

        output_dir = cfg.get("output_dir", "data/sessions")
        proxy = cfg.get("proxy", "")
        proxies = {"http": proxy, "https": proxy} if proxy else None

        if not init_auth or not generate_payload:
            _log("ERROR", "auth_core 未加载")
            log.finish({"success": False, "error": "auth_core 未加载"})
            return

        total = len(key_pairs)
        _log("INFO", f"开干！共 {total} 个 Key 排队等处理")
        results = []
        all_session = []

        max_workers = 10 if total > 10 else total

        def _process_one(idx, key, api_host):
            """处理单个 Key（线程安全）"""
            if api_host:
                import utils.exchange_api as eapi
                eapi._working_host = api_host

            info = get_account_info(key)
            if not info:
                return idx, {"key": key, "success": False, "error": "获取账号信息失败"}, None

            email = info.get("accountEmail", "")
            if not email:
                return idx, {"key": key, "success": False, "error": "未获取到邮箱"}, None

            session = cffi_requests.Session(proxies=proxies, impersonate="chrome110")
            session.headers["Connection"] = "close"
            session.timeout = 30

            try:
                session_data = _do_login(session, email, proxy, proxies, _log)
            finally:
                session.close()

            if not session_data:
                return idx, {"key": key, "success": False, "email": email, "error": "登录失败"}, None

            filepath = save_session_to_file(email, session_data, output_dir)
            sub2_path = convert_and_save_sub2(session_data, email, output_dir)
            user = session_data.get("user", {})
            result = {
                "key": key, "success": True, "email": email,
                "user_name": user.get("name", ""),
                "session_file": filepath, "sub2_file": sub2_path,
                "expires": session_data.get("expires", ""),
            }
            return idx, result, session_data

        if max_workers > 1:
            _log("INFO", f"并发模式启动: {max_workers} 线程同时处理")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_process_one, idx, key, api_host): idx
                    for idx, (key, api_host) in enumerate(key_pairs)
                }
                for future in as_completed(futures):
                    idx, result, session_data = future.result()
                    results.append((idx, result))
                    if result["success"]:
                        all_session.append(session_data)
                        _log("SUCCESS", f"[{idx+1}/{total}] {_mask(result['email'])} 到手！{result.get('user_name', '')}")
                    else:
                        _log("ERROR", f"[{idx+1}/{total}] {result.get('error', '未知错误')}")

            results.sort(key=lambda x: x[0])
            results = [r for _, r in results]
        else:
            for idx, (key, api_host) in enumerate(key_pairs, 1):
                _log("INFO", f"━━━ [{idx}/{total}] 拿出 Key: {key[:8]}... ━━━")

                if api_host:
                    import utils.exchange_api as eapi
                    eapi._working_host = api_host
                    _log("INFO", f"API 地址: {api_host}")

                info = get_account_info(key)
                if not info:
                    _log("ERROR", f"[{idx}/{total}] 这个 Key 不认识，查无此人")
                    results.append({"key": key, "success": False, "error": "获取账号信息失败"})
                    continue

                email = info.get("accountEmail", "")
                if not email:
                    _log("ERROR", f"[{idx}/{total}] Key 有问题是空号，没邮箱")
                    results.append({"key": key, "success": False, "error": "未获取到邮箱"})
                    continue

                session = cffi_requests.Session(proxies=proxies, impersonate="chrome110")
                session.headers["Connection"] = "close"
                session.timeout = 30

                try:
                    session_data = _do_login(session, email, proxy, proxies, _log)
                finally:
                    session.close()

                if not session_data:
                    results.append({"key": key, "success": False, "email": email, "error": "登录失败"})
                    _log("ERROR", f"[{idx}/{total}] {_mask(email)} 翻车了，跳过")
                    continue

                filepath = save_session_to_file(email, session_data, output_dir)
                sub2_path = convert_and_save_sub2(session_data, email, output_dir)
                user = session_data.get("user", {})
                results.append({
                    "key": key, "success": True, "email": email,
                    "user_name": user.get("name", ""),
                    "session_file": filepath, "sub2_file": sub2_path,
                    "expires": session_data.get("expires", ""),
                })
                all_session.append(session_data)
                _log("SUCCESS", f"[{idx}/{total}] {_mask(email)} 到手！{user.get('name', '')} 的地盘归我了")

                if idx < total:
                    _log("INFO", "喝口水，3 秒后继续...")
                    time.sleep(3)

        combined = None
        if len(all_session) > 1:
            _log("INFO", f"把 {len(all_session)} 个账号打包合并...")
            combined = convert_and_save_combined_sub2(all_session, output_dir)
            if combined:
                _log("SUCCESS", f"合并完成: {os.path.basename(combined)}")

        ok = sum(1 for r in results if r["success"])
        if ok == total:
            _log("SUCCESS", f"全部搞定！{ok}/{total}，一个都没跑掉")
        elif ok > 0:
            _log("SUCCESS", f"部分搞定: {ok}/{total}，有 {total - ok} 个不配合")
        else:
            _log("ERROR", f"全军覆没: 0/{total}，今天运气不太好")

        log.finish({
            "success": ok > 0, "total": total,
            "success_count": ok, "fail_count": total - ok,
            "results": results, "combined_file": combined,
        })
    except Exception as e:
        _log("ERROR", str(e))
        log.finish({"success": False, "error": str(e)})


@app.route("/")
def index():
    return render_template("index.html", default_key=cfg.get("key", ""))


@app.route("/api/start", methods=["POST"])
def start_task():
    data = request.json or {}
    keys_raw = data.get("keys", "").strip()

    if not keys_raw:
        return jsonify({"error": "卡密不能为空"}), 400

    key_pairs = _parse_key_input(keys_raw)
    if not key_pairs:
        return jsonify({"error": "未找到有效卡密"}), 400

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = TaskLog(task_id)
    threading.Thread(target=run_batch_task, args=(task_id, key_pairs), daemon=True).start()
    return jsonify({"task_id": task_id, "count": len(key_pairs)})


@app.route("/api/logs/<task_id>")
def stream_logs(task_id):
    log = tasks.get(task_id)
    if not log:
        return jsonify({"error": "任务不存在"}), 404

    def generate():
        q = log.subscribe()
        try:
            for entry in log.logs:
                yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
            if log.finished:
                yield f"data: {json.dumps({'_done': True, 'result': log.result}, ensure_ascii=False)}\n\n"
                return
            while True:
                try:
                    entry = q.get(timeout=30)
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                    if entry.get("_done"):
                        return
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            log.unsubscribe(q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/download/<path:filename>")
def download_file(filename):
    from flask import send_from_directory
    d = cfg.get("output_dir", "data/sessions")
    abs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), d)
    return send_from_directory(abs_dir, filename, as_attachment=True)


if __name__ == "__main__":
    cfg.load_config()
    print(f"[{cfg.ts()}] Web UI: http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
