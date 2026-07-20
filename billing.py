"""
EcoWise 宿舍助理 - 电费分摊模块
==========================
按插座累计电量算每个室友应交电费:
    应交电费 = 累计电量(kWh) × 电价(元/度)
"""
from typing import Dict, List
from collections import defaultdict

import config


def calc_person_bill(devices_data: List[Dict]) -> Dict:
    """按室友分组算电费。"""
    person_kwh = defaultdict(float)
    person_devices = defaultdict(list)

    MIN_KWH_THRESHOLD = 0.01

    for dev in devices_data:
        if "error" in dev:
            continue
        owner = dev.get("owner", "未知")
        kwh = dev.get("energy_kwh")
        if kwh is None:
            continue
        if kwh < MIN_KWH_THRESHOLD:
            kwh = 0.0
        person_kwh[owner] += kwh
        person_devices[owner].append(dev.get("device_name", dev["device_id"]))

    per_person = []
    total_kwh = 0.0
    for owner, kwh in person_kwh.items():
        yuan = kwh * config.ELECTRICITY_PRICE_PER_KWH
        per_person.append({
            "owner": owner,
            "kwh": round(kwh, 4),
            "wh": round(kwh * 1000.0, 2),
            "yuan": round(yuan, 2),
            "devices": person_devices[owner],
        })
        total_kwh += kwh

    per_person.sort(key=lambda x: x["yuan"], reverse=True)

    return {
        "total_kwh": round(total_kwh, 4),
        "total_yuan": round(total_kwh * config.ELECTRICITY_PRICE_PER_KWH, 2),
        "per_person": per_person,
        "price_per_kwh": config.ELECTRICITY_PRICE_PER_KWH,
    }


def print_bill(bill: Dict) -> None:
    """格式化打印账单。"""
    print("\n" + "=" * 50)
    print("           宿舍电费账单(实时)")
    print("=" * 50)
    print(f"电价: {bill['price_per_kwh']} 元/度")
    print(f"全宿舍累计: {bill['total_kwh']} 度 = {bill['total_yuan']} 元")
    print("-" * 50)
    print(f"{'室友':<8}{'累计电量(度)':<18}{'应交(元)':<12}{'插座'}")
    print("-" * 50)
    for p in bill["per_person"]:
        devices_str = ",".join(p["devices"])
        print(f"{p['owner']:<8}{p['kwh']:<18}{p['yuan']:<12}{devices_str}")
    print("=" * 50 + "\n")
