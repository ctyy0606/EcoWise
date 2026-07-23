"""
EcoWise 宿舍助理 - 违规电器检测
================================
L0: 功率阈值法（瞬时检测，用于实时显示）
L1: 持续时间检测（功率>800W持续5分钟 = 一次违规事件，用于历史记录）

违规事件判定逻辑：
- 每次 /api/devices 轮询（约10秒）时记录功率状态
- 功率 > 800W → 开启/继续违规事件
- 功率 <= 800W → 结束违规事件，若持续>=5分钟则确认为违规，否则丢弃
- 5分钟阈值参考 NETIO 功率看门狗工业标准
"""
import os
import sqlite3
from typing import Dict, List
from datetime import datetime, timedelta

import config


# 违规事件持续阈值（分钟）：持续高功率超过此时间才算一次违规
VIOLATION_DURATION_MINUTES = 5

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "energy_log.db")


def _get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS violation_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT NOT NULL,
            device_name TEXT,
            start_time  TEXT NOT NULL,
            last_seen   TEXT NOT NULL,
            end_time    TEXT,
            peak_power  REAL DEFAULT 0,
            is_confirmed INTEGER DEFAULT 0,
            record_date TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_viol_date ON violation_events(record_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_viol_dev ON violation_events(device_id, is_confirmed)")
    conn.commit()
    return conn


def _guess_appliance(power_w, duration_minutes=None):
    """根据功率猜测电器类型（L1-7：改用 appliance_fingerprint 指纹识别）"""
    try:
        import appliance_fingerprint
        result = appliance_fingerprint.identify_appliance(power_w, duration_minutes)
        return result["appliance"]
    except Exception:
        # 降级到原逻辑
        if power_w is None:
            return "未知电器"
        if power_w < 150:
            return "疑似电热毯"
        if power_w < 800:
            return "大功率电器"
        if power_w < 1500:
            return "疑似热得快"
        if power_w < 2200:
            return "疑似电水壶"
        return "超大功率电器"


def check_and_record(device_id: str, device_name: str, power_w):
    """
    每次轮询设备数据时调用，跟踪违规状态。
    power_w > 800W: 开始或继续记录违规事件
    power_w <= 800W: 结束当前违规事件，判断是否达到5分钟阈值
    """
    if not device_id:
        return

    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    today = now.strftime("%Y-%m-%d")
    threshold = config.VIOLATION_THRESHOLDS["violation_watts"]

    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, start_time, last_seen, peak_power, is_confirmed FROM violation_events "
            "WHERE device_id=? AND end_time IS NULL",
            (device_id,),
        ).fetchone()

        if power_w is not None and power_w > threshold:
            if row:
                peak = max(row[3] or 0, power_w)
                conn.execute(
                    "UPDATE violation_events SET last_seen=?, peak_power=?, device_name=? WHERE id=?",
                    (now_str, peak, device_name, row[0]),
                )
            else:
                conn.execute(
                    "INSERT INTO violation_events (device_id, device_name, start_time, last_seen, peak_power, is_confirmed, record_date) "
                    "VALUES (?, ?, ?, ?, ?, 0, ?)",
                    (device_id, device_name, now_str, now_str, power_w, today),
                )
        else:
            if row:
                start_time = datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S")
                last_seen = datetime.strptime(row[2], "%Y-%m-%d %H:%M:%S")
                duration_min = (last_seen - start_time).total_seconds() / 60.0

                if duration_min >= VIOLATION_DURATION_MINUTES:
                    conn.execute(
                        "UPDATE violation_events SET end_time=?, is_confirmed=1 WHERE id=?",
                        (now_str, row[0]),
                    )
                    try:
                        import notification
                        phone = session.get('phone') if 'session' in dir() else None
                        if phone:
                            notification.create_notification(
                                phone, "violation_auto_off",
                                "违规用电告警",
                                f"{device_name} 检测到功率 {row[3]}W，已自动断电，请及时处理！"
                            )
                    except Exception as e:
                        print(f"[通知] 发送失败: {e}")
                    try:
                        import auto_power_off
                        auto_power_off.trigger_power_off(device_id, device_name, row[3])
                    except Exception as e:
                        print(f"[自动断电] 触发失败 device={device_id}: {e}")
                else:
                    conn.execute("DELETE FROM violation_events WHERE id=?", (row[0],))

        stale_threshold = (now - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
        stale_rows = conn.execute(
            "SELECT id, device_id, device_name, start_time, last_seen, peak_power FROM violation_events "
            "WHERE end_time IS NULL AND last_seen < ?",
            (stale_threshold,),
        ).fetchall()
        for sr in stale_rows:
            sr_id, sr_dev_id, sr_dev_name = sr[0], sr[1], sr[2]
            start = datetime.strptime(sr[3], "%Y-%m-%d %H:%M:%S")
            last = datetime.strptime(sr[4], "%Y-%m-%d %H:%M:%S")
            sr_peak = sr[5]
            dur = (last - start).total_seconds() / 60.0
            if dur >= VIOLATION_DURATION_MINUTES:
                conn.execute(
                    "UPDATE violation_events SET end_time=?, is_confirmed=1 WHERE id=?",
                    (sr[4], sr_id),
                )
                try:
                    import auto_power_off
                    auto_power_off.trigger_power_off(sr_dev_id, sr_dev_name, sr_peak)
                except Exception as e:
                    print(f"[自动断电] stale触发失败 device={sr_dev_id}: {e}")
            else:
                conn.execute("DELETE FROM violation_events WHERE id=?", (sr_id,))

        conn.commit()
    finally:
        conn.close()


def get_today_violations(device_id: str = None) -> List[Dict]:
    """获取今日所有违规事件（包括正在进行中的和已确认的）"""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_db()
    try:
        if device_id:
            rows = conn.execute(
                "SELECT device_id, device_name, start_time, end_time, peak_power, is_confirmed "
                "FROM violation_events WHERE record_date=? AND device_id=? "
                "ORDER BY start_time ASC",
                (today, device_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT device_id, device_name, start_time, end_time, peak_power, is_confirmed "
                "FROM violation_events WHERE record_date=? "
                "ORDER BY start_time ASC",
                (today,),
            ).fetchall()
    finally:
        conn.close()

    results = []
    for dev_id, dev_name, start, end, peak, is_confirmed in rows:
        start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        if end:
            end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
            duration = (end_dt - start_dt).total_seconds() / 60.0
        else:
            end_dt = datetime.now()
            duration = (end_dt - start_dt).total_seconds() / 60.0
        results.append({
            "device_name": dev_name or dev_id,
            "time": start_dt.strftime("%H:%M"),
            "start_time": start,
            "end_time": end,
            "duration_minutes": round(duration, 1),
            "peak_power": round(peak, 1) if peak else 0,
            "appliance": _guess_appliance(peak),
            "is_confirmed": bool(is_confirmed),
        })
    return results


def get_month_violations(device_id: str = None) -> List[Dict]:
    """获取本月所有违规事件（包括正在进行中的和已确认的），按日期汇总"""
    month_str = datetime.now().strftime("%Y-%m")
    conn = _get_db()
    try:
        if device_id:
            rows = conn.execute(
                "SELECT device_id, device_name, start_time, end_time, peak_power, record_date, is_confirmed "
                "FROM violation_events WHERE record_date LIKE ? AND device_id=? "
                "ORDER BY start_time ASC",
                (f"{month_str}-%", device_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT device_id, device_name, start_time, end_time, peak_power, record_date, is_confirmed "
                "FROM violation_events WHERE record_date LIKE ? "
                "ORDER BY start_time ASC",
                (f"{month_str}-%",),
            ).fetchall()
    finally:
        conn.close()

    daily_map = {}
    for dev_id, dev_name, start, end, peak, date, is_confirmed in rows:
        if date not in daily_map:
            daily_map[date] = {"date": date, "dateShort": date.split("-")[2], "count": 0, "devices": []}
        daily_map[date]["count"] += 1
        start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        daily_map[date]["devices"].append({
            "device_name": dev_name or dev_id,
            "time": start_dt.strftime("%H:%M"),
            "peak_power": round(peak, 1) if peak else 0,
            "appliance": _guess_appliance(peak),
            "is_confirmed": bool(is_confirmed),
        })

    return list(daily_map.values())


def get_month_violation_count(device_id: str = None) -> int:
    """获取本月违规总次数（可按设备过滤）"""
    month_str = datetime.now().strftime("%Y-%m")
    conn = _get_db()
    try:
        if device_id:
            row = conn.execute(
                "SELECT COUNT(*) FROM violation_events WHERE record_date LIKE ? AND device_id=?",
                (f"{month_str}-%", device_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM violation_events WHERE record_date LIKE ?",
                (f"{month_str}-%",),
            ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


# ============ 瞬时检测（保留给实时显示用） ============
def check_power_violation(power_w) -> Dict:
    """根据当前功率判断是否有违规/预警。"""
    if power_w is None:
        return {
            "level": "normal",
            "title": "数据缺失",
            "message": "当前无法获取功率数据，可能设备离线。",
            "power_w": None,
        }

    if power_w > config.VIOLATION_THRESHOLDS["violation_watts"]:
        return {
            "level": "violation",
            "title": "违规电器告警",
            "message": (
                f"当前功率 {power_w}W，超过 {config.VIOLATION_THRESHOLDS['violation_watts']}W 红线，"
                f"疑似使用{_guess_appliance(power_w)}，请立即关闭！"
            ),
            "power_w": power_w,
        }

    if power_w > config.VIOLATION_THRESHOLDS["warning_watts"]:
        return {
            "level": "warning",
            "title": "限电预警",
            "message": (
                f"当前功率 {power_w}W，接近宿舍限电阈值，"
                f"建议关闭部分大功率设备，避免跳闸。"
            ),
            "power_w": power_w,
        }

    return {
        "level": "normal",
        "title": "用电正常",
        "message": f"当前功率 {power_w}W，处于安全范围。",
        "power_w": power_w,
    }


_last_alert_states = {}


def detect_all_devices(devices_data: List[Dict]) -> List[Dict]:
    """对所有插座批量做违规检测，同时记录违规状态用于历史追踪。"""
    results = []
    for dev in devices_data:
        if "error" in dev:
            results.append(dev)
            continue
        dev_id = dev.get("device_id", "")
        dev_name = dev.get("device_name", "")
        power = dev.get("power_w")
        temperature = dev.get("temperature_c")
        humidity = dev.get("humidity_percent")
        
        if dev.get("online") and (dev.get("switch_on") or dev.get("test_mode")):
            check_and_record(dev_id, dev_name, power)
            violation = check_power_violation(power)
            
            try:
                import smart_power_policy
                smart_power_policy.evaluate_action(dev_id, dev_name, power)
            except Exception as e:
                print(f"[智能策略] 评估失败 device={dev_id}: {e}")
            
            try:
                alert_level = _determine_alert_level(power, temperature, humidity)
                _send_alert_with_recovery(dev_id, alert_level)
            except Exception as e:
                print(f"[硬件告警] 发送失败 device={dev_id}: {e}")
        else:
            check_and_record(dev_id, dev_name, 0)
            violation = check_power_violation(None)
            
            try:
                _send_alert_with_recovery(dev_id, "none")
            except Exception as e:
                print(f"[硬件告警] 恢复正常失败 device={dev_id}: {e}")
        dev_with_violation = {**dev, "violation": violation}
        results.append(dev_with_violation)
    return results


def _determine_alert_level(power_w, temperature_c, humidity_percent) -> str:
    """根据功率、温度、湿度确定告警级别
    
    告警规则：
    - power > 800W → critical（红灯+蜂鸣器响）
    - temperature > 35°C 或 < 5°C → danger（红灯，蜂鸣器不响）
    - humidity > 80% 或 < 20% → danger（红灯，蜂鸣器不响）
    - 其他 → none（绿灯常亮）
    """
    temp_high = config.ENV_THRESHOLDS.get("temperature_high", 35)
    temp_low = config.ENV_THRESHOLDS.get("temperature_low", 5)
    hum_high = config.ENV_THRESHOLDS.get("humidity_high", 80)
    hum_low = config.ENV_THRESHOLDS.get("humidity_low", 20)
    power_danger = config.VIOLATION_THRESHOLDS.get("violation_watts", 800)
    
    if power_w is not None and power_w >= power_danger:
        return "critical"
    
    if temperature_c is not None and (temperature_c >= temp_high or temperature_c <= temp_low):
        return "danger"
    
    if humidity_percent is not None and (humidity_percent >= hum_high or humidity_percent <= hum_low):
        return "danger"
    
    return "none"


def _send_alert_with_recovery(device_id, alert_level):
    """发送告警命令，处理恢复提示逻辑
    
    当设备从告警状态恢复到正常状态时，先发送recovery（绿灯闪烁），然后发送none（绿灯常亮）
    """
    import device_client
    
    last_state = _last_alert_states.get(device_id)
    
    if alert_level == "none" and last_state in ("danger", "critical"):
        device_client.send_board_alert_by_socket(device_id, "recovery")
        import time
        time.sleep(1)
    
    device_client.send_board_alert_by_socket(device_id, alert_level)
    _last_alert_states[device_id] = alert_level
