"""
EcoWise 宿舍助理 - 自动断电 + 冷却期管理
=========================================
违规确认后自动断开插座，进入冷却期（默认10分钟）。
冷却期内拒绝用户重新开启，冷却期过后允许开启并解除事件。

触发位置：violation_detector.check_and_record() 确认违规时调用。
"""
import os
import sqlite3
from datetime import datetime, timedelta

import config

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "energy_log.db")


def _get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS power_off_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id     TEXT NOT NULL,
            device_name   TEXT,
            trigger_power REAL,
            triggered_at  TEXT NOT NULL,
            cooldown_until TEXT NOT NULL,
            is_released   INTEGER DEFAULT 0,
            record_date   TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pwr_dev ON power_off_events(device_id, is_released)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pwr_date ON power_off_events(record_date)")
    conn.commit()
    return conn


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_owner_phone(device_id):
    """通过设备 owner nickname 反查用户手机号（用于发通知）"""
    dev_meta = config.DEVICES.get(device_id, {})
    owner_nickname = dev_meta.get("owner", "")
    if not owner_nickname:
        return None
    try:
        import user_auth
        conn = user_auth._get_db()
        try:
            row = conn.execute(
                "SELECT username FROM users WHERE nickname=? LIMIT 1",
                (owner_nickname,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def trigger_power_off(device_id, device_name, peak_power):
    """
    触发自动断电：
    1. 调 device_client 关闭插座
    2. 写 power_off_events，cooldown_until = now + 冷却期
    3. 发送通知给设备 owner

    若设备已在冷却期则不重复触发。
    """
    cooldown = is_in_cooldown(device_id)
    if cooldown["in_cooldown"]:
        return {"ok": False, "msg": "设备已在冷却期，跳过重复断电"}

    from device_client import control_device_switch
    import notification

    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    today = now.strftime("%Y-%m-%d")
    cooldown_end = now + timedelta(minutes=config.AUTO_POWER_OFF_COOLDOWN_MINUTES)
    cooldown_until_str = cooldown_end.strftime("%Y-%m-%d %H:%M:%S")

    # 1. 关闭插座
    try:
        control_device_switch(device_id, False)
    except Exception as e:
        print(f"[自动断电] 关闭插座失败 device={device_id}: {e}")

    # 2. 写表
    conn = _get_db()
    try:
        cur = conn.execute(
            "INSERT INTO power_off_events (device_id, device_name, trigger_power, triggered_at, cooldown_until, is_released, record_date) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (device_id, device_name, peak_power or 0, now_str, cooldown_until_str, today),
        )
        event_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    # 3. 发通知
    phone = _get_owner_phone(device_id)
    if phone:
        try:
            title = "违规自动断电"
            message = (
                f"您的设备【{device_name or device_id}】检测到违规电器"
                f"（峰值功率{peak_power}W），已自动断电。"
                f"冷却期{config.AUTO_POWER_OFF_COOLDOWN_MINUTES}分钟，期间无法开启。"
            )
            notification.create_notification(phone, "violation_auto_off", title, message, device_id)
        except Exception as e:
            print(f"[自动断电] 发送通知失败: {e}")

    return {
        "ok": True,
        "event_id": event_id,
        "cooldown_until": cooldown_until_str,
        "msg": f"已断电，冷却期至 {cooldown_until_str}",
    }


def is_in_cooldown(device_id):
    """
    检查设备是否处于冷却期。
    返回 {"in_cooldown": bool, "remaining_minutes": int, "cooldown_until": str}
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, cooldown_until FROM power_off_events "
            "WHERE device_id=? AND is_released=0 "
            "ORDER BY triggered_at DESC LIMIT 1",
            (device_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {"in_cooldown": False, "remaining_minutes": 0, "cooldown_until": None}

    event_id, cooldown_until_str = row
    try:
        cooldown_until = datetime.strptime(cooldown_until_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return {"in_cooldown": False, "remaining_minutes": 0, "cooldown_until": None}

    now = datetime.now()
    if now < cooldown_until:
        remaining = int((cooldown_until - now).total_seconds() / 60) + 1
        return {
            "in_cooldown": True,
            "remaining_minutes": remaining,
            "cooldown_until": cooldown_until_str,
        }
    return {"in_cooldown": False, "remaining_minutes": 0, "cooldown_until": cooldown_until_str}


def get_latest_event(device_id):
    """获取设备最新断电事件（用于前端展示）"""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, device_id, device_name, trigger_power, triggered_at, cooldown_until, is_released "
            "FROM power_off_events WHERE device_id=? ORDER BY triggered_at DESC LIMIT 1",
            (device_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None
    cooldown = is_in_cooldown(device_id)
    return {
        "id": row[0],
        "device_id": row[1],
        "device_name": row[2],
        "trigger_power": round(row[3], 1) if row[3] else 0,
        "triggered_at": row[4],
        "cooldown_until": row[5],
        "is_released": bool(row[6]),
        "in_cooldown": cooldown["in_cooldown"],
        "remaining_minutes": cooldown["remaining_minutes"],
    }


def get_history(device_id=None, limit=20):
    """获取断电历史列表"""
    conn = _get_db()
    try:
        if device_id:
            rows = conn.execute(
                "SELECT id, device_id, device_name, trigger_power, triggered_at, cooldown_until, is_released, record_date "
                "FROM power_off_events WHERE device_id=? ORDER BY triggered_at DESC LIMIT ?",
                (device_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, device_id, device_name, trigger_power, triggered_at, cooldown_until, is_released, record_date "
                "FROM power_off_events ORDER BY triggered_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": r[0],
            "device_id": r[1],
            "device_name": r[2],
            "trigger_power": round(r[3], 1) if r[3] else 0,
            "triggered_at": r[4],
            "cooldown_until": r[5],
            "is_released": bool(r[6]),
            "record_date": r[7],
        }
        for r in rows
    ]


def release_event(device_id):
    """
    标记设备最新断电事件为已解除。
    用户在冷却期结束后重新开启插座时调用。
    """
    conn = _get_db()
    try:
        cur = conn.execute(
            "UPDATE power_off_events SET is_released=1 "
            "WHERE device_id=? AND is_released=0 "
            "AND id=(SELECT MAX(id) FROM power_off_events WHERE device_id=? AND is_released=0)",
            (device_id, device_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def clear_events(device_id=None):
    """
    清除自动断电记录。
    device_id=None 时清除所有记录，否则只清除指定设备的记录。
    """
    conn = _get_db()
    try:
        if device_id:
            conn.execute("DELETE FROM power_off_events WHERE device_id=?", (device_id,))
        else:
            conn.execute("DELETE FROM power_off_events")
        conn.commit()
    finally:
        conn.close()
