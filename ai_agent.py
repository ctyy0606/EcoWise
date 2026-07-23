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
    except Exception as e:
        print(f"[天气API] _get_weather_by_ip 异常 (ip={client_ip}): {e}")
        return None

# 常见城市名，用于从用户消息中提取
CHINESE_CITIES = [
    '北京', '上海', '广州', '深圳', '杭州', '南京', '苏州', '成都', '武汉', '西安',
    '重庆', '天津', '长沙', '郑州', '青岛', '大连', '厦门', '福州', '合肥', '济南',
    '宁波', '无锡', '佛山', '东莞', '昆明', '沈阳', '长春', '哈尔滨', '石家庄', '太原',
    '南昌', '南宁', '贵阳', '兰州', '海口', '乌鲁木齐', '拉萨', '银川', '西宁', '呼和浩特',
    '香港', '澳门', '台北'
]


def _extract_city(text):
    """从用户消息中提取中国城市名"""
    for city in CHINESE_CITIES:
        if city in text:
            return city
    return None


def _get_weather_by_coords(lat, lng):
    """通过经纬度获取天气（Open-Meteo 免费 API）"""
    try:
        weather_resp = requests.get(
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&current_weather=true",
            timeout=5
        )
        weather = weather_resp.json().get("current_weather", {})
        if not weather:
            print(f"[天气API] Open-Meteo 返回空数据: {weather_resp.text[:200]}")
            return None
        code = weather.get("weathercode", 0)
        weather_desc = WEATHER_CODES.get(code, "未知")
        temp = weather.get("temperature", 0)
        wind = weather.get("windspeed", 0)
        return {
            "city": f"({lat:.2f},{lng:.2f})",
            "weather": weather_desc,
            "temp": temp,
            "wind": wind,
        }
    except Exception as e:
        print(f"[天气API] _get_weather_by_coords 异常 (lat={lat},lng={lng}): {e}")
        return None


def _get_weather_by_city(city_name):
    """通过城市名获取天气（Open-Meteo 免费 geocoding + forecast）"""
    try:
        # 1. 地理编码
        geo_url = f'https://geocoding-api.open-meteo.com/v1/search?name={city_name}&count=1&language=zh&format=json'
        geo_resp = requests.get(geo_url, timeout=5)
        geo_data = geo_resp.json()
        results = geo_data.get('results', [])
        if not results:
            return None
        lat = results[0].get('latitude')
        lon = results[0].get('longitude')
        city = results[0].get('name', city_name)

        # 2. 获取天气
        weather_resp = requests.get(
            f'https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true',
            timeout=5
        )
        weather = weather_resp.json().get('current_weather', {})
        code = weather.get('weathercode', 0)
        weather_desc = WEATHER_CODES.get(code, '未知')
        temp = weather.get('temperature', 0)
        wind = weather.get('windspeed', 0)

        return {
            'city': city,
            'weather': weather_desc,
            'temp': temp,
            'wind': wind,
        }
    except Exception as e:
        print(f"[天气API] _get_weather_by_city 异常 (city={city_name}): {e}")
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
9. 闹钟提醒：用户说"提醒我XXX"时，按以下闹钟规则处理

【宿舍用电规则知识库】
1. 电费分摊规则：
   - 每个人添加自己的设备，用电量按个人设备统计
   - 同一个寝室的人可以创建空间，把室友拉进空间
   - 电费分摊 = 个人设备用电 + 公共空间用电（灯、空调等）按空间成员人数平摊
   - 电费每月结算一次，可在网页实时查看自己和同空间成员的电费
   - 缴费方式：通过校园生活 App，用校园卡余额缴费
   - 电价：0.6元/度
   - 计算公式：电费 = 用电量(kWh) × 电价
   - 用电量由每个插座的累计电量(Wh)差值计算
   - 1度电 = 1000Wh
   - 空调电费分摊规则：宿舍里的插座分为两类：（1） 个人插座：每个人书桌/床位的插座，只记录该同学自己的用电量。（2） 公共插座：空调、饮水机等公共电器使用的插座，由全宿舍共同使用。
   - 特殊情况：如果有人长期不在宿舍（如实习、请假），可由室友协商是否减免空调分摊，系统默认按人头均摊。

2. 违规电器与功率管理：
   - 功率超过 800W 视为违规电器
   - 功率在 450W-800W 之间为预警
   - 严禁使用的电热类：吹风机、热得快、电炉、电水壶、电热毯、取暖器、卷发棒等
   - 严禁使用的炊事类：电饭锅、电磁炉、电煮锅等
   - 严禁私拉乱接、多个插排串联
   - 错峰用电：高峰期（如夏季 11:00-17:30）尽量减少大功率设备同时使用

3.违规电器清单及功率参考
   - 热得快：1000-1500W
   - 电热毯：40-150W
   - 电水壶：800-2000W
   - 电饭煲：300-1200W
   - 电煮锅：300-2000W
   - 取暖器/小太阳：500-3000W
   - 电磁炉：1600-2200W
   - 电吹风：300-2200W（部分学校限制大于1000W）
   - 卷发棒/直发夹：30-100W（部分学校因发热元件禁止）
     【来源】教育部《高等学校消防安全管理规定》（2010）第十八条禁止在宿舍使用违规电器；各高校《学生宿舍用电管理规定》。

5.功率阈值告警规则
   - 功率大于800W时LED灯亮红灯并且蜂鸣器发出响声，当用电功率恢复到800W以下，LED灯量一下绿灯来提醒用户，蜂鸣器也会停止发出响声

4.用电安全事故数据
   -全国校园火灾中，约67%发生在学生宿舍，其中电器使用不当是主因
    【来源】应急管理部门公开统计数据
   -热得快、电热毯等纯电阻发热电器，持续通电2小时以上即存在显著火灾隐患
    【来源】参见相关消防技术研究

5.火灾应急知识
   -电器着火：先断电，用干粉灭火器，切勿用水
   -逃生：湿毛巾捂口鼻、低姿前行、不乘电梯
   -火警电话：119，讲清学校名称、楼栋号、房间号
    【来源】《消防安全常识二十条》（应急管理部消防救援局）

6. 断电原因判断：
   - 周一到周四晚上 23:00 学校统一熄灯（只熄灯，不断电）
   - 电费耗尽会导致断电，缴费后等待一会儿会恢复
   - 功率超标导致断电，需要找宿管帮忙恢复
   - 周五到周日不统一熄灯

7. 节电建议：
   - 人走电断：离开宿舍关闭照明、空调、风扇，拔掉充电器
   - 空调夏季建议 26℃，冬季不高于 20℃，开启时关闭门窗
   - 白天优先利用自然光，减少开灯
   - 杜绝待机耗电，充电完成后及时拔插头
   - 电器待机耗电约占宿舍能耗的 35%

【作息健康管理】
1.健康作息标准：成年人（18-25岁）推荐每日睡眠时长：7-9小时【来源】美国国家睡眠基金会（National Sleep Foundation,2015）
2.最佳入睡时间：22:00-23:00入睡有利深度睡眠【来源】《睡眠医学》（人民卫生出版社, 第2版）
3.熬夜定义：超过23:00未入睡，或睡眠不足6小时【来源】中国睡眠研究会《2023中国睡眠大数据报告》
4.熬夜危害：
   - 免疫力下降【来源】Irwin, M.R. (2015). Sleep and immunefunction. Sleep Medicine Reviews.
   - 记忆力减退：睡眠是记忆巩固关键期【来源】Walker, M.P. (2017). Why We Sleep.Scribner.
   - 情绪障碍：与焦虑、抑郁显著相关【来源】《柳叶刀·精神病学》（2019）中国大学生心理健康研究专题
5.午休建议：午睡20-30分钟最佳，可恢复精力且避免醒后昏沉【来源】Dhand, R. & Sohal, H. (2006). Goodsleep, bad sleep. BMJ.
6.浴室开放时间：每天6：00至8：00及10：00至23：30（上海工程技术大学松江校区为参考）

【碳排放与环保知识】
- 碳排放因子：中国电网平均排放因子：0.5777kg CO₂/kWh。即：每用1度电≈ 排放0.5777kg二氧化碳【来源】生态环境部《企业温室气体排放核算方法与报告指南 发电设施》（2024修订版）
- 碳汇参考：一棵成年阔叶树每年吸收约10kgCO₂【来源】《中国温室气体自愿减排项目方法学》（国家应对气候变化战略研究和国际合作中心,2015）
- 国家双碳目标：碳达峰：2030年前；碳中和：2060年前【来源】习近平主席在第75届联合国大会上的讲话（2020年9月22日）

【环境与天气规则】
1. 天气定位：通过用户授权获取位置，但支持全国其他城市
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
4. 天气规则：如果下方提供了"当前天气"数据，直接使用它来回答天气问题，不要再问用户所在城市；如果用户消息中已包含城市名（如"北京天气"），直接使用该城市返回的天气；如果天气数据显示"无法获取"，诚实告知用户当前无法查询天气，建议查看天气应用或稍后再试。
5. 时间规则：如果用户问现在几点，根据系统消息中的"当前时间"回答，不要编造。
6. 老实规则：遇到不确定的问题，老实说"我不太确定"，不要编造。涉及具体学校政策时，建议用户查看学校官方通知。
7. 隐私规则：不询问、不泄露用户的个人敏感信息。
8. 闹钟提醒规则：当用户说出"提醒我XXX"、"设置闹钟"、"叫我XXX"等类似表达时，你必须做以下事情：
   - 第一步：从用户消息中提取闹钟提醒内容（如"去上课"、"收衣服"、"睡觉"等）
   - 第二步：从用户消息中提取时间信息（如"明天早上8点"、"今晚10点"、"30分钟后"等）
   - 第三步：在回复中明确告诉用户"已设置闹钟，将在【具体时间】提醒你【具体内容】"
   - 如果用户只说"提醒我XXX"但没有说时间，用"今天"询问用户具体时间
   - 格式示例：用户说"提醒我明天早上8点去上课"，你回复"好的，已设置闹钟，明天早上8点会提醒你去上课哦~"
   - 注意：闹钟是真实可用的，系统会在设定的时间通过浏览器推送通知提醒用户，所以请诚实告知用户闹钟已设置成功
9. 备忘录规则：只有当用户消息明确包含"提醒我XXX"、"帮我记一下"、"设置备忘录"、"加个备忘录"、"备忘录"等关键词时，才触发备忘录功能。以下情况不算备忘录：
   - 用户说"好"、"可以"、"需要"、"是的"、"对的"等确认词 → 这是在确认你上一个问题，不是要创建备忘录
   - 用户说"帮我看看"、"帮我查一下" → 这是在请求查询，不是创建备忘录
   - 用户说普通日常对话 → 正常回复即可
   - 确认用户要设置备忘录时，提取内容、日期和时间
   - 如果用户没有指定时间，默认早上9:00，告知用户并询问是否修改
   - 格式示例：用户说"明天下午3点去图书馆"，你回复"好的，已为你添加备忘录：明天下午3点提醒你去图书馆~"
   - 格式示例：用户说"7月25日交作业"，你回复"好的，已为你添加备忘录：7月25日早上9点提醒你交作业（默认时间），如果需要修改提醒时间可以告诉我哦~"
   - 注意：备忘录是真实可用的，系统会在设定的时间通过浏览器推送通知提醒用户

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
            lines.append(f"- {name}（{dev_owner}）：{online}，功率{power}W，电压{voltage}V，电流{current}mA，开关{switch}，温度{temp}°C，湿度{humidity}%")

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
    
    for d in devices:
        temp = d.get("temperature_c")
        hum = d.get("humidity_percent")
        if temp is not None:
            temps.append(temp)
        if hum is not None:
            hums.append(hum)
    
    if not temps and not hums:
        return None
    
    parts = []
    if temps:
        avg_temp = sum(temps) / len(temps)
        parts.append(f"平均温度{avg_temp:.1f}°C")
    if hums:
        avg_hum = sum(hums) / len(hums)
        parts.append(f"平均湿度{avg_hum:.0f}%")
    
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


def chat(user_message, owner=None, client_ip=None, history=None, user_lat=None, user_lng=None, phone=None):
    """
    调用通义千问大模型，返回回复文本。
    自动注入当前时间、用电数据和天气作为上下文。可按用户过滤设备。
    支持多轮对话历史。
    支持通过 user_lat/user_lng 传入用户授权的位置。
    phone: 用户手机号，用于备忘录等需要手机号的场景。
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
        # 优先按用户坐标查询（用户授权位置），其次按城市名，最后按IP
        city = _extract_city(user_message)
        weather = None
        if user_lat is not None and user_lng is not None:
            weather = _get_weather_by_coords(user_lat, user_lng)
            if weather:
                print(f"[AI天气] 通过用户坐标获取天气成功: {weather['city']} {weather['weather']} {weather['temp']}°C")
            else:
                print(f"[AI天气] 通过用户坐标获取天气失败 (lat={user_lat}, lng={user_lng})，尝试城市名")
        if not weather and city:
            weather = _get_weather_by_city(city)
            if weather:
                print(f"[AI天气] 通过城市名获取天气成功: {weather['city']} {weather['weather']}")
            else:
                print(f"[AI天气] 通过城市名获取天气失败 (city={city})，尝试IP")
        if not weather:
            weather = _get_weather_by_ip(client_ip)
            if weather:
                print(f"[AI天气] 通过IP获取天气成功: {weather['city']} {weather['weather']}")
            else:
                print(f"[AI天气] 通过IP获取天气失败 (ip={client_ip})，无法获取天气")
        if weather:
            weather_info = f"【当前天气】{weather['city']}，{weather['weather']}，气温{weather['temp']}°C，风速{weather['wind']}km/h"
        else:
            weather_info = "【当前天气】无法获取当前天气信息，建议用户授权地理位置或手动输入城市名查询"

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

    resp = requests.post(API_URL, headers=headers, json=data, timeout=45)
    result = resp.json()

    if "choices" in result and len(result["choices"]) > 0:
        reply = result["choices"][0]["message"]["content"]
    elif "error" in result:
        return f"AI服务返回错误：{result['error'].get('message', '未知错误')}"
    else:
        return "AI服务返回异常，请稍后再试。"

    # ============ 闹钟处理 ============
    # 如果用户消息包含闹钟关键词，自动解析并设置闹钟
    alarm_keywords = ["提醒我", "提醒我", "叫我", "闹钟", "定时", "到点", "到时"]
    if any(kw in user_message for kw in alarm_keywords):
        try:
            from alarm_clock import parse_alarm_from_text, add_alarm
            alarm_info = parse_alarm_from_text(user_message)
            if alarm_info["has_alarm"] and alarm_info["remind_at"]:
                result = add_alarm(owner or "", alarm_info["remind_at"], alarm_info["message"])
                if result["success"]:
                    reply += f"\n\n（系统已自动设置闹钟：{alarm_info['remind_at']} 提醒你「{alarm_info['message']}」）"
                else:
                    reply += f"\n\n（闹钟设置失败：{result['message']}）"
        except Exception as e:
            print(f"[AI闹钟] 处理失败: {e}")

    # ============ 备忘录处理 ============
    # 如果用户消息包含日期+事件，自动解析并设置备忘录
    # 必须同时包含备忘录意图词 AND 日期+事件模式才触发
    memo_intent_words = ["备忘录", "记一下", "别忘了", "记得"]
    date_keywords = ["明天", "后天", "下周一", "下周二", "下周三", "下周四", "下周五", "下周六", "下周日", "今天"]
    has_memo_intent = any(kw in user_message for kw in memo_intent_words)
    has_date_event = any(kw in user_message for kw in date_keywords) and any(kw in user_message for kw in ["去", "做", "交", "开会", "打", "上", "买", "见", "写", "复习", "考试", "提醒"])
    starts_with_date = any(user_message.startswith(kw) for kw in ["明天", "今天", "后天", "下周一", "下周二", "下周三", "下周四", "下周五", "下周六", "下周日"])
    if has_memo_intent or has_date_event or (starts_with_date and not any(qk in user_message for qk in ["?", "？", "吗", "什么", "怎么", "会不会", "是不是", "有没有", "多少"])):
        # 避免与闹钟重复处理
        if not any(kw in user_message for kw in alarm_keywords):
            try:
                from memo import parse_memo_from_text, add_memo
                memo_info = parse_memo_from_text(user_message)
                if memo_info["has_memo"] and memo_info["memo_date"] and memo_info["memo_time"]:
                    # 使用 phone（手机号）而非 owner（昵称），确保与前端API的session['phone']一致
                    memo_user = phone or owner or ""
                    result = add_memo(memo_user, memo_info["memo_date"], memo_info["memo_time"], memo_info["content"])
                    if result["success"]:
                        time_note = "（默认早上9:00，如需修改时间请告诉我）" if memo_info["default_time_used"] else ""
                        reply += f"\n\n（📝系统已自动添加备忘录：{memo_info['memo_date']} {memo_info['memo_time']} 提醒你「{memo_info['content']}」{time_note}）"
                    else:
                        reply += f"\n\n（备忘录添加失败：{result['message']}）"
            except Exception as e:
                print(f"[AI备忘录] 处理失败: {e}")

    return reply
