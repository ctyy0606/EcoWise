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


# 中国电网平均碳排放因子（kgCO₂/kWh）
_CARBON_FACTOR = 0.581


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

    # 上周日期范围
    last_monday = monday - timedelta(days=7)
    last_week_start = last_monday.strftime("%Y-%m-%d")
    last_week_end = (last_monday + timedelta(days=6)).strftime("%Y-%m-%d")

    # 获取该用户的设备
    all_devices = device_client.get_all_devices()
    if owner_nickname:
        devices = [d for d in all_devices if d.get("owner") == owner_nickname]
    else:
        devices = all_devices

    # 汇总本周和上周的日用电量
    daily_breakdown = {}   # date -> kwh
    last_week_kwh = 0.0
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
                date_str = d.get("date", "")
                kwh_val = d.get("kwh", 0)
                if week_start <= date_str <= week_end:
                    total_kwh += kwh_val
                    daily_breakdown[date_str] = daily_breakdown.get(date_str, 0) + kwh_val
                elif last_week_start <= date_str <= last_week_end:
                    last_week_kwh += kwh_val
        except Exception:
            pass

    total_kwh = round(total_kwh, 4)
    last_week_kwh = round(last_week_kwh, 4)

    # 趋势百分比
    if last_week_kwh > 0:
        trend_percent = round((total_kwh - last_week_kwh) / last_week_kwh * 100, 1)
        trend_direction = "上升" if trend_percent > 0 else "下降" if trend_percent < 0 else "持平"
    else:
        trend_percent = None
        trend_direction = None

    total_yuan = round(total_kwh * config.ELECTRICITY_PRICE_PER_KWH, 2)
    last_week_yuan = round(last_week_kwh * config.ELECTRICITY_PRICE_PER_KWH, 2)

    # 日均用电
    daily_avg = round(total_kwh / 7, 4)

    # 用电高峰日分析：按日用电量排序
    sorted_days = sorted(daily_breakdown.items(), key=lambda x: x[1], reverse=True)
    peak_day = sorted_days[0] if sorted_days else None
    # 格式化的日明细列表
    daily_detail = [
        {"date": day, "kwh": round(kwh, 4)}
        for day, kwh in sorted(daily_breakdown.items())
    ]
    daily_detail.sort(key=lambda x: x["date"])

    # 碳排放（优先调用 carbon 模块，失败则按因子估算）
    carbon_kg = 0.0
    carbon_from_module = False
    try:
        carbon_data = get_week_carbon(None if not owner_nickname else None)
        carbon_kg = round(carbon_data.get("carbon_kg", 0), 4)
        if carbon_kg > 0:
            carbon_from_module = True
    except Exception:
        pass
    if not carbon_from_module:
        carbon_kg = round(total_kwh * _CARBON_FACTOR, 4)

    # 等效植树棵数（每棵树每年吸收约 21.77 kg CO₂，按天折算）
    trees_equivalent = round(carbon_kg / 21.77 * 365 / 7, 1) if carbon_kg > 0 else 0

    # 违规次数（本月，近似）
    try:
        violation_count = get_month_violation_count(None)
    except Exception:
        violation_count = 0

    # 宿舍人均用电对比
    avg_kwh = None
    try:
        owner_totals = {}
        for dev in all_devices:
            if "error" in dev:
                continue
            dev_id = dev.get("device_id")
            owner = dev.get("owner", "").strip()
            if not dev_id or not owner:
                continue
            try:
                month_data = get_month_energy(dev_id)
                for d in month_data.get("daily", []):
                    if week_start <= d.get("date", "") <= week_end:
                        owner_totals[owner] = owner_totals.get(owner, 0) + d.get("kwh", 0)
            except Exception:
                pass
        if owner_totals:
            avg_kwh = round(sum(owner_totals.values()) / len(owner_totals), 4)
    except Exception:
        pass

    # 用电等级评定
    if avg_kwh is not None and avg_kwh > 0:
        ratio = total_kwh / avg_kwh
        if ratio < 0.7:
            level = "节能先锋"
        elif ratio < 1.0:
            level = "低于平均"
        elif ratio < 1.3:
            level = "接近平均"
        elif ratio < 1.8:
            level = "高于平均"
        else:
            level = "用电偏高"
    else:
        ratio = None
        level = None

    return {
        "week_start": week_start,
        "week_end": week_end,
        "total_kwh": total_kwh,
        "total_yuan": total_yuan,
        "last_week_kwh": last_week_kwh,
        "last_week_yuan": last_week_yuan,
        "trend_percent": trend_percent,
        "trend_direction": trend_direction,
        "daily_breakdown": daily_detail,
        "peak_day": {"date": peak_day[0], "kwh": round(peak_day[1], 4)} if peak_day else None,
        "daily_avg": daily_avg,
        "carbon_kg": carbon_kg,
        "trees_equivalent": trees_equivalent,
        "violation_count": violation_count,
        "avg_kwh": avg_kwh,
        "level": level,
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

    # 构建趋势描述
    if data["trend_percent"] is not None:
        direction = data["trend_direction"]
        abs_pct = abs(data["trend_percent"])
        if direction == "上升":
            trend_desc = f"较上周{direction} {abs_pct}%（+{data['last_week_kwh']}→{data['total_kwh']} 度），请注意控制用电"
        elif direction == "下降":
            trend_desc = f"较上周{direction} {abs_pct}%（{data['last_week_kwh']}→{data['total_kwh']} 度），继续保持！"
        else:
            trend_desc = f"与上周持平（均为 {data['total_kwh']} 度）"
    else:
        trend_desc = "上周无用电数据，无法进行趋势对比"

    # 日用电明细
    if data["daily_breakdown"]:
        daily_lines = "\n".join(
            f"  - {d['date']}（{'周' + '一二三四五六日'[datetime.strptime(d['date'], '%Y-%m-%d').weekday()]}）: {d['kwh']} 度"
            for d in data["daily_breakdown"]
        )
    else:
        daily_lines = "  暂无日用电数据"

    # 高峰日
    if data["peak_day"]:
        peak_weekday = "周" + "一二三四五六日"[datetime.strptime(data["peak_day"]["date"], "%Y-%m-%d").weekday()]
        peak_desc = f"用电高峰出现在 {peak_weekday}（{data['peak_day']['date']}），当日用电 {data['peak_day']['kwh']} 度"
    else:
        peak_desc = "暂无高峰日数据"

    # 宿舍对比
    if data["avg_kwh"] is not None and data["avg_kwh"] > 0:
        avg_desc = f"宿舍人均用电 {data['avg_kwh']} 度，你的用电水平评定：{data['level']}（为平均的 {round(data['total_kwh']/data['avg_kwh']*100)}%）"
    else:
        avg_desc = "暂无宿舍平均水平数据"

    # 碳排放与环保
    carbon_desc = f"碳排放约 {data['carbon_kg']} kgCO₂，相当于需要 {data['trees_equivalent']} 棵树一周的吸收量"

    # 违规情况
    if data["violation_count"] > 0:
        violation_desc = f"本月已累计 {data['violation_count']} 次违规用电，请注意遵守宿舍用电规定"
    else:
        violation_desc = "本月暂无违规记录，表现良好！"

    prompt = f"""请根据以下本周用电数据生成一份详细、专业的宿舍用电周报：

【基础数据】
- 统计周期：{data['week_start']} 至 {data['week_end']}
- 本周总用电：{data['total_kwh']} 度（kWh）
- 本周电费：{data['total_yuan']} 元（电价 {config.ELECTRICITY_PRICE_PER_KWH} 元/度）
- 日均用电：{data['daily_avg']} 度/天

【趋势对比】
{trend_desc}

【用电模式分析】
{peak_desc}

日用电明细：
{daily_lines}

【宿舍对比】
{avg_desc}

【环保数据】
{carbon_desc}

【用电纪律】
{violation_desc}

【格式要求】
请生成一份结构清晰、内容丰富的周报，严格按以下格式输出，每部分使用统一的标题：

📊 本周用电概况
（概括本周总用电量、电费、日均用电量等核心数据，1-2句话）

📈 用电趋势分析
（结合环比数据，分析用电变化趋势及可能原因）

🔍 用电模式洞察
（分析用电高峰日、用电习惯特征）

💡 个性化节能建议
（根据用电模式给出 2-3 条针对性、可操作的节能建议）

🌱 环保贡献
（介绍碳排放量及环保意义）

📋 下周温馨提示
（节电小贴士、违规提醒、天气相关的用电建议）

【语气要求】
1. 语气专业但不失亲切，像贴心的宿舍管家
2. 如果用电量为 0，第一段就提示用户检查设备是否正常连接
3. 节能建议要具体可操作，不要泛泛而谈
4. 整体字数 350-500 字"""

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
            "last_week_kwh": data["last_week_kwh"],
            "trend_percent": data["trend_percent"],
            "trend_direction": data["trend_direction"],
            "daily_breakdown": data["daily_breakdown"],
            "peak_day": data["peak_day"],
            "daily_avg": data["daily_avg"],
            "carbon_kg": data["carbon_kg"],
            "trees_equivalent": data["trees_equivalent"],
            "violation_count": data["violation_count"],
            "avg_kwh": data["avg_kwh"],
            "level": data["level"],
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
    last_week_kwh = sum(d["last_week_kwh"] for d in all_data)
    carbon_kg = sum(d["carbon_kg"] for d in all_data)
    trees_equivalent = sum(d["trees_equivalent"] for d in all_data)
    violation_count = sum(d["violation_count"] for d in all_data)

    week_start = all_data[0]["week_start"]
    week_end = all_data[0]["week_end"]

    # 趋势
    if last_week_kwh > 0:
        trend_percent = round((total_kwh - last_week_kwh) / last_week_kwh * 100, 1)
        trend_direction = "上升" if trend_percent > 0 else "下降" if trend_percent < 0 else "持平"
        trend_desc = f"较上周{trend_direction} {abs(trend_percent)}%（{last_week_kwh}→{total_kwh} 度）"
    else:
        trend_desc = "上周无用电数据，无法进行趋势对比"

    # 人均
    avg_kwh = round(total_kwh / len(members), 4) if members else 0

    # 成员明细
    member_detail_lines = "\n".join(
        f"  - {m}: {d['total_kwh']} 度 ({d['total_yuan']} 元) [{d['level'] or '暂无评级'}]"
        for m, d in zip(members, all_data)
    )

    # 违规汇总
    if violation_count > 0:
        violation_desc = f"本月空间累计 {violation_count} 次违规用电，请各成员注意遵守宿舍用电规定"
    else:
        violation_desc = "本月空间暂无违规记录"

    # 环保
    carbon_desc = f"空间整体碳排放约 {carbon_kg} kgCO₂，相当于需要 {trees_equivalent} 棵树一周的吸收量"

    prompt = f"""请根据以下空间本周用电数据生成一份详细的空间用电周报：

【空间信息】
- 空间成员：{', '.join(members)}

【基础数据】
- 统计周期：{week_start} 至 {week_end}
- 空间总用电：{total_kwh} 度（kWh）
- 空间总电费：{total_yuan} 元（电价 {config.ELECTRICITY_PRICE_PER_KWH} 元/度）
- 人均用电：{avg_kwh} 度/人

【趋势对比】
{trend_desc}

【成员用电明细】
{member_detail_lines}

【环保数据】
{carbon_desc}

【用电纪律】
{violation_desc}

【格式要求】
请生成一份结构清晰、内容丰富的空间周报，严格按以下格式输出：

📊 空间用电总览
（概述空间总用电量、电费、人均用电等核心数据）

📈 用电趋势分析
（环比变化分析，总体趋势走向）

👥 成员用电对比
（分析各成员用电情况，评出节能之星和耗电大户）

💡 空间节能建议
（针对空间整体给出 2-3 条集体节能建议）

🌱 空间环保贡献
（碳排放及环保数据解读）

📋 下周集体提醒
（节电协作建议、违规预警、共同目标）

【语气要求】
1. 语气友善但不失专业，适合发到群聊
2. 如果数据全为 0，提示检查设备连接
3. 节能建议要适合集体行动
4. 整体字数 350-500 字"""

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
            "last_week_kwh": last_week_kwh,
            "trend_percent": trend_percent if last_week_kwh > 0 else None,
            "trend_direction": trend_direction if last_week_kwh > 0 else None,
            "avg_kwh": avg_kwh,
            "carbon_kg": carbon_kg,
            "trees_equivalent": trees_equivalent,
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
