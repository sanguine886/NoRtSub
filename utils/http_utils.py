import os
import time
from typing import Any, Dict, Optional

from curl_cffi import requests


def ssl_verify() -> bool:
    flag = os.getenv("OPENAI_SSL_VERIFY", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def post_with_retry(
    session: requests.Session,
    url: str,
    *,
    headers: Dict[str, Any],
    data: Any = None,
    json_body: Any = None,
    proxies: Any = None,
    timeout: int = 30,
    retries: int = 2,
    allow_redirects: bool = True,
) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            if json_body is not None:
                return session.post(
                    url, headers=headers, json=json_body,
                    proxies=proxies, verify=ssl_verify(),
                    timeout=timeout, allow_redirects=allow_redirects,
                )
            return session.post(
                url, headers=headers, data=data,
                proxies=proxies, verify=ssl_verify(),
                timeout=timeout, allow_redirects=allow_redirects,
            )
        except Exception as e:
            last_error = e
            if attempt >= retries:
                break
            time.sleep(2 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError("Request failed without exception")


def oai_headers(did: str, extra: dict = None) -> dict:
    h = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/110.0.0.0 Safari/537.36"
        ),
        "sec-ch-ua": '"Google Chrome";v="110", "Chromium";v="110", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "oai-device-id": did,
    }
    if extra:
        h.update(extra)
    return h


def follow_redirect_chain(
    session: requests.Session,
    start_url: str,
    proxies: Any = None,
    max_redirects: int = 12,
):
    import urllib.parse
    current_url = start_url
    response = None
    for _ in range(max_redirects):
        try:
            response = session.get(
                current_url,
                allow_redirects=False,
                proxies=proxies,
                verify=ssl_verify(),
                timeout=15,
            )
            if response.status_code not in (301, 302, 303, 307, 308):
                return response, current_url
            loc = response.headers.get("Location", "")
            if not loc:
                return response, current_url
            current_url = urllib.parse.urljoin(current_url, loc)
            if "code=" in current_url and "state=" in current_url:
                return None, current_url
        except Exception:
            return None, current_url
    return response, current_url
