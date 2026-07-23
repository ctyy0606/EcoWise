"""
EcoWise 宿舍助理 - 全局配置文件
================================
所有"会变的参数"都集中在这里:
- 涂鸦云 API 凭证
- 设备(插座)清单,以及每个插座对应哪个室友
- 违规检测的功率阈值
- 电价
- 各 DP 的单位换算系数
"""
from typing import Dict

# ============ 1. 涂鸦云开放平台凭证 ============
ACCESS_ID = "kfdcessnjag4fgv4wtru"
ACCESS_SECRET = "2c38065612014ee6801451e82cd66d54"
API_ENDPOINT = "https://openapi.tuyacn.com"  # 中国区


# ============ 1.5 通义千问 API(智能体用) ============
# 在阿里云百炼平台申请: https://bailian.console.aliyun.com/
QWEN_API_KEY = "sk-ws-H.EMPXMIP.LiyZ.MEQCIAQFthmhRF4C53dkSKTakiMCvS-G5kXX0kExQ3gw9jNeAiB4X9tYaSG1Rktiu7AgZod1UD3c5_DPBo1RR3UzbJqs4Q"
QWEN_MODEL = "qwen-turbo"  # 也可用 qwen-plus(更聪明) 或 qwen-max(最强)


# ============ 2. 插座清单 ============
# key 是设备 ID(涂鸦开发者平台 -> 设备管理里能看到)
# value 是这个插座的"友好名字"和它对应的室友
# 注意：开发板设备ID需要使用真实设备ID（vdevo开头的是虚拟设备，配网成功后会变成真实设备）
DEVICES: Dict[str, dict] = {
    "6c3780ebe5a98ff4a0n5rd": {
        "name": "插座1",
        "owner": ""  # 由用户自行添加设备时设置归属
,
        "group": "电脑设备",
    },
    "6c2d79c2bc1d87f3de5ako": {
        "name": "开发板",
        "owner": ""  # 由用户自行添加设备时设置归属
,
        "group": "传感器设备",
    },
}

# ============ 2.5 设备配对（插座与开发板关联） ============
# 开发板是协助插座工作的，它们的数据会合并成一个逻辑设备显示
# key: 插座设备ID, value: 开发板设备ID
DEVICE_PAIRINGS: Dict[str, str] = {
    "6c3780ebe5a98ff4a0n5rd": "6c2d79c2bc1d87f3de5ako",
}

# 网页端添加的设备会写入 devices.json,启动时自动合并到上面的 DEVICES
import json as _json
import os as _os
_TEMP_DIR = _os.path.join(_os.environ.get("TEMP", _os.environ.get("TMP", _os.path.dirname(__file__))), "Ecowise")
if not _os.path.exists(_TEMP_DIR):
    _os.makedirs(_TEMP_DIR, exist_ok=True)
_DEVICES_JSON = _os.path.join(_TEMP_DIR, "devices.json")
if _os.path.exists(_DEVICES_JSON):
    try:
        with open(_DEVICES_JSON, "r", encoding="utf-8") as _f:
            for _d in _json.load(_f):
                DEVICES[_d["device_id"]] = {"name": _d["name"], "owner": _d["owner"]}
    except:
        pass


# ============ 3. 违规检测阈值(单位:瓦 W) ============
VIOLATION_THRESHOLDS = {
    "warning_watts": 450,    # 限电预警线
    "violation_watts": 800, # 违规告警线
}


# ============ 4. 电价 ============
ELECTRICITY_PRICE_PER_KWH = 0.55  # 元/度


# ============ 5. L1 配置 ============
# 碳排放因子（kgCO₂e/kWh），中国电网平均值
CARBON_EMISSION_FACTOR = 0.5777

# 自动断电冷却期（分钟）：违规断电后多久内不允许重新开启
AUTO_POWER_OFF_COOLDOWN_MINUTES = 10

# 智能断电策略参数
SMART_POLICY = {
    "late_night_start": 23,          # 深夜开始时间（23:00）
    "late_night_end": 6,             # 深夜结束时间（06:00）
    "late_night_charging_watts": 50,  # 深夜正常充电阈值
    "late_night_warning_watts": 200,  # 深夜告警阈值
}

# ============ 6. 测试模式配置 ============
# 开启后模拟高功率数据，方便测试自动断电功能（无需真实大功率电器）
# 测试模式：True=使用模拟数据（快速），False=调用真实涂鸦API
# 有真实设备且设备在线时，设为 False
TEST_MODE = False
# 模拟功率值（W），设为 1200 可触发违规断电，设为 500 可触发预警
TEST_POWER_W = 1200


# ============ 7. DP 单位换算系数 ============
# 涂鸦各 DP 原始值 → 物理量的换算关系
# 注意:不同厂商固件可能不一致,实测后调整
#
# add_ele: 原始值 × 0.001 = 度(kWh)
#   例: 原始 6  →  6 × 0.001 = 0.006 度 = 6 Wh
#   (App 显示 0.006,代码里 6 Wh,其实是同一个值)
#
# cur_power: 实测原始值 200,实际 20W → 原始值 × 0.1 = W
#   例: 原始 200  →  200 × 0.1 = 20 W
#
# cur_voltage: 原始值 × 0.1 = V
#   例: 原始 2205  →  2205 × 0.1 = 220.5 V
#
# cur_current: 原始值单位是 mA,无需换算
ADD_ELE_RAW_UNIT_KWH = 0.001  # 1 原始单位 = 0.001 度
POWER_RAW_UNIT_W = 0.1        # 1 原始单位 = 0.1 W
VOLTAGE_RAW_UNIT_V = 0.1     # 1 原始单位 = 0.1 V


# ============ 8. T5开发板告警控制 DP 点配置 ============
# 硬件同学需要在T5开发板固件中实现以下DP点的处理逻辑
BOARD_ALERT_DP = {
    "led_control": "109",     # LED控制 DP点（枚举值）
    "buzzer_control": "110",  # 蜂鸣器控制 DP点（枚举值）
    "alert_level": "111",     # 告警级别 DP点（数值）
}

# 告警级别定义（对应 alert_level DP点的值）
ALERT_LEVELS = {
    "none": 0,      # 正常状态 - 绿灯常亮
    "danger": 1,    # 危险（温湿度异常）- 红灯常亮，蜂鸣器不响
    "critical": 2,  # 紧急（功率>800W）- 红灯常亮，蜂鸣器响
    "recovery": 3,  # 恢复正常 - 绿灯闪烁1秒后恢复常亮
}

# LED状态定义（对应 led_control DP点的值）
# LED只有红/绿两种颜色
LED_STATES = {
    "solid_green": 0,   # 常亮绿色（正常状态）
    "solid_red": 1,     # 常亮红色（告警状态）
    "blink_green": 2,   # 闪烁绿色（恢复提示，闪烁1秒后变回常亮绿）
    "blink_red": 3,     # 闪烁红色（紧急告警）
}

# 蜂鸣器状态定义（对应 buzzer_control DP点的值）
BUZZER_STATES = {
    "off": 0,           # 关闭（默认状态）
    "beep_danger": 1,   # 危险蜂鸣（功率>800W时，持续响直到功率恢复正常）
}


# ============ 9. 温湿度告警阈值 ============
ENV_THRESHOLDS = {
    "temperature_high": 35,     # 温度上限阈值（>35°C触发告警）
    "temperature_low": 5,       # 温度下限阈值（<5°C触发告警）
    "humidity_high": 80,        # 湿度上限阈值（>80%触发告警）
    "humidity_low": 20,         # 湿度下限阈值（<20%触发告警）
}
