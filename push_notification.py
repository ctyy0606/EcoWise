"""
EcoWise 宿舍助理 - 浏览器推送通知模块
======================================
支持 Web Push / Service Worker 推送订阅管理
"""
import os
import sqlite3
import json
import base64
from datetime import datetime
from urllib.parse import urlparse

# pywebpush 用于发送推送
import logging
_logger = logging.getLogger(__name__)

try:
    from pywebpush import webpush, WebPushException
    _logger.info("pywebpush 导入成功，推送通知功能就绪")
except ImportError as e:
    _logger.warning("pywebpush 未安装，推送通知功能不可用。请执行: pip install pywebpush")
    _logger.debug("ImportError 详情: %s", e)
    webpush = None
    WebPushException = Exception

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

DB_PATH = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.expanduser("~"))), "Ecowise", "energy_log.db")

# VAPID keys 文件路径（使用 TEMP 目录，避免 Render 上权限问题）
_TEMP_DIR = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.expanduser("~"))), "Ecowise")
PUBLIC_KEY_FILE = os.path.join(_TEMP_DIR, "vapid_public_key.txt")
PRIVATE_KEY_FILE = os.path.join(_TEMP_DIR, "vapid_private_key.txt")


def _get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_phone  TEXT NOT NULL,
            endpoint    TEXT NOT NULL UNIQUE,
            p256dh      TEXT NOT NULL,
            auth        TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_vapid_keys():
    """生成 VAPID 密钥对并保存到文件"""
    os.makedirs(os.path.dirname(PUBLIC_KEY_FILE), exist_ok=True)
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()

    private_bytes = private_key.private_numbers().private_value.to_bytes(32, "big")
    public_bytes = public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )

    private_b64 = base64.urlsafe_b64encode(private_bytes).rstrip(b"=").decode()
    public_b64 = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode()

    with open(PUBLIC_KEY_FILE, "w") as f:
        f.write(public_b64)
    with open(PRIVATE_KEY_FILE, "w") as f:
        f.write(private_b64)

    return public_b64, private_b64


def get_vapid_public_key():
    """获取 VAPID 公钥"""
    if os.path.exists(PUBLIC_KEY_FILE):
        with open(PUBLIC_KEY_FILE, "r") as f:
            return f.read().strip()
    pub, _ = _generate_vapid_keys()
    return pub


def get_vapid_private_key():
    """获取 VAPID 私钥"""
    if os.path.exists(PRIVATE_KEY_FILE):
        with open(PRIVATE_KEY_FILE, "r") as f:
            return f.read().strip()
    _, priv = _generate_vapid_keys()
    return priv


def save_subscription(user_phone, subscription):
    """保存用户的推送订阅信息"""
    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys", {})
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not endpoint or not p256dh or not auth:
        return False

    conn = _get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO push_subscriptions (user_phone, endpoint, p256dh, auth, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_phone, endpoint, p256dh, auth, _now()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_subscription(endpoint):
    """删除指定订阅"""
    conn = _get_db()
    try:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        conn.commit()
        return True
    finally:
        conn.close()


def get_subscriptions_by_user(user_phone):
    """获取指定用户的所有订阅"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_phone = ?",
            (user_phone,),
        ).fetchall()
        return [
            {"endpoint": r[0], "keys": {"p256dh": r[1], "auth": r[2]}}
            for r in rows
        ]
    finally:
        conn.close()


def get_all_subscriptions():
    """获取所有订阅（用于广播）"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT user_phone, endpoint, p256dh, auth FROM push_subscriptions"
        ).fetchall()
        return [
            {"user_phone": r[0], "endpoint": r[1], "keys": {"p256dh": r[2], "auth": r[3]}}
            for r in rows
        ]
    finally:
        conn.close()


def _get_claims(endpoint):
    """根据 endpoint 生成 VAPID claims"""
    parsed = urlparse(endpoint)
    return {
        "sub": "mailto:ecowise@example.com",
    }


def send_push_to_subscription(subscription, title, body, icon=None, badge=None, tag=None, url=None, require_interaction=False):
    """向单个订阅发送推送"""
    if webpush is None:
        return False, (
            "推送通知不可用：pywebpush 库未正确安装。"
            "请在服务器端执行 pip install pywebpush 安装该依赖，"
            "并确保已配置 VAPID 密钥。"
        )

    try:
        payload = {
            "title": title,
            "body": body,
            "icon": icon or "/static/icon-192.png",
            "badge": badge or "/static/icon-96.png",
            "tag": tag or "ecowise-default",
            "requireInteraction": require_interaction,
            "data": {"url": url or "/"},
        }

        webpush(
            subscription_info=subscription,
            data=json.dumps(payload),
            vapid_private_key=get_vapid_private_key(),
            vapid_claims=_get_claims(subscription.get("endpoint", "")),
        )
        return True, None
    except WebPushException as e:
        # 410 Gone 表示订阅已过期，应删除
        if e.response and e.response.status_code == 410:
            delete_subscription(subscription.get("endpoint"))
            return False, "订阅已过期并删除"
        return False, str(e)
    except Exception as e:
        return False, str(e)


def send_test_push_to_subscription(subscription, user_phone=None):
    """发送一条测试推送，用于验证推送订阅是否正常工作。

    参数:
        subscription: 推送订阅信息字典
        user_phone: 用户手机号（可选，用于日志）

    返回:
        (success: bool, detail: str)  — 成功与否及详细信息
    """
    summary_lines = []

    # 诊断 1: 检查 pywebpush 是否可用
    if webpush is None:
        summary_lines.append("✗ pywebpush 未安装 — 请执行 pip install pywebpush")
        return False, "\n".join(summary_lines)
    summary_lines.append("✓ pywebpush 已正确导入")

    # 诊断 2: 检查 VAPID 密钥
    try:
        vapid_private = get_vapid_private_key()
        vapid_public = get_vapid_public_key()
        summary_lines.append("✓ VAPID 密钥对已加载")
    except Exception as e:
        summary_lines.append(f"✗ VAPID 密钥异常: {e}")
        return False, "\n".join(summary_lines)

    # 诊断 3: 检查订阅信息完整性
    endpoint = subscription.get("endpoint", "")
    keys = subscription.get("keys", {})
    missing = []
    if not endpoint:
        missing.append("endpoint")
    if not keys.get("p256dh"):
        missing.append("p256dh")
    if not keys.get("auth"):
        missing.append("auth")
    if missing:
        summary_lines.append(f"✗ 订阅信息不完整，缺少: {', '.join(missing)}")
        return False, "\n".join(summary_lines)
    summary_lines.append(f"✓ 订阅信息完整 (endpoint: {endpoint[:40]}...)")

    # 诊断 4: 实际发送测试推送
    target = user_phone or "当前订阅"
    test_title = "🧪 EcoWise 测试推送"
    test_body = f"你好！这是一条测试推送。如果你能收到这条消息，说明 {target} 的推送订阅工作正常。"
    success, error = send_push_to_subscription(
        subscription,
        title=test_title,
        body=test_body,
        tag="ecowise-test",
    )

    if success:
        summary_lines.append(f"✓ 测试推送发送成功 → {target}")
    else:
        summary_lines.append(f"✗ 测试推送发送失败: {error}")

    return success, "\n".join(summary_lines)


def send_push_to_user(user_phone, title, body, **kwargs):
    """向指定用户发送推送"""
    subscriptions = get_subscriptions_by_user(user_phone)
    if not subscriptions:
        return False, "该用户没有订阅推送"

    results = []
    for sub in subscriptions:
        ok, err = send_push_to_subscription(sub, title, body, **kwargs)
        results.append({"endpoint": sub["endpoint"][:30] + "...", "success": ok, "error": err})

    success_count = sum(1 for r in results if r["success"])
    return success_count > 0, results


def broadcast_push(title, body, **kwargs):
    """向所有订阅用户广播推送"""
    subscriptions = get_all_subscriptions()
    if not subscriptions:
        return False, "没有订阅用户"

    results = []
    for sub in subscriptions:
        ok, err = send_push_to_subscription(sub, title, body, **kwargs)
        results.append({"endpoint": sub["endpoint"][:30] + "...", "success": ok, "error": err})

    success_count = sum(1 for r in results if r["success"])
    return success_count > 0, results
