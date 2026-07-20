"""
EcoWise 宿舍助理 - AI 周报生成
===============================
汇总最近7天用电数据（用电量、电费、违规次数、碳排放），
调用通义千问生成周报文本，存入 weekly_reports 表。
"""
import os
import sqlite3
import requests
from datetime import datetime, timedelta

import config

DB_PATH = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.expanduser("~"))), "Ecowise", "energy_log.db")
API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_reports (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_phone      TEXT NOT NULL,
            owner_nickname  TEXT,
            space_id        TEXT,
            week_start      TEXT NOT NULL,
            week_end        TEXT NOT NULL,
            content         TEXT NOT NULL,
            total_kwh       REAL DEFAULT 0,
            total_yuan      REAL DEFAULT 0,
            carbon_kg       REAL DEFAULT 0,
            violation_count INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_report_user ON weekly_reports(user_phone, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_report_space ON weekly_reports(space_id, created_at)")
    try:
        conn.execute("ALTER TABLE weekly_reports ADD COLUMN space_id TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _collect_week_data(owner_nickname):
    """汇总本周数据（从周一到周日，按 owner 过滤设备）"""
    from energy_history import get_month_energy
    from carbon import get_week_carbon
    from violation_detector import get_month_violation_count
    import device_client

    now = datetime.now()
    weekday = now.weekday()
    monday = now - timedelta(days=weekday)
    week_start = monday.strftime("%Y-%m-%d")
    week_end = (monday + timedelta(days=6)).strftime("%Y-%m-%d")

    # 获取该用户的设备
    all_devices = device_client.get_all_devices()
    if owner_nickname:
        devices = [d for d in all_devices if d.get("owner") == owner_nickname]
    else:
        devices = all_devices

    # 汇总7天用电量
    total_kwh = 0.0
    for dev in devices:
        if "error" in dev:
            continue
        dev_id = dev.get("device_id")
        if not dev_id:
            continue
        try:
            month_data = get_month_energy(dev_id)
            for d in month_data.get("daily", []):
                # 只统计最近7天
                if week_start <= d.get("date", "") <= week_end:
                    total_kwh += d.get("kwh", 0)
        except Exception:
            pass

    total_kwh = round(total_kwh, 4)
    total_yuan = round(total_kwh * config.ELECTRICITY_PRICE_PER_KWH, 2)

    # 碳排放
    try:
        # 取用户第一个设备的周碳排放，或汇总
        carbon_data = get_week_carbon(None if not owner_nickname else None)
        carbon_kg = round(carbon_data.get("carbon_kg", 0), 4)
    except Exception:
        carbon_kg = 0

    # 违规次数（本月，近似）
    try:
        violation_count = get_month_violation_count(None)
    except Exception:
        violation_count = 0

    return {
        "week_start": week_start,
        "week_end": week_end,
        "total_kwh": total_kwh,
        "total_yuan": total_yuan,
        "carbon_kg": carbon_kg,
        "violation_count": violation_count,
    }


def _call_qwen(prompt):
    """调用通义千问生成周报"""
    headers = {
        "Authorization": f"Bearer {config.QWEN_API_KEY}",
        "Content-Type": "application/json",
    }
    messages = [
        {"role": "system", "content": "你是 EcoWise 宿舍用电助手，负责生成简洁实用的用电周报。"},
        {"role": "user", "content": prompt},
    ]
    data = {"model": config.QWEN_MODEL, "messages": messages}
    resp = requests.post(API_URL, headers=headers, json=data, timeout=30)
    result = resp.json()
    if "choices" in result and len(result["choices"]) > 0:
        return result["choices"][0]["message"]["content"]
    raise RuntimeError(f"AI 返回异常: {result}")


def generate_report(user_phone, owner_nickname=None):
    """
    生成周报：采集数据 → 调 AI → 存表。
    返回 {"success": bool, "report": dict, "message": str}
    """
    data = _collect_week_data(owner_nickname)

    prompt = f"""请根据以下本周用电数据生成一份简洁的宿舍用电周报：

【本周数据】
- 统计周期：{data['week_start']} 至 {data['week_end']}
- 本周总用电：{data['total_kwh']} 度（kWh）
- 本周电费：{data['total_yuan']} 元（电价 {config.ELECTRICITY_PRICE_PER_KWH} 元/度）
- 碳排放：{data['carbon_kg']} kgCO₂
- 本月违规次数：{data['violation_count']} 次

【要求】
1. 包含三个部分：用电概况、节能建议、下周提醒
2. 语气友好贴心，像室友一样
3. 200字以内
4. 如果用电量为0，提示用户检查设备是否正常连接"""

    content = _call_qwen(prompt)

    conn = _get_db()
    try:
        cur = conn.execute(
            "INSERT INTO weekly_reports (user_phone, owner_nickname, week_start, week_end, content, total_kwh, total_yuan, carbon_kg, violation_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_phone, owner_nickname, data["week_start"], data["week_end"],
                content, data["total_kwh"], data["total_yuan"], data["carbon_kg"],
                data["violation_count"], _now(),
            ),
        )
        report_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    return {
        "success": True,
        "message": "周报生成成功",
        "report": {
            "id": report_id,
            "week_start": data["week_start"],
            "week_end": data["week_end"],
            "content": content,
            "total_kwh": data["total_kwh"],
            "total_yuan": data["total_yuan"],
            "carbon_kg": data["carbon_kg"],
            "violation_count": data["violation_count"],
            "created_at": _now(),
        },
    }


def generate_space_report(space_id, user_phone):
    """
    生成空间周报：汇总空间内所有成员的用电数据。
    返回 {"success": bool, "report": dict, "message": str}
    """
    import user_auth
    conn = user_auth._get_db()
    try:
        rows = conn.execute(
            "SELECT nickname FROM space_members WHERE space_id=?",
            (space_id,),
        ).fetchall()
        members = [r[0] for r in rows]
    finally:
        conn.close()

    if not members:
        return {"success": False, "message": "空间无成员"}

    all_data = []
    for nickname in members:
        data = _collect_week_data(nickname)
        all_data.append(data)

    total_kwh = sum(d["total_kwh"] for d in all_data)
    total_yuan = sum(d["total_yuan"] for d in all_data)
    carbon_kg = sum(d["carbon_kg"] for d in all_data)
    violation_count = sum(d["violation_count"] for d in all_data)

    week_start = all_data[0]["week_start"]
    week_end = all_data[0]["week_end"]

    prompt = f"""请根据以下空间本周用电数据生成一份简洁的宿舍用电周报：

【空间信息】
- 空间成员：{', '.join(members)}

【本周数据】
- 统计周期：{week_start} 至 {week_end}
- 本周总用电：{total_kwh} 度（kWh）
- 本周电费：{total_yuan} 元（电价 {config.ELECTRICITY_PRICE_PER_KWH} 元/度）
- 碳排放：{carbon_kg} kgCO₂
- 本月违规次数：{violation_count} 次

【要求】
1. 包含三个部分：用电概况、节能建议、下周提醒
2. 语气友好贴心，像室友一样
3. 200字以内
4. 如果用电量为0，提示用户检查设备是否正常连接"""

    content = _call_qwen(prompt)

    conn = _get_db()
    try:
        cur = conn.execute(
            "INSERT INTO weekly_reports (user_phone, owner_nickname, week_start, week_end, content, total_kwh, total_yuan, carbon_kg, violation_count, created_at, space_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_phone, None, week_start, week_end,
                content, total_kwh, total_yuan, carbon_kg,
                violation_count, _now(), space_id,
            ),
        )
        report_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    return {
        "success": True,
        "message": "空间周报生成成功",
        "report": {
            "id": report_id,
            "week_start": week_start,
            "week_end": week_end,
            "content": content,
            "total_kwh": total_kwh,
            "total_yuan": total_yuan,
            "carbon_kg": carbon_kg,
            "violation_count": violation_count,
            "created_at": _now(),
            "space_id": space_id,
            "members": members,
        },
    }


def get_latest_space_report(space_id):
    """获取空间最新一期周报"""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, week_start, week_end, content, total_kwh, total_yuan, carbon_kg, violation_count, created_at "
            "FROM weekly_reports WHERE space_id=? ORDER BY created_at DESC LIMIT 1",
            (space_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None
    return {
        "id": row[0],
        "week_start": row[1],
        "week_end": row[2],
        "content": row[3],
        "total_kwh": row[4],
        "total_yuan": row[5],
        "carbon_kg": row[6],
        "violation_count": row[7],
        "created_at": row[8],
        "space_id": space_id,
    }


def get_latest_report(user_phone):
    """获取用户最新一期周报"""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, week_start, week_end, content, total_kwh, total_yuan, carbon_kg, violation_count, created_at "
            "FROM weekly_reports WHERE user_phone=? ORDER BY created_at DESC LIMIT 1",
            (user_phone,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None
    return {
        "id": row[0],
        "week_start": row[1],
        "week_end": row[2],
        "content": row[3],
        "total_kwh": row[4],
        "total_yuan": row[5],
        "carbon_kg": row[6],
        "violation_count": row[7],
        "created_at": row[8],
    }


def get_report_history(user_phone, limit=10):
    """获取周报历史列表"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, week_start, week_end, content, total_kwh, total_yuan, carbon_kg, violation_count, created_at "
            "FROM weekly_reports WHERE user_phone=? ORDER BY created_at DESC LIMIT ?",
            (user_phone, limit),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": r[0],
            "week_start": r[1],
            "week_end": r[2],
            "content": r[3],
            "total_kwh": r[4],
            "total_yuan": r[5],
            "carbon_kg": r[6],
            "violation_count": r[7],
            "created_at": r[8],
        }
        for r in rows
    ]
