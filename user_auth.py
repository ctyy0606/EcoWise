"""
EcoWise 宿舍助理 - 用户认证模块
================================
提供用户注册、登录、密码验证、验证码功能。
密码使用 SHA256 + 随机 salt 加密存储。
"""
import os
import sqlite3
import hashlib
import secrets
import re
import time
from datetime import datetime

DB_PATH = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.expanduser("~"))), "Ecowise", "energy_log.db")

# 验证码临时存储: {phone: (code, expire_timestamp)}
_code_store = {}
CODE_EXPIRE_SECONDS = 300  # 验证码5分钟有效


def _get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            nickname      TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL
        )
    """)
    # 兼容旧库：如果 nickname 列不存在则添加
    try:
        conn.execute("SELECT nickname FROM users LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT NOT NULL DEFAULT ''")
    conn.commit()
    return conn


def _hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(8)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{hashed}"


def _verify_password(password, stored):
    try:
        salt, hashed = stored.split(':')
        return _hash_password(password, salt).split(':')[1] == hashed
    except Exception:
        return False


def _validate_phone(phone):
    """验证手机号格式：11位数字，1开头"""
    return bool(re.match(r'^1\d{10}$', phone))


def _validate_password(password):
    """验证密码强度：>=8位，包含字母和数字"""
    if len(password) < 8:
        return False, "密码至少8位"
    if not re.search(r'[a-zA-Z]', password):
        return False, "密码必须包含字母"
    if not re.search(r'\d', password):
        return False, "密码必须包含数字"
    return True, ""


def generate_code(phone):
    """生成4位验证码，存入内存，5分钟有效。返回验证码字符串。"""
    code = str(secrets.randbelow(9000) + 1000)
    _code_store[phone] = (code, time.time() + CODE_EXPIRE_SECONDS)
    # 清理过期验证码
    now = time.time()
    expired = [k for k, v in _code_store.items() if v[1] < now]
    for k in expired:
        del _code_store[k]
    return code


def verify_code(phone, code):
    """校验验证码是否正确且未过期"""
    record = _code_store.get(phone)
    if not record:
        return False
    stored_code, expire = record
    if time.time() > expire:
        del _code_store[phone]
        return False
    if stored_code != code:
        return False
    # 验证成功后删除，防止重复使用
    del _code_store[phone]
    return True


def register(phone, nickname, password, code):
    """
    注册新用户，返回 (success, message)
    phone: 手机号（登录账号）
    nickname: 用户名（显示名，用于"xxx的设备"）
    password: 密码
    code: 短信验证码
    """
    phone = phone.strip()
    nickname = nickname.strip()
    if not phone or not nickname or not password:
        return False, "手机号、用户名和密码不能为空"
    if not _validate_phone(phone):
        return False, "请输入有效的手机号（11位）"
    if not verify_code(phone, code):
        return False, "验证码错误或已过期"
    valid, msg = _validate_password(password)
    if not valid:
        return False, msg

    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, nickname, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (phone, nickname, _hash_password(password), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        return True, "注册成功"
    except sqlite3.IntegrityError:
        return False, "该手机号已注册"
    finally:
        conn.close()


def login(phone, password):
    """
    验证登录，返回 (success, message, nickname)
    nickname 为用户名（显示名），设备 owner 按此过滤。
    """
    phone = phone.strip()
    if not phone or not password:
        return False, "手机号和密码不能为空", None

    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT password_hash, nickname FROM users WHERE username=?",
            (phone,),
        ).fetchone()
        if not row:
            return False, "该手机号未注册，请先注册", None
        if not _verify_password(password, row[0]):
            return False, "密码错误", None
        return True, "登录成功", row[1] or phone
    finally:
        conn.close()


def get_nickname(phone):
    """根据手机号获取用户名（显示名）"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT nickname FROM users WHERE username=?", (phone,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def user_exists(phone):
    """检查手机号是否已注册"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT 1 FROM users WHERE username=?", (phone,)).fetchone()
        return row is not None
    finally:
        conn.close()
