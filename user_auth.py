"""
EcoWise 宿舍助理 - 用户认证模块
================================
提供用户注册、登录、密码验证、验证码功能。
密码使用 SHA256 + 随机 salt 加密存储。
验证码使用 SQLite 数据库存储（而非内存），确保多进程/重启后不丢失。
"""
import os
import sqlite3
import hashlib
import secrets
import re
import time
from datetime import datetime

DB_PATH = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.expanduser("~"))), "Ecowise", "energy_log.db")

CODE_EXPIRE_SECONDS = 300  # 验证码5分钟有效


def _get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    # WAL 模式：允许并发读写，解决 Render 文件系统锁问题
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
    except sqlite3.OperationalError as e:
        # 数据库可能被锁定，尝试关闭后重新打开
        print(f"[user_auth] WAL PRAGMA failed: {e}, trying to reset...")
        conn.close()
        # 尝试删除旧数据库并重建
        import os as _os
        db_path = DB_PATH
        for suffix in ['', '-journal', '-wal', '-shm']:
            try:
                _os.remove(db_path + suffix)
            except:
                pass
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        print(f"[user_auth] Database recreated with WAL mode")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            nickname      TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL
        )
    """)
    # 验证码存储表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS verify_codes (
            phone       TEXT PRIMARY KEY,
            code        TEXT NOT NULL,
            expire_time REAL NOT NULL
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
    """生成4位验证码，存入SQLite数据库，5分钟有效。返回验证码字符串。"""
    code = str(secrets.randbelow(9000) + 1000)
    expire = time.time() + CODE_EXPIRE_SECONDS
    
    print(f"[user_auth] generate_code: phone={phone}, code={code}, expire={expire}, DB_PATH={DB_PATH}")
    
    try:
        conn = _get_db()
    except Exception as e:
        print(f"[user_auth] generate_code _get_db() FAILED: {e}")
        import traceback
        traceback.print_exc()
        raise
    
    try:
        # 清理过期验证码
        conn.execute("DELETE FROM verify_codes WHERE expire_time < ?", (time.time(),))
        # 插入或更新验证码
        conn.execute(
            "INSERT OR REPLACE INTO verify_codes (phone, code, expire_time) VALUES (?, ?, ?)",
            (phone, code, expire)
        )
        conn.commit()
        print(f"[user_auth] generate_code: SUCCESS - code stored in DB for phone={phone}")
    except Exception as e:
        print(f"[user_auth] generate_code DB error: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        conn.close()
    
    return code


def verify_code(phone, code):
    """校验验证码是否正确且未过期"""
    print(f"[user_auth] verify_code: phone={phone}, input_code='{code}', DB_PATH={DB_PATH}")
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT code, expire_time FROM verify_codes WHERE phone = ?",
            (phone,)
        ).fetchone()
        
        if not row:
            print(f"[user_auth] verify_code: FAIL - no code found for phone={phone}")
            return False
        
        stored_code, expire = row
        now = time.time()
        print(f"[user_auth] verify_code: stored_code='{stored_code}', input_code='{code}', expire={expire}, now={now}, expired={now > expire}")
        
        if now > expire:
            print(f"[user_auth] verify_code: FAIL - code expired (expired {now - expire:.1f}s ago)")
            conn.execute("DELETE FROM verify_codes WHERE phone = ?", (phone,))
            conn.commit()
            return False
        
        if stored_code != code:
            print(f"[user_auth] verify_code: FAIL - code mismatch (stored='{stored_code}' vs input='{code}')")
            return False
        
        # 验证成功后删除，防止重复使用
        conn.execute("DELETE FROM verify_codes WHERE phone = ?", (phone,))
        conn.commit()
        print(f"[user_auth] verify_code: SUCCESS for phone={phone}")
        return True
    finally:
        conn.close()


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
