"""
EcoWise 宿舍助理 - 电器指纹识别
================================
功率指纹库：根据功率范围 + 持续时间 + 波形特征识别电器类型。
L1 阶段用功率范围 + 持续时间粗略匹配，L2 可加波形分析。
"""
from typing import Dict, Optional


# 电器指纹库
FINGERPRINTS = [
    {
        "name": "电热毯",
        "power_range": (60, 150),
        "duration_type": "long",
        "pattern": "slow_rise",
        "risk": "中",
        "advice": "电热毯属违规电器，建议改用热水袋或保暖衣物。",
    },
    {
        "name": "热得快",
        "power_range": (800, 1500),
        "duration_type": "long",
        "pattern": "stable",
        "risk": "高",
        "advice": "热得快极易引发火灾，严禁在宿舍使用！",
    },
    {
        "name": "电水壶",
        "power_range": (1500, 2200),
        "duration_type": "short",
        "pattern": "stable",
        "risk": "高",
        "advice": "电水壶功率过大，容易触发跳闸，请改用公共区域饮水机。",
    },
    {
        "name": "吹风机",
        "power_range": (800, 2000),
        "duration_type": "short",
        "pattern": "fluctuate",
        "risk": "中",
        "advice": "吹风机短时使用可以，但注意不要超过限电阈值。",
    },
    {
        "name": "电脑充电器",
        "power_range": (30, 90),
        "duration_type": "long",
        "pattern": "fluctuate",
        "risk": "低",
        "advice": "正常用电，充电完毕可拔掉以节省能耗。",
    },
    {
        "name": "台灯/小电器",
        "power_range": (5, 30),
        "duration_type": "long",
        "pattern": "stable",
        "risk": "低",
        "advice": "正常低功率用电。",
    },
    {
        "name": "电饭锅/电磁炉",
        "power_range": (800, 2200),
        "duration_type": "long",
        "pattern": "stable",
        "risk": "高",
        "advice": "电饭锅/电磁炉属违规大功率电器，严禁在宿舍使用！",
    },
]


def _classify_duration(duration_minutes):
    """持续时间分类：短时<10min，长时>=10min"""
    if duration_minutes is None:
        return None
    return "short" if duration_minutes < 10 else "long"


def identify_appliance(power_w, duration_minutes=None) -> Dict:
    """
    识别电器类型。

    返回:
        {
            "appliance": str,       - 电器名称
            "confidence": float,    - 置信度 0.0~1.0
            "risk": str,            - 风险等级（低/中/高）
            "advice": str,          - 使用建议
            "reason": str,          - 判断依据
        }
    """
    if power_w is None or power_w <= 0:
        return {
            "appliance": "未知电器",
            "confidence": 0.0,
            "risk": "低",
            "advice": "",
            "reason": "无功率数据",
        }

    dur_type = _classify_duration(duration_minutes)
    candidates = []

    for fp in FINGERPRINTS:
        low, high = fp["power_range"]
        if low <= power_w <= high:
            # 基础匹配分（功率匹配）
            score = 0.6
            reason = f"功率{power_w}W在{fp['name']}范围({low}-{high}W)内"

            # 持续时间加分
            if dur_type and fp["duration_type"] == dur_type:
                score += 0.25
                reason += f"，持续时间符合{('短时' if dur_type == 'short' else '长时')}特征"
            elif dur_type:
                reason += f"，持续时间{('短时' if dur_type == 'short' else '长时')}但指纹为{('短时' if fp['duration_type'] == 'short' else '长时')}"

            # 功率居中加分
            mid = (low + high) / 2
            range_span = high - low
            if range_span > 0:
                center_score = 1 - abs(power_w - mid) / range_span
                score += center_score * 0.15

            candidates.append({
                "appliance": fp["name"],
                "confidence": round(min(score, 1.0), 2),
                "risk": fp["risk"],
                "advice": fp["advice"],
                "reason": reason,
            })

    if not candidates:
        if power_w > 2200:
            return {
                "appliance": "超大功率电器",
                "confidence": 0.8,
                "risk": "高",
                "advice": "功率严重超标，立即关闭！",
                "reason": f"功率{power_w}W超过2200W，属超大功率电器",
            }
        return {
            "appliance": "未知电器",
            "confidence": 0.3,
            "risk": "中" if power_w > 450 else "低",
            "advice": "请留意该电器的功率消耗。",
            "reason": f"功率{power_w}W未匹配到已知指纹",
        }

    # 取置信度最高的
    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    return candidates[0]


def enhance_violation_info(power_w, duration_minutes=None) -> Dict:
    """
    给违规事件补充电器识别 + 风险等级 + 建议。
    用于 violation_detector 在确认违规时增强信息。
    """
    result = identify_appliance(power_w, duration_minutes)
    return {
        "appliance": result["appliance"],
        "confidence": result["confidence"],
        "risk": result["risk"],
        "advice": result["advice"],
        "reason": result["reason"],
    }
