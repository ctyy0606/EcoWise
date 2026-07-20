"""
EcoWise 宿舍助理 - 主程序入口(L0)
================================
运行方式:
    cd d:\\VS\\code\\Ecowise
    python main.py
"""
import device_client
import violation_detector
import billing
import energy_history


def _print_devices(devices):
    """打印插座实时数据。"""
    print("\n--- 插座实时数据 ---")
    for d in devices:
        if "error" in d:
            print(f"  [{d['device_name']}] 拉取失败: {d['error']}")
            continue
        print(f"  [{d['device_name']}]  负责人: {d['owner']}")
        print(f"     在线: {d['online']}  开关: {d['switch_on']}")
        print(f"     功率: {d['power_w']}W   电压: {d['voltage_v']}V   电流: {d['current_ma']}mA")
        print(f"     累计电量: {d['energy_kwh']} 度 (kWh)  =  {d['energy_wh']} Wh")
        print(f"     采集时间: {d['timestamp']}")


def _print_violations(devices):
    """打印违规检测结果。"""
    print("\n--- 违规检测 ---")
    for d in devices:
        if "error" in d:
            print(f"  [{d['device_name']}] 设备异常,跳过")
            continue
        v = d.get("violation", {})
        mark = {"normal": "[OK]", "warning": "[!]", "violation": "[X]"}.get(v.get("level"), "[?]")
        print(f"  {mark} [{d['device_name']}] {v.get('title')} - {v.get('message')}")


def cmd_show_devices():
    devices = device_client.get_all_devices()
    _print_devices(devices)


def cmd_check_violation():
    devices = device_client.get_all_devices()
    devices = violation_detector.detect_all_devices(devices)
    _print_violations(devices)


def cmd_show_bill():
    devices = device_client.get_all_devices()
    bill = billing.calc_person_bill(devices)
    billing.print_bill(bill)


def cmd_run_all():
    devices = device_client.get_all_devices()
    _print_devices(devices)
    devices = violation_detector.detect_all_devices(devices)
    _print_violations(devices)
    bill = billing.calc_person_bill(devices)
    billing.print_bill(bill)


def cmd_show_today():
    # 先采集一次,确保当前累计电量被记录到数据库
    energy_history.record_all()
    energy_history.print_all_today()


def cmd_show_month():
    # 先采集一次
    energy_history.record_all()
    energy_history.print_all_month()


MENU = """
========== EcoWise 宿舍助理 ==========
1) 查看所有插座实时数据
2) 违规电器检测
3) 查看电费分摊账单
4) 全部跑一遍(数据 + 检测 + 账单)
5) 查看今日用电量(分小时)
6) 查看本月用电量(分日,可做趋势图)
0) 退出
--------------------------------------
请选择: """


def main():
    while True:
        choice = input(MENU).strip()
        if choice == "1":
            cmd_show_devices()
        elif choice == "2":
            cmd_check_violation()
        elif choice == "3":
            cmd_show_bill()
        elif choice == "4":
            cmd_run_all()
        elif choice == "5":
            cmd_show_today()
        elif choice == "6":
            cmd_show_month()
        elif choice == "0":
            print("再见!")
            break
        else:
            print("无效输入,请重新选择。")


if __name__ == "__main__":
    main()
