"""
EcoWise 宿舍助理 - AI 智能体模块
================================
调用通义千问大模型，结合实时用电数据回答用户问题。
系统提示词定义了"小E"的人设，每次对话自动注入当前用电数据作为上下文。
"""
import requests
import time
from datetime import datetime
import config

API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

# 天气缓存: {ip: (weather_data, expire_timestamp)}
_weather_cache = {}
WEATHER_CACHE_SECONDS = 1800  # 30分钟缓存

# 触发天气查询的关键词
WEATHER_KEYWORDS = ['天气', '下雨', '下雪', '温度', '气温', '冷', '热', '穿什么',
                    '带伞', '收衣服', '防晒', '加衣服', '保暖', '出太阳', '刮风',
                    '湿度', '预报', '晴', '阴', '雨', '风']

# Open-Meteo 天气代码映射
WEATHER_CODES = {
    0: '晴天', 1: '主要晴', 2: '部分多云', 3: '阴天',
    45: '雾', 48: '雾凇',
    51: '小毛毛雨', 53: '毛毛雨', 55: '大毛毛雨',
    56: '冻毛毛雨', 57: '大冻毛毛雨',
    61: '小雨', 63: '中雨', 65: '大雨',
    66: '冻雨', 67: '大冻雨',
    71: '小雪', 73: '中雪', 75: '大雪',
    77: '雪粒',
    80: '小阵雨', 81: '中阵雨', 82: '大阵雨',
    85: '小阵雪', 86: '大阵雪',
    95: '雷暴', 96: '雷暴冰雹', 99: '大雷暴冰雹'
}


def _get_weather_by_ip(client_ip=None):
    """通过IP获取用户位置和实时天气（免费API，无需key，30分钟缓存）"""
    cache_key = client_ip or 'default'
    cached = _weather_cache.get(cache_key)
    if cached:
        data, expire = cached
        if time.time() < expire:
            return data

    try:
        # 1. 获取位置（ip-api.com 免费，支持中文）
        if client_ip and not client_ip.startswith('127.') and not client_ip.startswith('192.168.'):
            geo_url = f'http://ip-api.com/json/{client_ip}?lang=zh'
        else:
            geo_url = 'http://ip-api.com/json/?lang=zh'
        geo_resp = requests.get(geo_url, timeout=5)
        geo = geo_resp.json()
        lat = geo.get('lat')
        lon = geo.get('lon')
        city = geo.get('city') or geo.get('regionName') or '未知'
        if not lat or not lon:
            return None

        # 2. 获取实时天气（Open-Meteo 免费，无需key）
        weather_resp = requests.get(
            f'https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true',
            timeout=5
        )
        weather = weather_resp.json().get('current_weather', {})
        code = weather.get('weathercode', 0)
        weather_desc = WEATHER_CODES.get(code, '未知')
        temp = weather.get('temperature', 0)
        wind = weather.get('windspeed', 0)

        result = {
            'city': city,
            'weather': weather_desc,
            'temp': temp,
            'wind': wind,
        }
        _weather_cache[cache_key] = (result, time.time() + WEATHER_CACHE_SECONDS)
        return result
    except Exception:
        return None

SYSTEM_PROMPT = """你是 EcoWise 宿舍管家，名字叫小E。
你是同学们贴心的宿舍生活全能助手，性格温柔、佛系、可爱，像一位很会照顾人但从不 push 的室友。

【你的人设】
- 温柔管家：体贴细心，会主动关心同学的生活状态
- 佛系伙伴：不强迫、不焦虑，给建议但不施加压力
- 可爱萌系：语气轻松活泼，适当使用 emoji，让对话有温度

【你的职责范围】
1. 用电管理：实时功率/电压/电流查询、用电量统计、电费分摊、违规电器提醒、省电建议
2. 天气与生活：根据天气提醒加衣保暖、收衣服防雨、开窗通风、防晒防暑
3. 作息提醒：提醒早睡早起、避免熬夜、合理安排学习与休息
4. 宿舍安全：防火防盗、用电安全、门窗安全、应急处理
5. 宿舍关系：协助处理室友沟通、提供和谐相处建议、公共区域维护
6. 学习生活：考试周复习建议、时间管理、压力调节、专注力提升
7. 日常生活：洗衣晾晒、宿舍清洁、饮食健康、收纳整理
8. 季节关怀：换季提醒、防蚊防虫、冬季保暖、夏季防暑

【宿舍用电规则知识库】
1. 电费分摊规则：
   - 每个人添加自己的设备，用电量按个人设备统计
   - 同一个寝室的人可以创建空间，把室友拉进空间
   - 电费分摊 = 个人设备用电 + 公共空间用电（灯、空调等）按空间成员人数平摊
   - 电费每月结算一次，可在网页实时查看自己和同空间成员的电费
   - 缴费方式：通过校园生活 App，用校园卡余额缴费

2. 违规电器与功率管理：
   - 功率超过 800W 视为违规电器
   - 功率在 450W-800W 之间为预警
   - 严禁使用的电热类：热得快、电炉、电水壶、电热毯、取暖器、卷发棒等
   - 严禁使用的炊事类：电饭锅、电磁炉、电煮锅等
   - 其他禁用：大功率吹风机、无 3C 认证的劣质电器、部分学校规定的大容量充电宝
   - 严禁私拉乱接、多个插排串联
   - 错峰用电：高峰期（如夏季 11:00-17:30）尽量减少大功率设备同时使用

3. 断电原因判断：
   - 周一到周四晚上 23:00 学校统一熄灯（只熄灯，不断电）
   - 电费耗尽会导致断电，缴费后等待一会儿会恢复
   - 功率超标导致断电，需要找宿管帮忙恢复
   - 周五到周日不统一熄灯

4. 节电建议：
   - 人走电断：离开宿舍关闭照明、空调、风扇，拔掉充电器
   - 空调夏季建议 26℃，冬季不高于 20℃，开启时关闭门窗
   - 白天优先利用自然光，减少开灯
   - 杜绝待机耗电，充电完成后及时拔插头
   - 电器待机耗电约占宿舍能耗的 35%

【环境与天气规则】
1. 天气定位：通过用户授权获取位置，默认以上海为例，但支持全国其他城市
2. 空调建议：
   - 温度传感器显示超过 35℃ 提醒开冷空调
   - 低于 5℃ 提醒开热空调
   - 一般建议空调温度 26℃
   - 如果用户提过自己喜欢的空调温度，要记住并在后续对话中使用
3. 湿度标准：
   - 舒适湿度：40%-60%
   - < 30%：太干，建议加湿器、放盆水、多喝水
   - > 70%：太湿，建议开除湿、通风、干燥剂
   - > 80%：潮湿严重，衣物易发霉，必须除湿
4. 晾晒与雨具：
   - 下雨天气提醒收衣服
   - 雨天衣服未干可去一楼烘干机烘干
   - 可在 u净 App 查看楼栋烘干机/洗衣机是否空闲

【作息管理规则】
1. 宿舍统一熄灯：周一到周四 23:00，周五到周日由学生自己控制
2. 安静时间因寝室而异，如果用户提过要记住
3. 寝室楼早上开门时间：06:00
4. 午休时间没有统一规定，尊重个人习惯

【回答风格】
- 温柔、佛系、可爱，像一个会照顾人的室友
- 适当使用 emoji，但不要过度
- 回答简洁，控制在 150 字以内
- 不 push、不焦虑，建议为主
- 遇到复杂问题可以分点说明

【重要规则】
1. 打招呼规则：用户说"你好""嗨""在吗"等纯打招呼时，只简单回个招呼，不要主动汇报用电数据、天气或其他话题。示例：
   - "你好呀，有什么可以帮你的吗？"
   - "嗨，我在呢，需要什么帮助？"
   - "你好，今天过得怎么样？"
   每次随机选一种。
2. 话题规则：用户聊什么话题就回答什么话题，绝不把话题扯到用电上。聊天气就只聊天气，聊学习就只聊学习。
3. 用电数据规则：只有当用户明确询问用电相关问题时，才使用下方提供的用电数据。
4. 天气规则：如果下方提供了"当前天气"数据，直接使用它来回答天气问题，不要再问用户所在城市。
5. 时间规则：如果用户问现在几点，根据系统消息中的"当前时间"回答，不要编造。
6. 诚实规则：遇到不确定的问题，诚实说"我不太确定"，不要编造。涉及具体学校政策时，建议用户查看学校官方通知。
7. 隐私规则：不询问、不泄露用户的个人敏感信息。

【注意事项】
- 如果用电数据下方有提供，直接用
- 如果用电数据没提供或为 0，让用户刷新页面查看
- 如果用户提到自己的偏好（如空调温度、安静时间），要记住并在后续对话中使用
"""


def _build_context(owner=None):
    """拉取当前用电数据和环境数据，作为AI上下文。可按用户过滤设备。"""
    try:
        from device_client import get_all_devices
        from energy_history import get_all_today, get_all_month

        devices = get_all_devices()
        if owner:
            devices = [d for d in devices if d.get("owner") == owner]
        today = get_all_today()
        month = get_all_month()

        lines = ["【当前用电数据】"]
        for d in devices:
            name = d.get("device_name", d.get("device_id", "未知"))
            dev_owner = d.get("owner", "")
            power = d.get("power_w", 0)
            voltage = d.get("voltage_v", 0)
            current = d.get("current_ma", 0)
            switch = "开" if d.get("switch_on") else "关"
            online = "在线" if d.get("online") else "离线"
            temp = d.get("temperature_c", "--")
            humidity = d.get("humidity_percent", "--")
            light = d.get("light_level", "--")
            lines.append(f"- {name}（{dev_owner}）：{online}，功率{power}W，电压{voltage}V，电流{current}mA，开关{switch}，温度{temp}°C，湿度{humidity}%，光照{light}Lux")

        lines.append(f"今日总用电：{today.get('total_kwh', 0)}度")
        lines.append(f"本月总用电：{month.get('total_kwh', 0)}度")
        lines.append(f"电价：{config.ELECTRICITY_PRICE_PER_KWH}元/度")
        lines.append(f"违规阈值：预警{config.VIOLATION_THRESHOLDS['warning_watts']}W，违规{config.VIOLATION_THRESHOLDS['violation_watts']}W")
        
        env_status = _get_environment_status(devices)
        if env_status:
            lines.append("【环境状态】")
            lines.append(env_status)

        return "\n".join(lines)
    except Exception as e:
        return f"（用电数据获取失败：{e}）"


def _get_environment_status(devices):
    """获取环境状态摘要"""
    temps = []
    hums = []
    lights = []
    
    for d in devices:
        temp = d.get("temperature_c")
        hum = d.get("humidity_percent")
        light = d.get("light_level")
        if temp is not None:
            temps.append(temp)
        if hum is not None:
            hums.append(hum)
        if light is not None:
            lights.append(light)
    
    if not temps and not hums and not lights:
        return None
    
    parts = []
    if temps:
        avg_temp = sum(temps) / len(temps)
        parts.append(f"平均温度{avg_temp:.1f}°C")
    if hums:
        avg_hum = sum(hums) / len(hums)
        parts.append(f"平均湿度{avg_hum:.0f}%")
    if lights:
        avg_light = sum(lights) / len(lights)
        light_desc = "明亮" if avg_light > 50 else "较暗" if avg_light > 20 else "黑暗"
        parts.append(f"环境{light_desc}（{avg_light:.0f}Lux）")
    
    return "，".join(parts)


def analyze_habits(owner=None):
    """
    分析用户用电习惯，生成个性化节能建议。
    返回包含分析结果和建议的字典。
    """
    try:
        from device_client import get_all_devices

        devices = get_all_devices()
        if owner:
            devices = [d for d in devices if d.get("owner") == owner]

        today_kwh = 0
        month_kwh = 0
        avg_daily = 0
        peak_hours = []
        recent = []

        try:
            from energy_history import get_all_today, get_all_month, get_recent_records
            today_data = get_all_today()
            month_data = get_all_month()
            recent = get_recent_records()
            
            today_kwh = today_data.get('total_kwh', 0)
            month_kwh = month_data.get('total_kwh', 0)
            avg_daily = month_kwh / min(30, datetime.now().day) if month_kwh > 0 else 0

            if recent:
                hour_counts = {}
                for r in recent:
                    hour = int(r.get('recorded_at', '00:00').split(':')[0]) if isinstance(r, dict) else 0
                    if hour not in hour_counts:
                        hour_counts[hour] = 0
                    hour_counts[hour] += 1
                sorted_hours = sorted(hour_counts.items(), key=lambda x: x[1], reverse=True)
                peak_hours = [h[0] for h in sorted_hours[:3]]
        except Exception:
            pass

        high_power_devices = []
        for d in devices:
            power = d.get("power_w", 0)
            if power > 100:
                name = d.get("device_name", d.get("device_id", "未知"))
                high_power_devices.append({"name": name, "power": power})

        suggestions = []
        if today_kwh > 5:
            suggestions.append("今日用电量较高，建议检查是否有大功率电器长时间开启")
        if avg_daily > 3:
            suggestions.append("日均用电量偏高，可考虑调整用电习惯")
        if high_power_devices:
            suggestions.append(f"当前有{len(high_power_devices)}台高功率设备运行，注意及时关闭")
        if 12 in peak_hours and 13 in peak_hours:
            suggestions.append("午休时段用电较多，建议养成随手关灯习惯")
        if 22 in peak_hours or 23 in peak_hours:
            suggestions.append("夜间用电较多，注意节省用电并避免熬夜")

        return {
            "today_kwh": round(today_kwh, 2),
            "month_kwh": round(month_kwh, 2),
            "avg_daily": round(avg_daily, 2),
            "peak_hours": peak_hours,
            "high_power_devices": high_power_devices,
            "suggestions": suggestions,
            "device_count": len(devices),
        }
    except Exception as e:
        return {
            "today_kwh": 0,
            "month_kwh": 0,
            "avg_daily": 0,
            "peak_hours": [],
            "high_power_devices": [],
            "suggestions": ["暂无足够数据进行分析，请先使用设备一段时间"],
            "device_count": 0,
        }


def chat(user_message, owner=None, client_ip=None, history=None):
    """
    调用通义千问大模型，返回回复文本。
    自动注入当前时间、用电数据和天气作为上下文。可按用户过滤设备。
    支持多轮对话历史。
    """
    headers = {
        "Authorization": f"Bearer {config.QWEN_API_KEY}",
        "Content-Type": "application/json",
    }
    now = datetime.now()
    time_info = f"【当前时间】{now.strftime('%Y年%m月%d日 %H:%M:%S')}（{'上午' if now.hour < 12 else '下午' if now.hour < 18 else '晚上'}）"
    context = _build_context(owner)

    # 只在用户问天气相关问题时才获取天气数据（避免每次对话都慢）
    weather_info = ""
    if any(kw in user_message for kw in WEATHER_KEYWORDS):
        weather = _get_weather_by_ip(client_ip)
        if weather:
            weather_info = f"【当前天气】{weather['city']}，{weather['weather']}，气温{weather['temp']}°C，风速{weather['wind']}km/h"

    full_context = "\n".join([time_info, weather_info, context])
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n" + full_context},
    ]

    # 注入历史对话（最多保留最近 10 轮）
    if history:
        for item in history[-10:]:
            role = item.get("role")
            content = item.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_message})
    data = {"model": config.QWEN_MODEL, "messages": messages}

    resp = requests.post(API_URL, headers=headers, json=data, timeout=30)
    result = resp.json()

    if "choices" in result and len(result["choices"]) > 0:
        return result["choices"][0]["message"]["content"]
    elif "error" in result:
        return f"AI服务返回错误：{result['error'].get('message', '未知错误')}"
    else:
        return "AI服务返回异常，请稍后再试。"
