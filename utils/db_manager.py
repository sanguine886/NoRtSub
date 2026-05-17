"""
db_manager 最小 stub —— 仅供 auth_core 许可证检查使用
"""

import json
import os
import sqlite3
import threading

os.makedirs("data", exist_ok=True)
DB_PATH = "data/data.db"
_write_lock = threading.RLock()


class get_db_conn:
    def __init__(self, as_dict=False, is_write=False):
        self.as_dict = as_dict
        self.is_write = is_write

    def __enter__(self):
        if self.is_write:
            _write_lock.acquire()
        self.conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level="IMMEDIATE")
        if self.as_dict:
            self.conn.row_factory = sqlite3.Row
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.conn.close()
        if self.is_write:
            _write_lock.release()


def get_cursor(conn, as_dict=False):
    return conn.cursor()


def execute_sql(cursor, sql, params=()):
    return cursor.execute(sql, params)


def init_db():
    with get_db_conn(is_write=True) as conn:
        c = get_cursor(conn)
        execute_sql(c, '''
            CREATE TABLE IF NOT EXISTS system_kv (
                `key` TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
    print("[DB] 数据库初始化完成 (stub)")


def set_sys_kv(key, value):
    try:
        val_str = json.dumps(value, ensure_ascii=False)
        with get_db_conn(is_write=True) as conn:
            c = get_cursor(conn)
            execute_sql(c, "INSERT OR REPLACE INTO system_kv (`key`, value) VALUES (?, ?)", (key, val_str))
    except Exception:
        pass


def get_sys_kv(key, default=None):
    try:
        with get_db_conn() as conn:
            c = get_cursor(conn)
            execute_sql(c, "SELECT value FROM system_kv WHERE `key` = ?", (key,))
            row = c.fetchone()
            if row and row[0]:
                return json.loads(row[0])
    except Exception:
        pass
    return default


def check_account_exists(email):
    return False


def save_account_to_db(email, password, token_json_str):
    return False
