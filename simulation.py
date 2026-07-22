"""
EcoWise 宿舍助理 - 模拟模式后端逻辑
=====================================
提供模拟模式开关、预设场景、自定义数据注入。
前端可在不连接真实涂鸦设备的情况下，模拟各种设备状态进行测试。

使用方式:
    from simulation import register_simulation_routes
    register_simulation_routes(app)
"""
from flask import jsonify, request


# ============ 1. 全局状态 ============
SIMULATION_ENABLED = False
_current_scene = "未设置"
_current_data = None  # 自定义模拟数据（dict）


# ============ 2. LED 颜色枚举 ============
# green : 正常
# yellow: 预警
# red   : 违规/告警
# off   : 熄灭
LED_COLORS = {
    "green": "正常",
    "yellow": "预警",
    "red": "违规/告警",
    "off": "熄灭",
}


# ============ 3. 模拟场景预设 ============
SCENES = {
    "正常用电": {
        "description": "正常用电状态，绿灯常亮",
        "power": 150,
        "voltage": 220,
        "current": 0.68,
        "temperature": 25,
        "humidity": 50,
        "switch_on": True,
        "online": True,
        "led": "green",
        "buzzer": False,
    },
    "功率预警": {
        "description": "功率接近阈值，黄灯预警",
        "power": 500,
        "voltage": 218,
        "current": 2.29,
        "temperature": 26,
        "humidity": 52,
        "switch_on": True,
        "online": True,
        "led": "yellow",
        "buzzer": False,
    },
    "违规用电": {
        "description": "功率超过阈值，红灯+蜂鸣器告警",
        "power": 900,
        "voltage": 215,
        "current": 4.19,
        "temperature": 28,
        "humidity": 55,
        "switch_on": True,
        "online": True,
        "led": "red",
        "buzzer": True,
    },
    "深夜用电": {
        "description": "深夜时段大功率用电，红灯+蜂鸣器告警",
        "power": 300,
        "voltage": 220,
        "current": 1.36,
        "temperature": 24,
        "humidity": 48,
        "switch_on": True,
        "online": True,
        "led": "red",
        "buzzer": True,
    },
    "设备离线": {
        "description": "设备处于离线状态",
        "power": 0,
        "voltage": 0,
        "current": 0,
        "temperature": None,
        "humidity": None,
        "switch_on": False,
        "online": False,
        "led": "off",
        "buzzer": False,
    },
    "温湿度过高": {
        "description": "温度或湿度过高，黄灯预警",
        "power": 100,
        "voltage": 220,
        "current": 0.45,
        "temperature": 38,
        "humidity": 85,
        "switch_on": True,
        "online": True,
        "led": "yellow",
        "buzzer": False,
    },
    "断电恢复": {
        "description": "功率归零、开关关闭，模拟断电后状态",
        "power": 0,
        "voltage": 0,
        "current": 0,
        "temperature": 25,
        "humidity": 50,
        "switch_on": False,
        "online": True,
        "led": "off",
        "buzzer": False,
    },
}


# ============ 4. 核心逻辑函数 ============

def get_simulation_data(device_id):
    """返回当前模拟的设备数据。

    优先使用自定义数据（_current_data），否则回退到当前预设场景。
    返回格式与 device_client.get_all_devices() 中单个设备结构对齐。
    """
    data = _current_data if _current_data else SCENES.get(_current_scene, {})
    return {
        "device_id": device_id,
        "device_name": f"模拟设备({device_id})",
        "owner": "模拟用户",
        "online": data.get("online", True),
        "switch_on": data.get("switch_on", True),
        "power_w": data.get("power", 0),
        "voltage_v": data.get("voltage", 220),
        "current_ma": (data.get("current", 0) * 1000) if data.get("current") is not None else 0,
        "energy_kwh": 0,
        "energy_wh": 0,
        "temperature": data.get("temperature"),
        "humidity": data.get("humidity"),
        "led_color": data.get("led", "green"),
        "buzzer": data.get("buzzer", False),
        "timestamp": _now_str(),
        "_simulated": True,
    }


def set_simulation_scene(scene_name):
    """设置模拟预设场景。

    Args:
        scene_name: 场景名称，必须是 SCENES 中定义的预设名称。

    Returns:
        dict: {"success": bool, "message": str, "scene": str}
    """
    global _current_scene, _current_data
    if scene_name not in SCENES:
        available = "、".join(SCENES.keys())
        return {"success": False, "message": f"未知场景，可选: {available}"}
    _current_scene = scene_name
    _current_data = None  # 清除自定义数据，以预设场景为准
    return {
        "success": True,
        "message": f"已设置为场景「{scene_name}」: {SCENES[scene_name]['description']}",
        "scene": scene_name,
        "data": SCENES[scene_name],
    }


def set_simulation_data(data):
    """设置自定义模拟数据，覆盖预设场景。

    Args:
        data: dict，可包含 power, voltage, current, temperature,
              humidity, switch_on, online, led, buzzer 任意字段。

    Returns:
        dict: {"success": True, "data": merged_data}
    """
    global _current_data
    # 取预设场景数据为底，再用传入字段覆盖
    base = SCENES.get(_current_scene, {})
    merged = dict(base)
    # 标准化字段名
    field_map = {
        "power": "power",
        "voltage": "voltage",
        "current": "current",
        "temperature": "temperature",
        "humidity": "humidity",
        "switch_on": "switch_on",
        "online": "online",
        "led_color": "led",
        "led": "led",
        "buzzer": "buzzer",
    }
    for key, value in data.items():
        mapped = field_map.get(key, key)
        merged[mapped] = value
    _current_data = merged
    return {"success": True, "data": _current_data}


def get_simulation_status():
    """返回当前模拟状态的摘要信息。

    Returns:
        dict: 包含 enabled, current_scene, scenes, custom_data 等字段。
    """
    scene_data = SCENES.get(_current_scene) if _current_scene in SCENES else None
    scenes_list = [
        {"name": name, "description": info["description"]}
        for name, info in SCENES.items()
    ]
    return {
        "enabled": SIMULATION_ENABLED,
        "current_scene": _current_scene if not _current_data else "自定义",
        "scenes": scenes_list,
        "scene_data": scene_data,
        "custom_data": _current_data,
        "simulation": SIMULATION_ENABLED,
    }


# ============ 5. 辅助函数 ============

def _now_str():
    """返回当前时间的格式化字符串。"""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============ 6. API 路由注册 ============

def register_simulation_routes(app):
    """向 Flask app 注册模拟模式相关的 API 路由。

    Args:
        app: Flask 应用实例。
    """

    @app.route('/api/simulation/status')
    def api_simulation_status():
        """GET /api/simulation/status - 获取模拟模式状态。"""
        return jsonify(get_simulation_status())

    @app.route('/api/simulation/toggle', methods=['POST'])
    def api_simulation_toggle():
        """POST /api/simulation/toggle - 开启/关闭模拟模式。

        请求体 JSON:
            {"enabled": true/false}
        """
        global SIMULATION_ENABLED
        data = request.get_json() or {}
        old_value = SIMULATION_ENABLED
        SIMULATION_ENABLED = bool(data.get('enabled', not old_value))
        print(f"[模拟模式] toggle called, old={old_value}, new={SIMULATION_ENABLED}")
        return jsonify({
            "success": True,
            "enabled": SIMULATION_ENABLED,
            "simulation_enabled": SIMULATION_ENABLED,
            "message": f"模拟模式已{'开启' if SIMULATION_ENABLED else '关闭'}",
        })

    @app.route('/api/simulation/scene', methods=['POST'])
    def api_simulation_scene():
        """POST /api/simulation/scene - 设置预设场景。

        请求体 JSON:
            {"scene": "正常用电"}
        """
        data = request.get_json() or {}
        scene_name = data.get('scene', '').strip()
        if not scene_name:
            return jsonify({"success": False, "message": "缺少 scene 参数"}), 400
        result = set_simulation_scene(scene_name)
        if result["success"]:
            return jsonify(result)
        return jsonify(result), 400

    @app.route('/api/simulation/custom', methods=['POST'])
    def api_simulation_custom():
        """POST /api/simulation/custom - 设置自定义模拟数据。

        请求体 JSON 可包含以下任意字段:
            power, voltage, current, temperature, humidity,
            switch_on, online, led_color, buzzer
        """
        data = request.get_json() or {}
        if not data:
            return jsonify({"success": False, "message": "请求体为空"}), 400
        result = set_simulation_data(data)
        return jsonify(result)

    @app.route('/api/simulation/data')
    def api_simulation_data():
        """GET /api/simulation/data - 获取当前模拟数据。

        可选参数:
            device_id: 设备ID，不传则返回所有已配置设备的模拟数据。
        """
        device_id = request.args.get('device_id', '').strip()
        if device_id:
            return jsonify(get_simulation_data(device_id))

        # 未指定 device_id 时，为所有已配置的设备返回模拟数据
        import config
        devices = []
        for did in config.DEVICES.keys():
            devices.append(get_simulation_data(did))
        return jsonify(devices)
