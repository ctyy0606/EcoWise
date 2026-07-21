"""
EcoWise 宿舍助理 - 通知系统
============================
应用内通知，支持违规断电、熬夜提醒、周报生成等通知类型。
前端通过轮询 /api/notifications/unread_count 获取未读数。
"""
import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.expanduser("~"))), "Ecowise", "energy_log.db")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_phone  TEXT NOT NULL,
            type        TEXT NOT NULL,
            title       TEXT NOT NULL,
            message     TEXT NOT NULL,
            device_id   TEXT,
            is_read     INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_phone, is_read)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notif_created ON notifications(created_at DESC)")
    conn.commit()
    return conn


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_notification(user_phone, notif_type, title, message, device_id=None):
    """
    创建通知。
    notif_type: violation_auto_off / late_night_warning / weekly_report
    返回 notification_id
    """
    conn = _get_db()
    try:
        cur = conn.execute(
            "INSERT INTO notifications (user_phone, type, title, message, device_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_phone, notif_type, title, message, device_id, _now()),
        )
        conn.commit()
        notification_id = cur.lastrowid

        # 同时发送浏览器推送通知（如果用户已订阅）
        try:
            import push_notification
            push_notification.send_push_to_user(
                user_phone,
                title=title,
                body=message,
                tag=notif_type,
                require_interaction=(notif_type == "violation_auto_off"),
            )
        except Exception:
            # 推送失败不影响应用内通知
            pass

        return notification_id
    finally:
        conn.close()


def get_notifications(user_phone, unread_only=False, limit=20):
    """获取通知列表，按创建时间倒序"""
    conn = _get_db()
    try:
        if unread_only:
            rows = conn.execute(
                "SELECT id, type, title, message, device_id, is_read, created_at "
                "FROM notifications WHERE user_phone=? AND is_read=0 "
                "ORDER BY created_at DESC LIMIT ?",
                (user_phone, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, type, title, message, device_id, is_read, created_at "
                "FROM notifications WHERE user_phone=? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_phone, limit),
            ).fetchall()
        return [
            {
                "id": r[0], "type": r[1], "title": r[2], "message": r[3],
                "device_id": r[4], "is_read": bool(r[5]), "created_at": r[6],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_unread_count(user_phone):
    """获取未读通知数量"""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_phone=? AND is_read=0",
            (user_phone,),
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def mark_as_read(notification_id, user_phone):
    """标记单条通知为已读（验证归属）"""
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE notifications SET is_read=1 WHERE id=? AND user_phone=?",
            (notification_id, user_phone),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def mark_all_as_read(user_phone):
    """全部标记已读"""
    conn = _get_db()
    try:
        cur = conn.execute(
            "UPDATE notifications SET is_read=1 WHERE user_phone=? AND is_read=0",
            (user_phone,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def check_recent_notification(user_phone, notif_type, device_id=None, hours=1):
    """
    检查最近 hours 小时内是否已发过同类通知（防重复）。
    返回 True 表示最近已发过，不应再发。
    """
    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_db()
    try:
        if device_id:
            row = conn.execute(
                "SELECT 1 FROM notifications WHERE user_phone=? AND type=? AND device_id=? "
                "AND created_at > ? LIMIT 1",
                (user_phone, notif_type, device_id, cutoff),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM notifications WHERE user_phone=? AND type=? "
                "AND created_at > ? LIMIT 1",
                (user_phone, notif_type, cutoff),
            ).fetchone()
        return row is not None
    finally:
        conn.close()


def check_and_notify_late_night(user_phone, device_id, device_name, power_w):
    """
    熬夜提醒：23:00后功率>200W → 发送通知。
    1小时内不重复发送。
    返回 True 表示发送了通知。
    """
    now = datetime.now()
    hour = now.hour
    if hour < 23 and hour >= 6:
        return False
    if power_w is None or power_w <= 200:
        return False

    if check_recent_notification(user_phone, "late_night_warning", device_id, hours=1):
        return False

    title = "熬夜提醒"
    message = f"现在已{hour}点，{device_name}功率{power_w}W仍在使用，注意休息，避免熬夜哦"
    create_notification(user_phone, "late_night_warning", title, message, device_id)
    return True


def notify_weekly_report(user_phone, report_type):
    """
    周报生成通知：周报生成完成后发送通知。
    report_type: personal / space
    """
    title = "周报已生成"
    if report_type == "personal":
        message = "你的个人用电周报已生成，快来查看本周用电分析和节能建议吧！"
    else:
        message = "空间用电周报已生成，包含所有成员的用电统计和分摊明细。"
    
    create_notification(user_phone, "weekly_report", title, message)
    return True


