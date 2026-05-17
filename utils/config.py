import os
import yaml
from datetime import datetime, timezone, timedelta

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")

_c = {}


def ts() -> str:
    tz_utc_8 = timezone(timedelta(hours=8))
    return datetime.now(tz_utc_8).strftime("%H:%M:%S")


def load_config():
    global _c
    if not os.path.exists(CONFIG_PATH):
        print(f"[{ts()}] [WARNING] config.yaml 不存在，使用默认配置")
        _c = {}
        return True
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        _c = yaml.safe_load(f) or {}
    return True


def get(key, default=None):
    return _c.get(key, default)


@property
def proxy():
    return _c.get("proxy", "")


def proxies():
    p = _c.get("proxy", "")
    if p:
        return {"http": p, "https": p}
    return None
