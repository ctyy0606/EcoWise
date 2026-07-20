"""
EcoWise 宿舍助理 - 作息分析模块
================================
多模态融合推断逻辑：
- 功率+温湿度+光照联合判断宿舍状态（入睡/起床/外出/活动/疑似违规）
- 入睡条件：功率<30W + 光照低 + 持续30分钟
- 熬夜提醒：23:00后持续高功率(>200W)活动 → 推送提醒
- 违规判定：高功率(>800W) + 低光照 + 深夜 = 疑似违规

硬件（T5开发板+DHT11+光敏电阻）接好后可直接使用真实数据。
"""
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List

import config

DB_PATH = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.expanduser("~"))), "Ecowise", "energy_log.db")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedule_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id       TEXT NOT NULL,
            user_phone      TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            start_time      TEXT NOT NULL,
            end_time        TEXT,
            duration_minutes REAL,
            record_date     TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dorm_status (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_phone      TEXT NOT NULL,
            status          TEXT NOT NULL,
            confidence      REAL,
            timestamp       TEXT NOT NULL,
            power_w         REAL,
            light_level     INTEGER,
            temperature_c   REAL,
            humidity_percent REAL,
            record_date     TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sched_user ON schedule_events(user_phone, record_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dorm_user ON dorm_status(user_phone, record_date)")
    conn.commit()
    return conn


def _get_user_device_ids(user_phone):
    """获取用户的所有设备ID（通过 owner nickname 反查）"""
    try:
        import user_auth
        conn = user_auth._get_db()
        try:
            row = conn.execute(
                "SELECT nickname FROM users WHERE username=?", (user_phone,)
            ).fetchone()
            nickname = row[0] if row else None
        finally:
            conn.close()
        if not nickname:
            return []
        return [did for did, meta in config.DEVICES.items() if meta.get("owner") == nickname]
    except Exception:
        return []


def _detect_dorm_status(power_w, light_level, temperature_c=None, humidity_percent=None):
    """
    多模态融合推断宿舍状态：
    - 入睡：功率<30W + 光照低(<50) + 持续30分钟
    - 活动：功率>30W + 光照正常
    - 外出：功率=0 + 光照正常 + 持续一段时间
    - 疑似违规：高功率(>800W) + 低光照 + 深夜
    - 熬夜：23:00后 功率>200W
    """
    now = datetime.now()
    is_night = now.hour >= 23 or now.hour < 6
    
    if power_w is None or light_level is None:
        return {"status": "unknown", "confidence": 0.0}
    
    if power_w < 30 and light_level < 50:
        return {"status": "sleeping", "confidence": 0.9}
    elif power_w > 800 and light_level < 50 and is_night:
        return {"status": "violation", "confidence": 0.95}
    elif power_w > 200 and is_night:
        return {"status": "staying_up", "confidence": 0.85}
    elif power_w > 30:
        return {"status": "active", "confidence": 0.8}
    elif power_w == 0 and light_level > 50:
        return {"status": "out", "confidence": 0.7}
    elif power_w == 0 and light_level < 50:
        return {"status": "sleeping", "confidence": 0.85}
    else:
        return {"status": "unknown", "confidence": 0.5}


def analyze_sleep(device_id, user_phone):
    """
    扫描 energy_records 的时序数据，识别入睡/醒来事件。
    入睡：功率<30W + 光照<50持续30分钟
    醒来：光照>50 或 功率>30W
    """
    today = datetime.now().strftime("%Y-%m-%d")
    light_threshold = 50
    power_threshold = 30
    sleep_duration = config.SCHEDULE["sleep_duration_minutes"]

    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT recorded_at, power_w, light_level FROM energy_records "
            "WHERE device_id=? AND record_date=? "
            "ORDER BY recorded_at ASC",
            (device_id, today),
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < 2:
        return {"analyzed": False, "msg": "今日数据不足"}

    events = []
    is_sleeping = False
    sleep_start = None
    dark_since = None

    for time_str, power, light in rows:
        t = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        
        is_dark = light is not None and light < light_threshold
        is_low_power = power is not None and power < power_threshold
        
        if is_dark and is_low_power:
            if dark_since is None:
                dark_since = t
            if not is_sleeping and dark_since and (t - dark_since).total_seconds() >= sleep_duration * 60:
                is_sleeping = True
                sleep_start = dark_since
        else:
            if is_sleeping:
                events.append({
                    "type": "sleep",
                    "start": sleep_start.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": t.strftime("%Y-%m-%d %H:%M:%S"),
                    "duration_minutes": round((t - sleep_start).total_seconds() / 60, 1),
                })
                is_sleeping = False
                sleep_start = None
            dark_since = None

    if is_sleeping and sleep_start:
        now = datetime.now()
        events.append({
            "type": "sleep",
            "start": sleep_start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": None,
            "duration_minutes": round((now - sleep_start).total_seconds() / 60, 1),
        })

    sched_conn = _get_db()
    try:
        sched_conn.execute(
            "DELETE FROM schedule_events WHERE user_phone=? AND record_date=?",
            (user_phone, today),
        )
        for ev in events:
            sched_conn.execute(
                "INSERT INTO schedule_events (device_id, user_phone, event_type, start_time, end_time, duration_minutes, record_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (device_id, user_phone, ev["type"], ev["start"], ev["end"], ev["duration_minutes"], today),
            )
        sched_conn.commit()
    finally:
        sched_conn.close()

    return {"analyzed": True, "events": events, "count": len(events)}


def record_dorm_status(user_phone, power_w, light_level, temperature_c=None, humidity_percent=None):
    """记录当前宿舍状态"""
    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    status = _detect_dorm_status(power_w, light_level, temperature_c, humidity_percent)
    
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO dorm_status (user_phone, status, confidence, timestamp, power_w, light_level, temperature_c, humidity_percent, record_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_phone, status["status"], status["confidence"], now_str, power_w, light_level, temperature_c, humidity_percent, today),
        )
        conn.commit()
    finally:
        conn.close()
    
    return status


def get_current_dorm_status(user_phone) -> Dict:
    """获取当前宿舍状态"""
    today = datetime.now().strftime("%Y-%m-%d")
    
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT status, confidence, timestamp, power_w, light_level, temperature_c, humidity_percent "
            "FROM dorm_status WHERE user_phone=? AND record_date=? ORDER BY id DESC LIMIT 1",
            (user_phone, today),
        ).fetchone()
    finally:
        conn.close()
    
    if row:
        status_map = {
            "sleeping": "已休息",
            "active": "活动中",
            "out": "外出",
            "violation": "疑似违规",
            "staying_up": "熬夜中",
            "unknown": "未知"
        }
        return {
            "status": row[0],
            "status_text": status_map.get(row[0], "未知"),
            "confidence": round(row[1], 2),
            "timestamp": row[2],
            "power_w": row[3],
            "light_level": row[4],
            "temperature_c": row[5],
            "humidity_percent": row[6],
        }
    return {"status": "unknown", "status_text": "未知", "confidence": 0}


def check_stay_up_late(user_phone) -> Dict:
    """检查是否熬夜（23:00后持续高功率>200W）"""
    now = datetime.now()
    if now.hour < 23 and now.hour >= 6:
        return {"is_staying_up": False, "reason": "非熬夜时段"}
    
    today = now.strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT recorded_at, power_w FROM energy_records "
            "WHERE device_id IN ({}) AND record_date=? AND power_w > 200 "
            "ORDER BY recorded_at DESC LIMIT 10".format(
                ",".join("?" * len(_get_user_device_ids(user_phone)))
            ),
            (_get_user_device_ids(user_phone) + [today]),
        ).fetchall()
    finally:
        conn.close()
    
    if not rows:
        return {"is_staying_up": False, "reason": "功率低于200W"}
    
    recent_power = [r[1] for r in rows]
    avg_power = sum(recent_power) / len(recent_power)
    
    return {
        "is_staying_up": avg_power > 200,
        "avg_power_w": round(avg_power, 1),
        "reason": "深夜高功率活动" if avg_power > 200 else "功率低于200W",
        "suggestion": "该休息了，请注意身体健康" if avg_power > 200 else None,
    }


def get_today_schedule(user_phone) -> Dict:
    """获取今日作息"""
    today = datetime.now().strftime("%Y-%m-%d")
    device_ids = _get_user_device_ids(user_phone)
    for did in device_ids:
        analyze_sleep(did, user_phone)

    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT device_id, event_type, start_time, end_time, duration_minutes "
            "FROM schedule_events WHERE user_phone=? AND record_date=? ORDER BY start_time ASC",
            (user_phone, today),
        ).fetchall()
    finally:
        conn.close()

    events = []
    total_sleep = 0
    for dev_id, etype, start, end, dur in rows:
        events.append({
            "device_id": dev_id,
            "type": etype,
            "start_time": start,
            "end_time": end,
            "duration_minutes": dur,
        })
        if etype == "sleep" and dur:
            total_sleep += dur

    return {
        "date": today,
        "events": events,
        "total_sleep_minutes": round(total_sleep, 1),
        "total_sleep_hours": round(total_sleep / 60, 2),
    }


def get_week_schedule(user_phone) -> Dict:
    """获取本周作息汇总（每日睡眠时长）- 从周一到周日"""
    now = datetime.now()
    weekday = now.weekday()
    monday = now - timedelta(days=weekday)
    daily = []
    for i in range(7):
        d = monday + timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT duration_minutes FROM schedule_events "
                "WHERE user_phone=? AND record_date=? AND event_type='sleep'",
                (user_phone, date_str),
            ).fetchall()
        finally:
            conn.close()
        sleep_min = sum(r[0] or 0 for r in rows)
        daily.append({
            "date": date_str,
            "dateShort": date_str.split("-")[2],
            "sleep_minutes": round(sleep_min, 1),
            "sleep_hours": round(sleep_min / 60, 2),
        })

    return {
        "start_date": daily[0]["date"],
        "end_date": daily[-1]["date"],
        "daily": daily,
    }


def get_schedule_summary(user_phone) -> Dict:
    """作息汇总：平均睡眠时长、入睡时间趋势、熬夜天数"""
    week = get_week_schedule(user_phone)
    today = get_today_schedule(user_phone)

    sleep_hours = [d["sleep_hours"] for d in week["daily"] if d["sleep_hours"] > 0]
    avg_sleep = sum(sleep_hours) / len(sleep_hours) if sleep_hours else 0

    late_night_count = 0
    conn = _get_db()
    try:
        for d in week["daily"]:
            rows = conn.execute(
                "SELECT start_time FROM schedule_events "
                "WHERE user_phone=? AND record_date=? AND event_type='sleep' ORDER BY start_time ASC LIMIT 1",
                (user_phone, d["date"]),
            ).fetchall()
            if rows:
                start_dt = datetime.strptime(rows[0][0], "%Y-%m-%d %H:%M:%S")
                if start_dt.hour >= 23 or start_dt.hour < 1:
                    late_night_count += 1
    finally:
        conn.close()

    return {
        "avg_sleep_hours": round(avg_sleep, 2),
        "late_night_count": late_night_count,
        "today_sleep_hours": today["total_sleep_hours"],
        "week_range": f"{week['start_date']} ~ {week['end_date']}",
    }
