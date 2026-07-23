"""
EcoWise 宿舍助理 - 智能断电策略
================================
分级响应：根据时段和功率采取不同动作。
- 深夜(23:00-6:00) 功率<50W → allow（正常充电）
- 功率>800W → power_off（违规断电，调 auto_power_off）
- 深夜 功率>200W → warn（熬夜告警通知，不断电）
- 其他 → allow

被 violation_detector.detect_all_devices() 在每次轮询时调用。
"""
from datetime import datetime

import config


def _is_late_night():
    """判断当前是否在深夜时段（23:00-6:00）"""
    hour = datetime.now().hour
    start = config.SMART_POLICY["late_night_start"]
    end = config.SMART_POLICY["late_night_end"]
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end


def _get_owner_phone(device_id):
    """通过设备 owner nickname 反查用户手机号"""
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


def evaluate_action(device_id, device_name, power_w, user_phone=None):
    """
    评估当前功率应采取的动作。

    返回:
        {
            "action": "allow" | "warn" | "power_off",
            "reason": str,
            "is_late_night": bool,
        }

    动作说明：
    - allow: 正常，不做任何处理
    - warn: 发送熬夜告警通知（不断电）
    - power_off: 调用 auto_power_off 触发断电
    """
    late_night = _is_late_night()
    violation_threshold = config.VIOLATION_THRESHOLDS["violation_watts"]
    late_warning = config.SMART_POLICY["late_night_warning_watts"]
    late_charging = config.SMART_POLICY["late_night_charging_watts"]

    if power_w is None or power_w <= 0:
        return {"action": "allow", "reason": "无功率数据", "is_late_night": late_night}

    # 1. 违规断电（任何时段，功率>800W）
    if power_w > violation_threshold:
        result = {"action": "power_off", "reason": f"功率{power_w}W超过违规红线{violation_threshold}W", "is_late_night": late_night}
        try:
            import auto_power_off
            auto_power_off.trigger_power_off(device_id, device_name, power_w)
        except Exception as e:
            print(f"[智能策略] 触发断电失败 device={device_id}: {e}")
        return result

    # 2. 深夜告警（23:00-6:00，功率>200W）
    if late_night and power_w > late_warning:
        if not user_phone:
            user_phone = _get_owner_phone(device_id)
        if user_phone:
            try:
                import notification
                notification.check_and_notify_late_night(user_phone, device_id, device_name, power_w)
            except Exception as e:
                print(f"[智能策略] 熬夜告警失败 device={device_id}: {e}")
        return {"action": "warn", "reason": f"深夜时段功率{power_w}W超过{late_warning}W告警线", "is_late_night": True}

    # 3. 深夜正常充电（功率<50W）
    if late_night and power_w <= late_charging:
        return {"action": "allow", "reason": "深夜正常充电", "is_late_night": True}

    # 4. 其他情况放行
    return {"action": "allow", "reason": "功率正常", "is_late_night": late_night}


def get_policy_status():
    """获取当前策略参数和时段状态（供 API 展示）"""
    return {
        "is_late_night": _is_late_night(),
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "policy": {
            "late_night_start": config.SMART_POLICY["late_night_start"],
            "late_night_end": config.SMART_POLICY["late_night_end"],
            "late_night_charging_watts": config.SMART_POLICY["late_night_charging_watts"],
            "late_night_warning_watts": config.SMART_POLICY["late_night_warning_watts"],
            "violation_watts": config.VIOLATION_THRESHOLDS["violation_watts"],
        },
    }
