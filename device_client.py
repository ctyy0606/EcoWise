"""
EcoWise 宿舍助理 - 涂鸦云 API 封装
================================
只负责一件事:从涂鸦云拉取插座实时数据,并做单位换算。
"""
from typing import Optional, Dict
from datetime import datetime
import time

from tuya_connector import TuyaOpenAPI

import config


_openapi: Optional[TuyaOpenAPI] = None

# 设备数据缓存，按设备ID存储，避免频繁调用涂鸦云 API
# 格式: { device_id: { "data": {...}, "timestamp": float } }
_device_cache: Dict[str, Dict] = {}
CACHE_TTL_SECONDS: float = 3.0


def _get_openapi() -> TuyaOpenAPI:
    """获取(或首次创建)涂鸦云连接对象。"""
    global _openapi
    if _openapi is None:
        _openapi = TuyaOpenAPI(
            config.API_ENDPOINT,
            config.ACCESS_ID,
            config.ACCESS_SECRET,
        )
        _openapi.connect()
        print("[涂鸦云] 已建立连接")
    return _openapi


def get_device_data(device_id: str) -> Dict:
    """
    拉取单个插座的实时数据,做单位换算后返回。

    返回字段:
        device_id, device_name, owner,
        online, switch_on,
        power_w, voltage_v, current_ma,
        energy_kwh, energy_wh,
        light_level,  # 光敏传感器数值(0-100, 0=黑暗, 100=明亮)
        timestamp
    """
    if config.TEST_MODE:
        dev_meta = config.DEVICES.get(device_id, {})
        print(f"[测试模式] 返回模拟数据: 功率={config.TEST_POWER_W}W")
        hour = datetime.now().hour
        if 22 <= hour or hour < 6:
            light_level = 5
        elif 6 <= hour < 8 or 18 <= hour < 22:
            light_level = 50
        else:
            light_level = 90
        
        violation_threshold = config.VIOLATION_THRESHOLDS.get("violation_watts", 800)
        is_over_threshold = config.TEST_POWER_W >= violation_threshold
        
        is_switched_on = True
        if is_over_threshold:
            try:
                import auto_power_off
                event = auto_power_off.get_latest_event(device_id)
                if event and not event.get('is_released', True):
                    is_switched_on = False
            except:
                pass
        
        if not hasattr(config, '_test_start_time'):
            config._test_start_time = datetime.now()
        
        test_duration_minutes = (datetime.now() - config._test_start_time).total_seconds() / 60
        
        base_energy_wh = 5000.0
        energy_wh = base_energy_wh + (config.TEST_POWER_W / 60) * test_duration_minutes
        
        return {
            "device_id": device_id,
            "device_name": dev_meta.get("name", f"未命名设备-{device_id[:6]}"),
            "owner": dev_meta.get("owner", "未知"),
            "group": dev_meta.get("group", "未分组"),
            "online": True,
            "switch_on": is_switched_on,
            "power_w": config.TEST_POWER_W,
            "voltage_v": 220.0,
            "current_ma": int(config.TEST_POWER_W / 220 * 1000),
            "energy_kwh": round(energy_wh / 1000.0, 3),
            "energy_wh": energy_wh,
            "light_level": getattr(config, 'TEST_LIGHT_LEVEL', 100),
            "temperature_c": getattr(config, 'TEST_TEMPERATURE_C', 25),
            "humidity_percent": getattr(config, 'TEST_HUMIDITY_PERCENT', 60),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "test_mode": True,
        }

    api = _get_openapi()

    # 1) 先查设备信息(判断在线状态)
    info_resp = api.get(f"/v1.0/devices/{device_id}")
    info = info_resp.get("result", {}) or {}
    is_online = info.get("online", False)
    print(f"[device_client] 设备 {device_id} 在线状态: {is_online}, 设备信息: {info}")

    # 2) 拉取设备 DP 状态
    status_resp = api.get(f"/v1.0/devices/{device_id}/status")
    result = status_resp.get("result", []) or []
    print(f"[device_client] 设备 {device_id} DP状态: {result}")

    power_raw = None
    add_ele_raw = None
    voltage_raw = None
    current_raw = None
    switch_on = None
    light_level = None
    temperature_c = None
    humidity_percent = None

    dp_code_mapping = {
        "101": "add_ele",
        "102": "cur_current",
        "103": "cur_power",
        "104": "cur_voltage",
        "105": "switch_1",
        "106": "temp",
        "107": "lux",
        "108": "humidity",
        "201": "temp",
        "202": "cur_power",
        "203": "lux",
        "204": "humidity",
        "add_ele": "add_ele",
        "cur_current": "cur_current",
        "cur_power": "cur_power",
        "cur_voltage": "cur_voltage",
        "switch_1": "switch_1",
        "temp": "temp",
        "temperature": "temp",
        "lux": "lux",
        "light": "lux",
        "humidity": "humidity",
        "hum": "humidity",
        "switch": "switch_1",
    }

    for item in result:
        code = item.get("code")
        value = item.get("value")
        mapped_code = dp_code_mapping.get(str(code), str(code))
        
        if mapped_code == "cur_power":
            power_raw = value
        elif mapped_code == "add_ele":
            add_ele_raw = value
        elif mapped_code == "cur_voltage":
            voltage_raw = value
        elif mapped_code == "cur_current":
            current_raw = value
        elif mapped_code == "switch_1":
            switch_on = bool(value)
        elif mapped_code == "lux":
            light_level = value
        elif mapped_code == "temp":
            temperature_c = value
        elif mapped_code == "humidity":
            humidity_percent = value

    energy_kwh = add_ele_raw if add_ele_raw is not None else None
    energy_wh = energy_kwh * 1000.0 if energy_kwh is not None else None
    power_w = (power_raw * 0.1) if power_raw is not None else None
    voltage_v = (voltage_raw * 0.1) if voltage_raw is not None else None
    current_ma = current_raw

    dev_meta = config.DEVICES.get(device_id, {})

    group = dev_meta.get("group", "未分组")
    name = dev_meta.get("name", "")
    is_sensor = (
        device_id in config.DEVICE_PAIRINGS.values()
        or group == "传感器设备"
        or "开发板" in name
        or "sensor" in name.lower()
    )

    return {
        "device_id": device_id,
        "device_name": name or f"未命名设备-{device_id[:6]}",
        "owner": dev_meta.get("owner", "未知"),
        "group": group,
        "is_sensor": is_sensor,
        "online": is_online,
        "switch_on": switch_on,
        "power_w": round(power_w, 1) if power_w is not None else None,
        "voltage_v": round(voltage_v, 1) if voltage_v is not None else None,
        "current_ma": current_ma,
        "energy_kwh": energy_kwh,
        "energy_wh": energy_wh,
        "light_level": light_level,
        "temperature_c": temperature_c,
        "humidity_percent": humidity_percent,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _get_sensor_data(device_id: str) -> Dict:
    """获取开发板的传感器数据（光照、温度、湿度）"""
    if config.TEST_MODE:
        return {
            "light_level": getattr(config, 'TEST_LIGHT_LEVEL', 100),
            "temperature_c": getattr(config, 'TEST_TEMPERATURE_C', 25),
            "humidity_percent": getattr(config, 'TEST_HUMIDITY_PERCENT', 60),
        }
    
    try:
        api = _get_openapi()
        status_resp = api.get(f"/v1.0/devices/{device_id}/status")
        result = status_resp.get("result", []) or []
        
        light_level = None
        temperature_c = None
        humidity_percent = None
        
        dp_code_mapping = {
            "107": "lux",
            "203": "lux",
            "lux": "lux",
            "light": "lux",
            "106": "temp",
            "201": "temp",
            "temp": "temp",
            "temperature": "temp",
            "108": "humidity",
            "204": "humidity",
            "humidity": "humidity",
            "hum": "humidity",
        }
        
        for item in result:
            code = item.get("code")
            value = item.get("value")
            mapped_code = dp_code_mapping.get(str(code), str(code))
            
            if mapped_code == "lux":
                light_level = value
            elif mapped_code == "temp":
                temperature_c = value
            elif mapped_code == "humidity":
                humidity_percent = value
        
        return {
            "light_level": light_level,
            "temperature_c": temperature_c,
            "humidity_percent": humidity_percent,
        }
    except Exception as e:
        print(f"[device_client] 获取开发板 {device_id} 传感器数据失败: {e}")
        return {
            "light_level": None,
            "temperature_c": None,
            "humidity_percent": None,
        }


def get_all_devices(include_paired_boards=True) -> list:
    """一次性拉取所有设备数据,单个失败不影响整体。
    已配对的开发板数据会合并到对应的插座中显示。
    include_paired_boards=True 时，已配对的开发板也会作为独立设备显示在列表中。
    非测试模式下启用 3 秒缓存，避免频繁调用涂鸦云 API 导致页面卡顿。
    """
    global _device_cache

    now = time.time()
    results = []
    paired_devices = set(config.DEVICE_PAIRINGS.values())

    for device_id in config.DEVICES.keys():
        if device_id in paired_devices and not include_paired_boards:
            continue

        try:
            if not config.TEST_MODE and device_id in _device_cache:
                cache_entry = _device_cache[device_id]
                if (now - cache_entry.get("timestamp", 0)) < CACHE_TTL_SECONDS:
                    device_data = cache_entry.get("data", {})
                else:
                    device_data = get_device_data(device_id)
                    _device_cache[device_id] = {"data": device_data, "timestamp": now}
            else:
                device_data = get_device_data(device_id)
                if not config.TEST_MODE:
                    _device_cache[device_id] = {"data": device_data, "timestamp": now}

            device_data["owner"] = config.DEVICES[device_id].get("owner", "未知")

            paired_board_id = config.DEVICE_PAIRINGS.get(device_id)
            if paired_board_id and paired_board_id in config.DEVICES:
                sensor_data = _get_sensor_data(paired_board_id)
                device_data["light_level"] = sensor_data.get("light_level")
                device_data["temperature_c"] = sensor_data.get("temperature_c")
                device_data["humidity_percent"] = sensor_data.get("humidity_percent")

            is_paired_as_board = device_id in paired_devices
            if is_paired_as_board:
                device_data["paired_with"] = None
                for socket_id, board_id in config.DEVICE_PAIRINGS.items():
                    if board_id == device_id:
                        device_data["paired_with"] = socket_id
                        break
            device_data["is_paired_board"] = is_paired_as_board

            results.append(device_data)
        except Exception as e:
            results.append({
                "device_id": device_id,
                "device_name": config.DEVICES[device_id].get("name", device_id),
                "owner": config.DEVICES[device_id].get("owner", "未知"),
                "online": False,
                "error": str(e),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "is_paired_board": device_id in paired_devices,
            })

    return results


def control_device_switch(device_id: str, on: bool) -> dict:
    """
    控制插座开关(下发 105 命令)。
    on=True 开, on=False 关。
    返回涂鸦云响应。
    """
    api = _get_openapi()
    payload = {"commands": [{"code": "105", "value": bool(on)}]}
    resp = api.post(f"/v1.0/devices/{device_id}/commands", payload)
    if not resp.get("success", False):
        raise RuntimeError(f"涂鸦云返回失败: {resp}")
    return resp


def send_alert_to_board(board_device_id: str, alert_level: str) -> dict:
    """
    向T5开发板发送告警控制命令，控制LED和蜂鸣器报警。
    
    参数:
        board_device_id: 开发板设备ID
        alert_level: 告警级别 ("none", "danger", "critical", "recovery")
    
    告警逻辑：
    - none: 正常状态 → 绿灯常亮，蜂鸣器关闭
    - danger: 温湿度异常（温度>35或<5，湿度>80或<20）→ 红灯常亮，蜂鸣器不响
    - critical: 功率>800W → 红灯常亮，蜂鸣器响
    - recovery: 恢复正常 → 绿灯闪烁1秒后恢复常亮，蜂鸣器关闭
    
    硬件同学需要在T5开发板固件中实现以下DP点的处理：
    - 109 (led_control): 枚举值，控制LED状态
    - 110 (buzzer_control): 枚举值，控制蜂鸣器
    - 111 (alert_level): 数值，告警级别
    """
    if alert_level not in config.ALERT_LEVELS:
        raise ValueError(f"无效的告警级别: {alert_level}")
    
    level_value = config.ALERT_LEVELS[alert_level]
    
    if alert_level == "none":
        led_state = config.LED_STATES["solid_green"]
        buzzer_state = config.BUZZER_STATES["off"]
    elif alert_level == "danger":
        led_state = config.LED_STATES["solid_red"]
        buzzer_state = config.BUZZER_STATES["off"]
    elif alert_level == "critical":
        led_state = config.LED_STATES["solid_red"]
        buzzer_state = config.BUZZER_STATES["beep_danger"]
    elif alert_level == "recovery":
        led_state = config.LED_STATES["blink_green"]
        buzzer_state = config.BUZZER_STATES["off"]
    else:
        led_state = config.LED_STATES["solid_green"]
        buzzer_state = config.BUZZER_STATES["off"]
    
    api = _get_openapi()
    
    payload = {
        "commands": [
            {"code": config.BOARD_ALERT_DP["led_control"], "value": led_state},
            {"code": config.BOARD_ALERT_DP["buzzer_control"], "value": buzzer_state},
            {"code": config.BOARD_ALERT_DP["alert_level"], "value": level_value},
        ]
    }
    
    resp = api.post(f"/v1.0/devices/{board_device_id}/commands", payload)
    
    if resp.get("success", False):
        print(f"[设备控制] 成功向开发板 {board_device_id} 发送告警命令: {alert_level}")
        return resp
    else:
        print(f"[设备控制] 向开发板 {board_device_id} 发送告警命令失败: {resp}")
        raise RuntimeError(f"涂鸦云返回失败: {resp}")


def send_board_alert_by_socket(socket_device_id: str, alert_level: str) -> dict:
    """
    通过插座设备ID找到配对的开发板，并发送告警命令。
    
    参数:
        socket_device_id: 插座设备ID
        alert_level: 告警级别
    
    返回:
        涂鸦云响应（如果找到配对开发板）或 None（未找到配对）
    """
    paired_board_id = config.DEVICE_PAIRINGS.get(socket_device_id)
    
    if not paired_board_id:
        print(f"[设备控制] 插座 {socket_device_id} 未配对开发板，无法发送硬件告警")
        return None
    
    if paired_board_id not in config.DEVICES:
        print(f"[设备控制] 配对的开发板 {paired_board_id} 未在配置中，无法发送硬件告警")
        return None
    
    try:
        return send_alert_to_board(paired_board_id, alert_level)
    except Exception as e:
        print(f"[设备控制] 向配对开发板 {paired_board_id} 发送告警失败: {e}")
        return None
