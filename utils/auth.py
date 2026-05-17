"""OpenAI 认证流程"""

import json
import random
import time
from datetime import datetime
from typing import Any

from curl_cffi import requests

from .http_utils import ssl_verify, post_with_retry, oai_headers, follow_redirect_chain


def submit_email(session, email, did, proxies=None, sentinel_token=None):
    headers = oai_headers(did, {
        "Referer": "https://auth.openai.com/create-account",
        "content-type": "application/json",
    })
    if sentinel_token:
        headers["openai-sentinel-token"] = sentinel_token
    return post_with_retry(
        session,
        "https://auth.openai.com/api/accounts/authorize/continue",
        headers=headers,
        json_body={"username": {"value": email, "kind": "email"}, "screen_hint": "login_or_signup"},
        proxies=proxies,
    )


def send_otp(session, did, proxies=None, sentinel_token=None):
    headers = oai_headers(did, {
        "Referer": "https://auth.openai.com/create-account/password",
        "content-type": "application/json",
    })
    if sentinel_token:
        headers["openai-sentinel-token"] = sentinel_token
    try:
        resp = post_with_retry(
            session,
            "https://auth.openai.com/api/accounts/email-otp/send",
            headers=headers, json_body={}, proxies=proxies, timeout=30,
        )
        return resp.status_code == 200
    except Exception:
        return False


def verify_otp(session, code, did, proxies=None, sentinel_token=None):
    headers = oai_headers(did, {
        "Referer": "https://auth.openai.com/email-verification",
        "content-type": "application/json",
    })
    if sentinel_token:
        headers["openai-sentinel-token"] = sentinel_token
    return post_with_retry(
        session,
        "https://auth.openai.com/api/accounts/email-otp/validate",
        headers=headers,
        json_body={"code": code},
        proxies=proxies,
    )


def create_account_info(session, did, proxies=None):
    first = random.choice(["James", "John", "Robert", "Michael", "William", "David", "Emma", "Olivia", "Ava", "Sophia"])
    last = random.choice(["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis"])
    year = random.randint(datetime.now().year - 45, datetime.now().year - 18)
    headers = oai_headers(did, {
        "Referer": "https://auth.openai.com/about-you",
        "content-type": "application/json",
    })
    return post_with_retry(
        session,
        "https://auth.openai.com/api/accounts/create_account",
        headers=headers,
        json_body={"name": f"{first} {last}", "birthdate": f"{year}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"},
        proxies=proxies,
    )
