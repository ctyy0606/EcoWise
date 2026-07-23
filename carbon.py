"""
EcoWise 宿舍助理 - 碳排放计算模块
==================================
用电量(kWh) × 0.5777 kgCO₂e/kWh = 碳排放量
提供今日/本周/累计碳排放查询，复用 energy_history 的用电数据。
"""
import os
import sqlite3
from datetime import datetime, timedelta

import config
from energy_history import get_today_energy, get_month_energy

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "energy_log.db")


def _kwh_to_carbon(kwh):
    """用电量转碳排放量"""
    return round(kwh * config.CARBON_EMISSION_FACTOR, 4)


def get_today_carbon(device_id=None):
    """
    今日碳排放。
    device_id=None 时汇总所有设备。
    返回 {date, total_kwh, carbon_kg, hourly: [{hour, kwh, carbon_kg}]}
    """
    if device_id:
        data = get_today_energy(device_id)
    else:
        from energy_history import get_all_today
        data = get_all_today()

    hourly = []
    for h in data.get("hourly", []):
        kwh = h.get("kwh", 0)
        hourly.append({
            "hour": h.get("hour"),
            "kwh": round(kwh, 4),
            "carbon_kg": _kwh_to_carbon(kwh),
        })

    total_kwh = data.get("total_kwh", 0)
    return {
        "date": data.get("date", datetime.now().strftime("%Y-%m-%d")),
        "total_kwh": round(total_kwh, 4),
        "carbon_kg": _kwh_to_carbon(total_kwh),
        "hourly": hourly,
        "factor": config.CARBON_EMISSION_FACTOR,
    }


def get_week_carbon(device_id=None):
    """
    本周碳排放（从周一到周日）。
    返回 {start_date, end_date, total_kwh, carbon_kg, daily: [{date, kwh, carbon_kg}]}
    """
    now = datetime.now()
    weekday = now.weekday()
    monday = now - timedelta(days=weekday)
    dates = []
    for i in range(7):
        d = monday + timedelta(days=i)
        dates.append(d.strftime("%Y-%m-%d"))

    daily_map = {d: 0.0 for d in dates}

    if device_id:
        data = get_month_energy(device_id)
        for d in data.get("daily", []):
            if d["date"] in daily_map:
                daily_map[d["date"]] = d.get("kwh", 0)
    else:
        for did in config.DEVICES.keys():
            data = get_month_energy(did)
            for d in data.get("daily", []):
                if d["date"] in daily_map:
                    daily_map[d["date"]] += d.get("kwh", 0)

    daily = []
    total_kwh = 0.0
    for date in dates:
        kwh = round(daily_map[date], 4)
        daily.append({
            "date": date,
            "dateShort": date.split("-")[2],
            "kwh": kwh,
            "carbon_kg": _kwh_to_carbon(kwh),
        })
        total_kwh += kwh

    return {
        "start_date": dates[0],
        "end_date": dates[-1],
        "total_kwh": round(total_kwh, 4),
        "carbon_kg": _kwh_to_carbon(total_kwh),
        "daily": daily,
        "factor": config.CARBON_EMISSION_FACTOR,
    }


def get_total_carbon(device_id=None):
    """
    累计碳排放（从最早记录到当前）。
    返回 {total_kwh, carbon_kg, start_date, trees_equivalent}
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000;")
    try:
        if device_id:
            first = conn.execute(
                "SELECT energy_wh, record_date FROM energy_records WHERE device_id=? ORDER BY recorded_at ASC LIMIT 1",
                (device_id,),
            ).fetchone()
            last = conn.execute(
                "SELECT energy_wh FROM energy_records WHERE device_id=? ORDER BY recorded_at DESC LIMIT 1",
                (device_id,),
            ).fetchone()
        else:
            first = conn.execute(
                "SELECT energy_wh, record_date FROM energy_records ORDER BY recorded_at ASC LIMIT 1",
            ).fetchone()
            last = conn.execute(
                "SELECT energy_wh FROM energy_records ORDER BY recorded_at DESC LIMIT 1",
            ).fetchone()
    finally:
        conn.close()

    if not first or not last:
        return {
            "total_kwh": 0,
            "carbon_kg": 0,
            "start_date": None,
            "trees_equivalent": 0,
            "factor": config.CARBON_EMISSION_FACTOR,
        }

    total_wh = max(0.0, last[0] - first[0])
    total_kwh = round(total_wh / 1000.0, 4)
    carbon_kg = _kwh_to_carbon(total_kwh)
    # 一棵树年吸收约 18 kg CO₂，折算等效植树数
    trees = round(carbon_kg / 18, 2)

    return {
        "total_kwh": total_kwh,
        "carbon_kg": carbon_kg,
        "start_date": first[1],
        "trees_equivalent": trees,
        "factor": config.CARBON_EMISSION_FACTOR,
    }
