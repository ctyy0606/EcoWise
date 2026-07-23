"""
EcoWise 宿舍助理 - 历史用电量统计(本地存储版)
================================================
不依赖涂鸦统计接口(那个需要开通权限),改用本地 SQLite 存储:
- 每 10 分钟自动采集一次 add_ele(累计电量)
- "今日用电" = 当前累计电量 - 今日最早记录的累计电量
- "每日用电" = 每天最早累计电量 - 前一天最早累计电量

数据库: energy_log.db(自动创建在当前目录)

调用方式:
- 调用 record_once(device_id) 记录一次累计电量
- 调用 get_today_energy(device_id) 查今日用电
- 调用 get_month_energy(device_id) 查本月每日用电

主程序运行时自动记录,也可以单独跑:
    python energy_history.py record   # 记录一次
    python energy_history.py show     # 打印今日和本月
"""
import os
import sqlite3
from typing import Dict, List
from datetime import datetime, timedelta

import config
from device_client import get_device_data


# ============ 数据库初始化 ============
DB_PATH = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.expanduser("~"))), "Ecowise", "energy_log.db")


def _get_db() -> sqlite3.Connection:
    """获取数据库连接,首次调用时自动建表。"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS energy_records (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT NOT NULL,
            energy_wh   REAL NOT NULL,           -- 累计电量 Wh
            recorded_at TEXT NOT NULL,            -- YYYY-MM-DD HH:MM:SS
            record_date TEXT NOT NULL,            -- YYYY-MM-DD (方便按天聚合)
            record_hour INTEGER NOT NULL,         -- 0-23 (方便按小时聚合)
            power_w     REAL                      -- 采集时的瞬时功率(W)
        )
    """)
    # 兼容旧表：如果 power_w 列不存在则添加
    try:
        conn.execute("SELECT power_w FROM energy_records LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE energy_records ADD COLUMN power_w REAL")
    # 加索引,加快按设备/日期查询
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dev_date ON energy_records(device_id, record_date)")
    conn.commit()
    return conn


# ============ 采集:把当前累计电量存进数据库 ============
def record_once(device_id: str) -> Dict:
    """
    读取一次设备实时数据,把累计电量存进数据库。

    返回: {"device_id": ..., "energy_wh": ..., "recorded_at": ..., "ok": bool}
    """
    data = get_device_data(device_id)
    if "error" in data or data.get("energy_wh") is None:
        return {
            "device_id": device_id,
            "ok": False,
            "msg": data.get("error", "无法读取累计电量"),
        }

    now = datetime.now()
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO energy_records (device_id, energy_wh, recorded_at, record_date, record_hour, power_w) VALUES (?, ?, ?, ?, ?, ?)",
            (
                device_id,
                float(data["energy_wh"]),
                now.strftime("%Y-%m-%d %H:%M:%S"),
                now.strftime("%Y-%m-%d"),
                now.hour,
                data.get("power_w"),
            ),
        )
        conn.commit()
        return {
            "device_id": device_id,
            "energy_wh": data["energy_wh"],
            "power_w": data.get("power_w"),
            "recorded_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "ok": True,
        }
    finally:
        conn.close()


def record_all() -> List[Dict]:
    """采集所有设备一次，优先复用 device_client 的缓存数据，减少涂鸦云 API 调用。"""
    try:
        from device_client import get_all_devices
        devices = get_all_devices(include_paired_boards=False)
    except Exception as e:
        print(f"[energy_history] 批量获取设备数据失败: {e}")
        devices = []

    results = []
    for dev in devices:
        device_id = dev.get("device_id")
        if not device_id or "error" in dev:
            continue
        energy_wh = dev.get("energy_wh")
        if energy_wh is None:
            continue

        now = datetime.now()
        conn = _get_db()
        try:
            conn.execute(
                "INSERT INTO energy_records (device_id, energy_wh, recorded_at, record_date, record_hour, power_w) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    device_id,
                    float(energy_wh),
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    now.strftime("%Y-%m-%d"),
                    now.hour,
                    dev.get("power_w"),
                ),
            )
            conn.commit()
            results.append({
                "device_id": device_id,
                "energy_wh": energy_wh,
                "power_w": dev.get("power_w"),
                "recorded_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "ok": True,
            })
        finally:
            conn.close()
    return results


def clear_today_records():
    """清除今日所有采集记录(关闭测试模式时调用,避免模拟数据污染真实统计)"""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_db()
    try:
        conn.execute(
            "DELETE FROM energy_records WHERE record_date=?",
            (today,),
        )
        conn.commit()
        print(f"[energy_history] 已清除 {today} 的采集记录")
    finally:
        conn.close()


# ============ 查询:今日用电量(按小时) ============
def get_today_energy(device_id: str) -> Dict:
    """
    今日用电量 = 当前累计电量 - 今日最早累计电量
    分小时明细 = 每个小时内累计电量的增量

    返回:
        {
            "date":      "YYYY-MM-DD",
            "total_kwh": 今日总电量(度),
            "hourly":    [{"hour": 0, "kwh": 0.01}, ...],
        }
    """
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_db()
    try:
        # 取今天所有记录,按时间排序
        rows = conn.execute(
            "SELECT energy_wh, record_hour, recorded_at FROM energy_records "
            "WHERE device_id=? AND record_date=? ORDER BY recorded_at ASC",
            (device_id, today),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"date": today, "total_kwh": 0.0, "hourly": []}

    # 第一个记录是"今日起始读数"
    start_wh = rows[0][0]
    # 最后一个记录是"当前读数"
    end_wh = rows[-1][0]
    total_wh = max(0.0, end_wh - start_wh)
    total_kwh = round(total_wh / 1000.0, 4)

    # 分小时:把每个小时的"第一条记录 - 前一小时最后一条记录"算增量
    # 简化做法:按小时分组,取每小时内(最大-最小)
    hourly_map = {}
    for wh, hour, _ in rows:
        if hour not in hourly_map:
            hourly_map[hour] = {"min": wh, "max": wh}
        else:
            hourly_map[hour]["min"] = min(hourly_map[hour]["min"], wh)
            hourly_map[hour]["max"] = max(hourly_map[hour]["max"], wh)

    hourly = []
    for hour in sorted(hourly_map.keys()):
        h_data = hourly_map[hour]
        hour_delta_wh = max(0.0, h_data["max"] - h_data["min"])
        hourly.append({
            "hour": hour,
            "kwh": round(hour_delta_wh / 1000.0, 4),
        })

    return {
        "date": today,
        "total_kwh": total_kwh,
        "hourly": hourly,
    }


# ============ 查询:本月用电量(按日) ============
def get_month_energy(device_id: str) -> Dict:
    """
    本月用电量 = 当前累计电量 - 本月1号最早累计电量
    分日明细 = 每天的(当日最后一条 - 当日第一条)

    返回:
        {
            "month":      "YYYY-MM",
            "total_kwh":  本月总电量(度),
            "daily":      [{"date": "YYYY-MM-DD", "kwh": 1.2}, ...],
        }
    """
    now = datetime.now()
    month_str = now.strftime("%Y-%m")
    # 本月所有日期:从 1 号到今天
    start_date = now.replace(day=1)
    date_list = []
    cur = start_date
    while cur <= now:
        date_list.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)

    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT energy_wh, record_date FROM energy_records "
            "WHERE device_id=? AND record_date LIKE ? ORDER BY recorded_at ASC",
            (device_id, f"{month_str}-%"),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"month": month_str, "total_kwh": 0.0, "daily": []}

    # 按日期分组
    daily_map = {}
    for wh, date in rows:
        if date not in daily_map:
            daily_map[date] = {"first": wh, "last": wh}
        else:
            daily_map[date]["last"] = wh  # 最后一条会覆盖成 last

    # 当日用电 = 当日 last - first
    daily = []
    for date in date_list:  # 按日期顺序
        if date in daily_map:
            d = daily_map[date]
            delta_wh = max(0.0, d["last"] - d["first"])
            daily.append({
                "date": date,
                "kwh": round(delta_wh / 1000.0, 4),
            })
        else:
            daily.append({"date": date, "kwh": 0.0})

    # 本月总电量 = 当前累计 - 月初累计
    start_wh = rows[0][0]
    end_wh = rows[-1][0]
    total_wh = max(0.0, end_wh - start_wh)
    total_kwh = round(total_wh / 1000.0, 4)

    return {
        "month": month_str,
        "total_kwh": total_kwh,
        "daily": daily,
    }


# ============ 打印函数 ============
def print_today(device_id: str) -> None:
    """打印今日用电量。"""
    data = get_today_energy(device_id)
    dev_name = config.DEVICES.get(device_id, {}).get("name", device_id)
    print(f"\n[{dev_name}] 今日用电量 ({data['date']})")
    print(f"  今日累计: {data['total_kwh']} 度 = {data['total_kwh']*1000} Wh")
    if data["hourly"]:
        print("  分小时明细:")
        for h in data["hourly"]:
            print(f"    {h['hour']:>2}时: {h['kwh']} 度")
    else:
        print("  分小时明细: 暂无(还没采集到今天的数据)")


def print_month(device_id: str) -> None:
    """打印本月用电量。"""
    data = get_month_energy(device_id)
    dev_name = config.DEVICES.get(device_id, {}).get("name", device_id)
    print(f"\n[{dev_name}] 本月用电量 ({data['month']})")
    print(f"  本月累计: {data['total_kwh']} 度 = {data['total_kwh']*1000} Wh")
    print("  分日明细(用于趋势图):")
    if data["daily"]:
        for d in data["daily"]:
            print(f"    {d['date']}: {d['kwh']} 度")
    else:
        print("    暂无数据")


def get_all_today() -> Dict:
    """获取所有设备今日用电数据(汇总版,供前端使用)"""
    today = datetime.now().strftime("%Y-%m-%d")
    total_kwh = 0.0
    hourly_list = [{"hour": h, "kwh": 0.0} for h in range(24)]
    records = []
    
    for device_id in config.DEVICES.keys():
        data = get_today_energy(device_id)
        total_kwh += data.get("total_kwh", 0)
        
        for h in data.get("hourly", []):
            hour_idx = h.get("hour", 0)
            if 0 <= hour_idx < 24:
                hourly_list[hour_idx]["kwh"] += h.get("kwh", 0)
    
    max_kwh = max([h["kwh"] for h in hourly_list] + [1])
    
    conn = _get_db()
    try:
        recent_rows = conn.execute(
            "SELECT recorded_at, energy_wh FROM energy_records "
            "WHERE record_date=? ORDER BY recorded_at DESC LIMIT 50",
            (today,),
        ).fetchall()
        for time_str, wh in recent_rows:
            records.append({"time": time_str.split()[1], "power": round(wh / 1000.0, 2)})
    finally:
        conn.close()
    
    return {
        "date": today,
        "total_kwh": round(total_kwh, 4),
        "hourly": hourly_list,
        "max_kwh": max_kwh,
        "records": records,
    }


def get_all_month() -> Dict:
    """获取所有设备本月用电数据(汇总版,供前端使用)"""
    now = datetime.now()
    month_str = now.strftime("%Y-%m")
    total_kwh = 0.0
    daily_list = []

    start_date = now.replace(day=1)
    cur = start_date
    while cur <= now:
        date_str = cur.strftime("%Y-%m-%d")
        daily_list.append({"date": date_str, "dateShort": date_str.split("-")[2], "kwh": 0.0})
        cur += timedelta(days=1)

    for device_id in config.DEVICES.keys():
        data = get_month_energy(device_id)
        total_kwh += data.get("total_kwh", 0)

        for d in data.get("daily", []):
            for day_item in daily_list:
                if day_item["date"] == d.get("date"):
                    day_item["kwh"] += d.get("kwh", 0)
                    break

    max_kwh = max([d["kwh"] for d in daily_list] + [1])

    return {
        "month": month_str,
        "total_kwh": round(total_kwh, 4),
        "daily": daily_list,
        "max_kwh": max_kwh,
    }


def get_today_for_device(device_id: str) -> Dict:
    """获取单个设备今日用电(格式和汇总版一致,供前端切换设备用)"""
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_today_energy(device_id)

    hourly_list = [{"hour": h, "kwh": 0.0} for h in range(24)]
    for h in data.get("hourly", []):
        hour_idx = h.get("hour", 0)
        if 0 <= hour_idx < 24:
            hourly_list[hour_idx]["kwh"] = h.get("kwh", 0)

    max_kwh = max([h["kwh"] for h in hourly_list] + [1])

    records = []
    conn = _get_db()
    try:
        recent_rows = conn.execute(
            "SELECT recorded_at, energy_wh FROM energy_records "
            "WHERE device_id=? AND record_date=? ORDER BY recorded_at DESC LIMIT 50",
            (device_id, today),
        ).fetchall()
        for time_str, wh in recent_rows:
            records.append({"time": time_str.split()[1], "power": round(wh / 1000.0, 2)})
    finally:
        conn.close()

    return {
        "date": today,
        "total_kwh": data["total_kwh"],
        "hourly": hourly_list,
        "max_kwh": max_kwh,
        "records": records,
    }


def get_month_for_device(device_id: str) -> Dict:
    """获取单个设备本月用电(格式和汇总版一致,供前端切换设备用)"""
    now = datetime.now()
    month_str = now.strftime("%Y-%m")
    data = get_month_energy(device_id)

    daily_list = []
    start_date = now.replace(day=1)
    cur = start_date
    while cur <= now:
        date_str = cur.strftime("%Y-%m-%d")
        daily_list.append({"date": date_str, "dateShort": date_str.split("-")[2], "kwh": 0.0})
        cur += timedelta(days=1)

    for d in data.get("daily", []):
        for day_item in daily_list:
            if day_item["date"] == d.get("date"):
                day_item["kwh"] = d.get("kwh", 0)
                break

    max_kwh = max([d["kwh"] for d in daily_list] + [1])

    return {
        "month": month_str,
        "total_kwh": data["total_kwh"],
        "daily": daily_list,
        "max_kwh": max_kwh,
    }


def print_all_today() -> None:
    for did in config.DEVICES.keys():
        print_today(did)


def print_all_month() -> None:
    for did in config.DEVICES.keys():
        print_month(did)


def get_recent_records(limit=100):
    """获取最近的记录（供习惯分析用）"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT recorded_at, energy_wh, power_w, device_id "
            "FROM energy_records ORDER BY recorded_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"recorded_at": r[0], "energy_wh": r[1], "power_w": r[2], "device_id": r[3]}
            for r in rows
        ]
    finally:
        conn.close()


def get_records_by_date_range(device_id: str, start_date: str, end_date: str) -> List[Dict]:
    """
    获取指定日期范围内的记录。
    device_id为空则查询所有设备。
    返回: [{record_date, recorded_at, device_id, power_w, energy_wh, voltage_v, current_ma}, ...]
    """
    conn = _get_db()
    try:
        if device_id:
            rows = conn.execute(
                "SELECT record_date, recorded_at, device_id, power_w, energy_wh "
                "FROM energy_records WHERE device_id=? AND record_date BETWEEN ? AND ? "
                "ORDER BY recorded_at ASC",
                (device_id, start_date, end_date),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT record_date, recorded_at, device_id, power_w, energy_wh "
                "FROM energy_records WHERE record_date BETWEEN ? AND ? "
                "ORDER BY recorded_at ASC",
                (start_date, end_date),
            ).fetchall()
        
        return [
            {
                "record_date": r[0],
                "recorded_at": r[1],
                "device_id": r[2],
                "power_w": r[3],
                "energy_wh": r[4],
                "voltage_v": "",
                "current_ma": "",
            }
            for r in rows
        ]
    finally:
        conn.close()


# ============ 后台持续记录线程 ============
import threading
import time as _time  # 注意：上面已经 import time，这里用别名避免冲突

_background_thread = None
_background_running = False


def _background_recorder_loop():
    """后台 daemon 线程：每10分钟自动采集一次用电数据。
    即使用户关闭网页、退出登录，服务仍在运行就会持续记录。
    """
    global _background_running
    print("[后台记录] 用电数据后台采集线程已启动（每10分钟采集一次）")
    while _background_running:
        try:
            print(f"[后台记录] 开始采集...")
            results = record_all()
            count = sum(1 for r in results if r.get("ok"))
            print(f"[后台记录] 采集完成：{count}/{len(results)} 个设备成功")
        except Exception as e:
            print(f"[后台记录] 采集出错: {e}")
        # 休眠10分钟，但每秒检查一次是否需要退出
        for _ in range(600):
            if not _background_running:
                break
            _time.sleep(1)


def start_background_recorder():
    """启动后台采集线程（web_server 启动时调用）。"""
    global _background_thread, _background_running
    if _background_running:
        print("[后台记录] 已在运行中，跳过")
        return
    _background_running = True
    _background_thread = threading.Thread(target=_background_recorder_loop, daemon=True)
    _background_thread.start()
    print("[后台记录] 后台采集线程已启动")


def stop_background_recorder():
    """停止后台采集线程。"""
    global _background_running
    _background_running = False
    print("[后台记录] 后台采集线程已停止")


# ============ 命令行入口(可单独跑) ============
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法:")
        print("  python energy_history.py record  # 采集一次")
        print("  python energy_history.py show    # 打印今日+本月")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "record":
        results = record_all()
        for r in results:
            if r.get("ok"):
                print(f"[{r['device_id']}] 记录成功: {r['energy_wh']} Wh @ {r['recorded_at']}")
            else:
                print(f"[{r['device_id']}] 记录失败: {r.get('msg')}")
    elif cmd == "show":
        print_all_today()
        print_all_month()
    else:
        print(f"未知命令: {cmd}")
