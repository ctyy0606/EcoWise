"""
EcoWise 宿舍助理 - Flask 网页版服务器
======================================
运行方式:
    cd d:\\VS\\code\\Ecowise
    python web_server.py
然后在手机浏览器访问: http://电脑IP:5000
"""
from flask import Flask, render_template, jsonify, request, session, Response, make_response
from functools import wraps
from datetime import timedelta, datetime
import time
import config
import device_client
import violation_detector
import billing
import energy_history
import ai_agent
import user_auth
import space_manager
import notification
import carbon
import auto_power_off
import smart_power_policy
import weekly_report
import appliance_fingerprint

print("[DEBUG] 服务器启动时加载的配置:")
print(f"[DEBUG] DEVICES keys: {list(config.DEVICES.keys())}")
print(f"[DEBUG] DEVICE_PAIRINGS: {config.DEVICE_PAIRINGS}")

app = Flask(__name__)
app.secret_key = "ecowise_dorm_secret_2026"
# session 持久化时长：30分钟无操作自动过期
app.permanent_session_lifetime = timedelta(minutes=30)

# 退出后免密登录宽限期（秒）：5分钟内重新访问自动登录
LOGOUT_GRACE_SECONDS = 300


# ============ 静态文件路由（Service Worker 等根级文件） ============
@app.route('/sw.js')
def serve_sw():
    return app.send_static_file('sw.js')

@app.route('/manifest.json')
def serve_manifest():
    return app.send_static_file('manifest.json')


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin', '')
    if origin:
        response.headers['Access-Control-Allow-Origin'] = origin
    else:
        response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        print(f"[DEBUG login_required] session keys: {list(session.keys())}")
        if 'nickname' not in session:
            print(f"[DEBUG login_required] nickname not in session, returning 401")
            return jsonify({"error": "未登录", "need_login": True}), 401
        print(f"[DEBUG login_required] nickname found: {session['nickname']}")
        return f(*args, **kwargs)
    return decorated


def _current_nickname():
    """返回当前登录用户的用户名（显示名），用于设备过滤"""
    return session.get('nickname')


def _get_user_devices():
    """获取当前登录用户的设备（按nickname过滤owner）"""
    nickname = _current_nickname()
    if not nickname:
        print(f"[DEBUG] _get_user_devices: no nickname, returning empty")
        return []
    all_devices = device_client.get_all_devices()
    print(f"[DEBUG] _get_user_devices: nickname={nickname}, all_devices_count={len(all_devices)}")
    for d in all_devices:
        print(f"[DEBUG]   device: {d.get('device_id')}, owner={d.get('owner')}")
    filtered = [d for d in all_devices if d.get('owner') == nickname]
    print(f"[DEBUG] _get_user_devices: filtered_count={len(filtered)}")
    return filtered


def _check_device_owner(device_id):
    """检查设备是否属于当前登录用户"""
    nickname = _current_nickname()
    if not nickname:
        return False
    if device_id not in config.DEVICES:
        return False
    return config.DEVICES[device_id].get('owner') == nickname


@app.route('/')
def index():
    """首页"""
    resp = make_response(render_template('index.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


# ============ 用户认证 API ============

@app.route('/api/send_code', methods=['POST'])
def api_send_code():
    """发送验证码（模拟模式：返回验证码到前端显示）"""
    data = request.get_json()
    phone = data.get('phone', '').strip()
    if not phone:
        return jsonify({"success": False, "message": "请输入手机号"})
    if not user_auth._validate_phone(phone):
        return jsonify({"success": False, "message": "手机号格式不正确"})
    code = user_auth.generate_code(phone)
    # 模拟模式：直接返回验证码。上线时改为调用短信服务发送，不在响应中返回
    return jsonify({"success": True, "message": "验证码已发送", "code": code})


@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json()
    phone = data.get('phone', '').strip()
    nickname = data.get('nickname', '').strip()
    password = data.get('password', '')
    code = data.get('code', '').strip()
    success, message = user_auth.register(phone, nickname, password, code)
    if success:
        return jsonify({"success": True, "message": message})
    return jsonify({"success": False, "message": message})


@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    phone = data.get('phone', '').strip()
    password = data.get('password', '')
    success, message, nickname = user_auth.login(phone, password)
    if success:
        session.permanent = True
        session['phone'] = phone
        session['nickname'] = nickname
        session.pop('logout_time', None)
        return jsonify({"success": True, "message": message, "username": nickname})
    return jsonify({"success": False, "message": message})


@app.route('/api/logout', methods=['POST'])
def api_logout():
    # 软退出：保留session但记录退出时间，5分钟内可免密恢复
    session['logout_time'] = time.time()
    return jsonify({"success": True, "message": "已退出"})


@app.route('/api/current_user')
def api_current_user():
    nickname = session.get('nickname')
    if not nickname:
        return jsonify({"logged_in": False})

    logout_time = session.get('logout_time')
    if logout_time:
        if time.time() - logout_time < LOGOUT_GRACE_SECONDS:
            # 宽限期内，自动恢复登录
            session.pop('logout_time', None)
            return jsonify({"logged_in": True, "username": nickname})
        else:
            # 超过宽限期，真正清除
            session.clear()
            return jsonify({"logged_in": False})

    return jsonify({"logged_in": True, "username": nickname})


# ============ 设备 API ============

@app.route('/api/devices')
@login_required
def api_devices():
    """获取当前用户的所有插座实时数据"""
    try:
        devices = device_client.get_all_devices()
        print(f"[DEBUG] api_devices: raw devices count={len(devices)}")
        for d in devices:
            print(f"[DEBUG]   {d.get('device_id')}: owner={d.get('owner')}")
        nickname = _current_nickname()
        print(f"[DEBUG] api_devices: current nickname={nickname}")
        filtered = [d for d in devices if d.get('owner') == nickname]
        print(f"[DEBUG] api_devices: filtered count={len(filtered)}")

        # 如果当前用户没有匹配设备，但配置中存在其他 owner 的设备，给出提示
        if not filtered and devices:
            other_owners = sorted(set(d.get('owner') for d in devices if d.get('owner')))
            return jsonify({
                "devices": filtered,
                "hint": f"当前登录用户 '{nickname}' 没有设备。现有设备属于: {', '.join(other_owners)}。请使用对应用户名登录，或在设备管理中添加设备。"
            })
        return jsonify(filtered)
    except Exception as e:
        print(f"[DEBUG] api_devices error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/violation')
@login_required
def api_violation():
    """获取当前用户的违规检测结果"""
    try:
        devices = _get_user_devices()
        devices = violation_detector.detect_all_devices(devices)
        return jsonify(devices)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/violations/today')
@login_required
def api_violations_today():
    """获取今日确认的违规事件（支持按设备过滤）"""
    try:
        device_id = request.args.get('device_id', '').strip()
        violations = violation_detector.get_today_violations(device_id if device_id else None)
        return jsonify({"violations": violations, "count": len(violations)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/violations/month')
@login_required
def api_violations_month():
    """获取本月确认的违规事件（按日期汇总，支持按设备过滤）"""
    try:
        device_id = request.args.get('device_id', '').strip()
        violations = violation_detector.get_month_violations(device_id if device_id else None)
        total_count = violation_detector.get_month_violation_count(device_id if device_id else None)
        return jsonify({"daily": violations, "total_count": total_count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/bill')
@login_required
def api_bill():
    """获取当前用户的电费分摊账单"""
    try:
        devices = _get_user_devices()
        bill = billing.calc_person_bill(devices)
        return jsonify({
            "total_amount": bill.get("total_yuan", 0),
            "persons": [
                {"name": p.get("owner", "未知"), "total_kwh": p.get("kwh", 0), "amount": p.get("yuan", 0)}
                for p in bill.get("per_person", [])
            ]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/today')
@login_required
def api_today():
    """获取今日用电数据(支持按设备查询)"""
    try:
        energy_history.record_all()
        device_id = request.args.get('device_id', '').strip()
        if not device_id:
            user_devices = _get_user_devices()
            if not user_devices:
                return jsonify({"total_kwh": 0, "hourly": []})
            device_id = user_devices[0]['device_id']
        result = energy_history.get_today_for_device(device_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/month')
@login_required
def api_month():
    """获取本月用电数据(支持按设备查询)"""
    try:
        energy_history.record_all()
        device_id = request.args.get('device_id', '').strip()
        if not device_id:
            user_devices = _get_user_devices()
            if not user_devices:
                return jsonify({"total_kwh": 0, "daily": []})
            device_id = user_devices[0]['device_id']
        result = energy_history.get_month_for_device(device_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/record')
@login_required
def api_record():
    """手动触发一次数据采集"""
    energy_history.record_all()
    return jsonify({"status": "ok"})


@app.route('/api/debug/device_raw/<device_id>')
def api_debug_device_raw(device_id):
    """调试API：获取涂鸦云返回的原始设备数据（尝试多个API端点）"""
    try:
        from device_client import _get_openapi
        api = _get_openapi()
        
        results = {
            "device_id": device_id,
            "endpoints": {},
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        
        endpoints_to_try = [
            f"/v1.0/devices/{device_id}",
            f"/v1.0/devices/{device_id}/status",
            f"/v2.0/devices/{device_id}/status",
            f"/v1.0/iot-03/devices/{device_id}/status",
            f"/v1.0/iot-03/devices/{device_id}/status?codes=temp,humidity",
            f"/v1.0/devices/{device_id}/specification",
            f"/v1.0/devices/{device_id}/shadow",
            f"/v1.0/iot-03/devices/{device_id}/logs?start_time=0&end_time=9999999999999&size=20",
            f"/v1.0/iot-03/devices/{device_id}/properties",
            f"/v1.0/iot-03/devices/{device_id}/properties?codes=temp,humidity",
            f"/v1.0/statistics-device/device/{device_id}/day",
        ]
        
        for endpoint in endpoints_to_try:
            try:
                resp = api.get(endpoint)
                results["endpoints"][endpoint] = {
                    "success": resp.get("success", False),
                    "result": resp.get("result", None),
                    "t": resp.get("t", None),
                }
            except Exception as e:
                results["endpoints"][endpoint] = {
                    "success": False,
                    "error": str(e),
                }
        
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/debug/device_dps/<device_id>')
def api_debug_device_dps(device_id):
    """调试API：获取设备的物模型定义和所有DP点状态（帮助排查DP点问题）"""
    try:
        from device_client import _get_openapi
        api = _get_openapi()
        
        results = {
            "device_id": device_id,
            "device_info": None,
            "status_dps": [],
            "product_functions": [],
            "issues": [],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        
        info_resp = api.get(f"/v1.0/devices/{device_id}")
        if info_resp.get("success", False):
            results["device_info"] = info_resp.get("result", {})
            product_id = results["device_info"].get("product_id")
            online = results["device_info"].get("online", False)
            
            if not online:
                results["issues"].append("设备当前离线，请确保设备已配网并在线")
            
            if product_id:
                func_resp = api.get(f"/v1.0/iot-03/products/{product_id}/functions")
                if func_resp.get("success", False):
                    results["product_functions"] = func_resp.get("result", [])
        
        status_resp = api.get(f"/v1.0/devices/{device_id}/status")
        if status_resp.get("success", False):
            results["status_dps"] = status_resp.get("result", [])
        
        reported_dp_codes = set()
        for dp in results["status_dps"]:
            code = dp.get("code")
            if code:
                reported_dp_codes.add(str(code))
        
        expected_dp_codes = set()
        for func in results["product_functions"]:
            code = func.get("code")
            if code:
                expected_dp_codes.add(str(code))
        
        missing_dps = expected_dp_codes - reported_dp_codes
        if missing_dps:
            results["issues"].append(f"设备未上报以下DP点: {', '.join(sorted(missing_dps))}")
            results["missing_dps"] = sorted(list(missing_dps))
        
        if results["product_functions"] and not results["status_dps"]:
            results["issues"].append("设备物模型已配置，但设备未上报任何状态数据")
        
        if not results["product_functions"]:
            results["issues"].append("未获取到产品物模型定义，请检查产品配置")
        
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/debug/device_by_product/<product_id>')
def api_debug_device_by_product(product_id):
    """调试API：按产品ID搜索设备（不需要登录）"""
    try:
        from device_client import _get_openapi
        api = _get_openapi()
        
        device_list = []
        page_size = 100
        page_no = 1
        
        while True:
            resp = api.get(f"/v1.0/devices?page_size={page_size}&page_no={page_no}")
            devices = resp.get("result", [])
            if not devices:
                break
            
            for dev in devices:
                if dev.get("product_id") == product_id:
                    device_list.append({
                        "device_id": dev.get("id"),
                        "name": dev.get("name"),
                        "product_id": dev.get("product_id"),
                        "product_name": dev.get("product_name"),
                        "model": dev.get("model"),
                        "category": dev.get("category"),
                        "online": dev.get("online"),
                        "active_time": dev.get("active_time"),
                    })
            
            if len(devices) < page_size:
                break
            page_no += 1
        
        return jsonify({
            "product_id": product_id,
            "devices": device_list,
            "count": len(device_list),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/debug/config_dump')
def api_debug_config_dump():
    """调试API：打印当前加载的配置（帮助排查配置问题）"""
    try:
        from config import DEVICES, DEVICE_PAIRINGS, BOARD_ALERT_DP, ALERT_LEVELS, LED_STATES, BUZZER_STATES, ENV_THRESHOLDS, VIOLATION_THRESHOLDS
        import os
        return jsonify({
            "DEVICES_keys": list(DEVICES.keys()),
            "DEVICE_PAIRINGS": DEVICE_PAIRINGS,
            "BOARD_ALERT_DP": BOARD_ALERT_DP,
            "ALERT_LEVELS": ALERT_LEVELS,
            "LED_STATES": LED_STATES,
            "BUZZER_STATES": BUZZER_STATES,
            "ENV_THRESHOLDS": ENV_THRESHOLDS,
            "VIOLATION_THRESHOLDS": VIOLATION_THRESHOLDS,
            "config_file_path": os.path.abspath(__file__),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/debug/all_devices')
def api_debug_all_devices():
    """调试API：获取涂鸦云中所有设备和配置的设备（不需要登录）"""
    try:
        from device_client import _get_openapi
        from config import DEVICES, DEVICE_PAIRINGS
        
        print(f"[DEBUG API] DEVICES keys at request time: {list(DEVICES.keys())}")
        print(f"[DEBUG API] DEVICE_PAIRINGS at request time: {DEVICE_PAIRINGS}")
        
        api = _get_openapi()
        
        cloud_devices = []
        cloud_error = None
        try:
            page_size = 100
            page_no = 1
            while True:
                resp = api.get(f"/v1.0/devices?page_size={page_size}&page_no={page_no}")
                devices = resp.get("result", [])
                if not devices:
                    break
                
                for dev in devices:
                    cloud_devices.append({
                        "device_id": dev.get("id"),
                        "name": dev.get("name"),
                        "product_id": dev.get("product_id"),
                        "product_name": dev.get("product_name"),
                        "model": dev.get("model"),
                        "category": dev.get("category"),
                        "online": dev.get("online"),
                        "active_time": dev.get("active_time"),
                        "uuid": dev.get("uuid"),
                    })
                
                if len(devices) < page_size:
                    break
                page_no += 1
        except Exception as e:
            cloud_error = str(e)
        
        configured_devices = []
        for device_id, info in DEVICES.items():
            configured_devices.append({
                "device_id": device_id,
                "name": info.get("name", device_id),
                "owner": info.get("owner", "未知"),
                "group": info.get("group", "未知"),
                "is_paired_socket": device_id in DEVICE_PAIRINGS,
                "paired_with": DEVICE_PAIRINGS.get(device_id, None),
                "is_paired_board": device_id in DEVICE_PAIRINGS.values(),
            })
        
        return jsonify({
            "cloud_devices": cloud_devices,
            "cloud_count": len(cloud_devices),
            "cloud_error": cloud_error,
            "configured_devices": configured_devices,
            "configured_count": len(configured_devices),
            "device_pairings": DEVICE_PAIRINGS,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/debug/product_dps/<product_id>')
def api_debug_product_dps(product_id):
    """调试API：获取产品的物模型定义（所有DP点）"""
    try:
        from device_client import _get_openapi
        api = _get_openapi()
        
        results = {}
        
        endpoints = [
            f"/v1.0/products/{product_id}/functions",
            f"/v1.0/products/{product_id}",
            f"/v1.0/iot-03/products/{product_id}/functions",
            f"/v1.0/iot-03/products/{product_id}/definition",
        ]
        
        for endpoint in endpoints:
            try:
                resp = api.get(endpoint)
                if resp.get("success", False):
                    results[endpoint] = resp.get("result", resp)
            except Exception as e:
                results[endpoint] = {"error": str(e)}
        
        return jsonify({
            "product_id": product_id,
            "results": results,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/ai', methods=['POST'])
@login_required
def api_ai():
    """AI 智能助手接口"""
    try:
        data = request.get_json()
        message = data.get('message', '')
        history = data.get('history', [])
        owner = _current_nickname()
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        user_lat = session.get('user_lat')
        user_lng = session.get('user_lng')
        reply = ai_agent.chat(message, owner, client_ip=client_ip, history=history, user_lat=user_lat, user_lng=user_lng)
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"reply": "抱歉，AI 服务暂时不可用: " + str(e)}), 500


@app.route('/api/habit-analysis')
@login_required
def api_habit_analysis():
    """用电习惯分析接口"""
    try:
        owner = _current_nickname()
        result = ai_agent.analyze_habits(owner)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/export/csv')
@login_required
def api_export_csv():
    """导出用电数据为CSV文件"""
    try:
        import csv
        from io import StringIO
        from datetime import datetime
        
        phone = session.get('phone')
        device_id = request.args.get('device_id', '')
        days = int(request.args.get('days', 7))
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        records = []
        try:
            records = energy_history.get_records_by_date_range(
                device_id, 
                start_date.strftime("%Y-%m-%d"), 
                end_date.strftime("%Y-%m-%d")
            )
        except Exception:
            pass
        
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(['日期', '时间', '设备ID', '功率(W)', '电量(Wh)', '电压(V)', '电流(mA)'])
        
        for r in records:
            writer.writerow([
                r.get('record_date', ''),
                r.get('recorded_at', ''),
                r.get('device_id', ''),
                r.get('power_w', ''),
                r.get('energy_wh', ''),
                r.get('voltage_v', ''),
                r.get('current_ma', ''),
            ])
        
        output.seek(0)
        filename = f"ecowise_data_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"
        
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "text/csv; charset=utf-8"
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============ 碳排放 API ============

@app.route('/api/carbon/today')
@login_required
def api_carbon_today():
    """今日碳排放（支持 ?device_id=xxx）"""
    try:
        device_id = request.args.get('device_id', '').strip()
        if not device_id:
            user_devices = _get_user_devices()
            if not user_devices:
                return jsonify({"date": datetime.now().strftime("%Y-%m-%d"), "total_kwh": 0, "carbon_kg": 0, "hourly": [], "factor": config.CARBON_EMISSION_FACTOR})
            device_id = user_devices[0]['device_id']
        result = carbon.get_today_carbon(device_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/carbon/week')
@login_required
def api_carbon_week():
    """本周碳排放（支持 ?device_id=xxx）"""
    try:
        device_id = request.args.get('device_id', '').strip()
        if device_id and not _check_device_owner(device_id):
            return jsonify({"error": "无权访问"}), 403
        result = carbon.get_week_carbon(device_id if device_id else None)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/carbon/total')
@login_required
def api_carbon_total():
    """累计碳排放"""
    try:
        device_id = request.args.get('device_id', '').strip()
        result = carbon.get_total_carbon(device_id if device_id else None)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============ 通知 API ============

@app.route('/api/notifications')
@login_required
def api_notifications():
    """获取通知列表"""
    phone = session.get('phone')
    unread_only = request.args.get('unread_only', 'false').lower() == 'true'
    notifications = notification.get_notifications(phone, unread_only=unread_only)
    return jsonify({"notifications": notifications, "count": len(notifications)})


@app.route('/api/notifications/unread_count')
@login_required
def api_notifications_unread_count():
    """获取未读通知数量"""
    phone = session.get('phone')
    count = notification.get_unread_count(phone)
    return jsonify({"count": count})


@app.route('/api/notifications/read', methods=['POST'])
@login_required
def api_notifications_read():
    """标记单条通知为已读"""
    data = request.get_json()
    notification_id = data.get('notification_id')
    phone = session.get('phone')
    notification.mark_as_read(notification_id, phone)
    return jsonify({"success": True})


@app.route('/api/notifications/read_all', methods=['POST'])
@login_required
def api_notifications_read_all():
    """全部标记已读"""
    phone = session.get('phone')
    count = notification.mark_all_as_read(phone)
    return jsonify({"success": True, "marked_count": count})


# ============ 空间管理 API ============

@app.route('/api/space/create', methods=['POST'])
@login_required
def api_space_create():
    """创建空间"""
    data = request.get_json()
    name = data.get('name', '').strip()
    creator_phone = session.get('phone')
    success, message, space_id = space_manager.create_space(name, creator_phone)
    return jsonify({"success": success, "message": message, "space_id": space_id})


@app.route('/api/space/invite', methods=['POST'])
@login_required
def api_space_invite():
    """邀请室友加入空间（通过手机号）"""
    data = request.get_json()
    space_id = data.get('space_id')
    invitee_phone = data.get('phone', '').strip()
    alias = data.get('alias', '').strip()
    inviter_phone = session.get('phone')
    success, message = space_manager.invite_member(space_id, inviter_phone, invitee_phone, alias)
    return jsonify({"success": success, "message": message})


@app.route('/api/space/invitations')
@login_required
def api_space_invitations():
    """获取当前用户收到的待处理邀请"""
    phone = session.get('phone')
    invitations = space_manager.get_pending_invitations(phone)
    return jsonify({"invitations": invitations, "count": len(invitations)})


@app.route('/api/space/invite/respond', methods=['POST'])
@login_required
def api_space_invite_respond():
    """接受或拒绝邀请"""
    data = request.get_json()
    invitation_id = data.get('invitation_id')
    accept = bool(data.get('accept', False))
    phone = session.get('phone')
    success, message = space_manager.respond_invitation(invitation_id, phone, accept)
    return jsonify({"success": success, "message": message})


@app.route('/api/space/list')
@login_required
def api_space_list():
    """获取当前用户加入的所有空间"""
    phone = session.get('phone')
    spaces = space_manager.get_user_spaces(phone)
    return jsonify({"spaces": spaces, "count": len(spaces)})


@app.route('/api/space/members')
@login_required
def api_space_members():
    """获取空间成员列表"""
    space_id = request.args.get('space_id', type=int)
    if not space_id:
        return jsonify({"error": "缺少 space_id"}), 400
    phone = session.get('phone')
    success, result = space_manager.get_space_members(space_id, phone)
    if success:
        return jsonify({"members": result, "count": len(result)})
    return jsonify({"error": result}), 403


@app.route('/api/space/bill')
@login_required
def api_space_bill():
    """获取空间电费分摊（每人所有设备电费总和）"""
    space_id = request.args.get('space_id', type=int)
    if not space_id:
        return jsonify({"error": "缺少 space_id"}), 400
    phone = session.get('phone')
    success, result = space_manager.get_space_bill(space_id, phone)
    if success:
        return jsonify(result)
    return jsonify({"error": result}), 403


@app.route('/api/space/leave', methods=['POST'])
@login_required
def api_space_leave():
    """离开空间"""
    data = request.get_json()
    space_id = data.get('space_id')
    phone = session.get('phone')
    success, message = space_manager.leave_space(space_id, phone)
    return jsonify({"success": success, "message": message})


@app.route('/api/space/delete', methods=['POST'])
@login_required
def api_space_delete():
    """删除空间（仅创建者）"""
    data = request.get_json()
    space_id = data.get('space_id')
    phone = session.get('phone')
    success, message = space_manager.delete_space(space_id, phone)
    return jsonify({"success": success, "message": message})


@app.route('/api/member/alias', methods=['POST'])
@login_required
def api_member_alias():
    """设置成员备注名（仅创建者可设置）"""
    data = request.get_json()
    space_id = data.get('space_id')
    member_phone = data.get('member_phone', '').strip()
    alias = data.get('alias', '').strip()
    setter_phone = session.get('phone')
    success, message = space_manager.set_member_alias(space_id, setter_phone, member_phone, alias)
    return jsonify({"success": success, "message": message})


@app.route('/api/my_bill')
@login_required
def api_my_bill():
    """获取当前用户自己设备的电费明细（按设备）"""
    try:
        devices = _get_user_devices()
        import config
        MIN_KWH_THRESHOLD = 0.01
        device_bills = []
        total_kwh = 0.0
        for dev in devices:
            if "error" in dev:
                continue
            kwh = dev.get("energy_kwh")
            if kwh is None:
                kwh = 0
            if kwh < MIN_KWH_THRESHOLD:
                kwh = 0.0
            yuan = kwh * config.ELECTRICITY_PRICE_PER_KWH
            device_bills.append({
                "name": dev.get("device_name", dev.get("device_id", "未知")),
                "device_id": dev.get("device_id", ""),
                "kwh": round(kwh, 4),
                "yuan": round(yuan, 2),
            })
            total_kwh += kwh
        device_bills.sort(key=lambda x: x["yuan"], reverse=True)
        return jsonify({
            "total_kwh": round(total_kwh, 4),
            "total_amount": round(total_kwh * config.ELECTRICITY_PRICE_PER_KWH, 2),
            "devices": device_bills,
            "price_per_kwh": config.ELECTRICITY_PRICE_PER_KWH,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============ 设备管理 API ============

@app.route('/api/devices/add', methods=['POST'])
@login_required
def api_add_device():
    """添加新设备(owner自动设为当前登录用户名)"""
    try:
        import json, os
        data = request.get_json()

        device_id = data.get('device_id', '').strip()
        name = data.get('name', '').strip()
        device_group = data.get('group', '').strip() or '未分组'
        owner = _current_nickname()

        if not device_id or not name:
            return jsonify({"success": False, "message": "请填写设备ID和设备名称"})

        if device_id in config.DEVICES:
            pass

        config.DEVICES[device_id] = {"name": name, "owner": owner, "group": device_group}

        devices_file = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.dirname(__file__))), "Ecowise", "devices.json")
        existing = []
        if os.path.exists(devices_file):
            with open(devices_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        existing.append({"device_id": device_id, "name": name, "owner": owner, "group": device_group})
        with open(devices_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        return jsonify({"success": True, "message": "添加成功"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/devices/delete', methods=['POST'])
@login_required
def api_delete_device():
    """删除设备(只能删除自己的设备)"""
    try:
        import json, os
        data = request.get_json()
        device_id = data.get('device_id', '').strip()

        if device_id not in config.DEVICES:
            return jsonify({"success": False, "message": "设备不存在"})

        if not _check_device_owner(device_id):
            return jsonify({"success": False, "message": "无权删除他人的设备"})

        del config.DEVICES[device_id]

        devices_file = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.dirname(__file__))), "Ecowise", "devices.json")
        if os.path.exists(devices_file):
            with open(devices_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing = [d for d in existing if d.get("device_id") != device_id]
            with open(devices_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)

        return jsonify({"success": True, "message": "删除成功"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/devices/modify', methods=['POST'])
@login_required
def api_modify_device():
    """修改设备名称（不能修改设备ID和owner）"""
    try:
        import json, os
        data = request.get_json()
        device_id = data.get('device_id', '').strip()
        new_name = data.get('name', '').strip()
        new_group = data.get('group', '').strip() or '未分组'

        if not device_id or not new_name:
            return jsonify({"success": False, "message": "请填写设备名称"})
        if device_id not in config.DEVICES:
            return jsonify({"success": False, "message": "设备不存在"})
        if not _check_device_owner(device_id):
            return jsonify({"success": False, "message": "无权修改他人的设备"})

        owner = config.DEVICES[device_id].get('owner', '')
        config.DEVICES[device_id] = {"name": new_name, "owner": owner, "group": new_group}

        devices_file = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.dirname(__file__))), "Ecowise", "devices.json")
        if os.path.exists(devices_file):
            with open(devices_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
            for d in existing:
                if d.get("device_id") == device_id:
                    d["name"] = new_name
                    d["group"] = new_group
            with open(devices_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)

        return jsonify({"success": True, "message": "修改成功"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/devices/pair', methods=['POST'])
@login_required
def api_pair_devices():
    """设备配对：将插座与开发板关联，开发板数据会合并到插座显示"""
    try:
        import json, os
        data = request.get_json()
        socket_device_id = data.get('socket_device_id', '').strip()
        board_device_id = data.get('board_device_id', '').strip()

        if not socket_device_id or not board_device_id:
            return jsonify({"success": False, "message": "请选择插座和开发板"})
        
        if socket_device_id not in config.DEVICES:
            return jsonify({"success": False, "message": "插座设备不存在"})
        if board_device_id not in config.DEVICES:
            return jsonify({"success": False, "message": "开发板设备不存在"})
        
        if not _check_device_owner(socket_device_id):
            return jsonify({"success": False, "message": "无权操作该插座"})
        if not _check_device_owner(board_device_id):
            return jsonify({"success": False, "message": "无权操作该开发板"})

        config.DEVICE_PAIRINGS[socket_device_id] = board_device_id
        
        pairings_file = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.dirname(__file__))), "Ecowise", "pairings.json")
        pairings_data = {}
        if os.path.exists(pairings_file):
            with open(pairings_file, "r", encoding="utf-8") as f:
                pairings_data = json.load(f)
        pairings_data[socket_device_id] = board_device_id
        with open(pairings_file, "w", encoding="utf-8") as f:
            json.dump(pairings_data, f, ensure_ascii=False, indent=2)

        return jsonify({"success": True, "message": "配对成功！开发板数据将合并到插座显示"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/devices/unpair', methods=['POST'])
@login_required
def api_unpair_devices():
    """取消设备配对"""
    try:
        import json, os
        data = request.get_json()
        socket_device_id = data.get('socket_device_id', '').strip()

        if not socket_device_id:
            return jsonify({"success": False, "message": "请选择插座"})
        
        if socket_device_id not in config.DEVICE_PAIRINGS:
            return jsonify({"success": False, "message": "该插座未配对开发板"})
        
        if not _check_device_owner(socket_device_id):
            return jsonify({"success": False, "message": "无权操作该插座"})

        del config.DEVICE_PAIRINGS[socket_device_id]
        
        pairings_file = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.dirname(__file__))), "Ecowise", "pairings.json")
        if os.path.exists(pairings_file):
            with open(pairings_file, "r", encoding="utf-8") as f:
                pairings_data = json.load(f)
            if socket_device_id in pairings_data:
                del pairings_data[socket_device_id]
            with open(pairings_file, "w", encoding="utf-8") as f:
                json.dump(pairings_data, f, ensure_ascii=False, indent=2)

        return jsonify({"success": True, "message": "取消配对成功"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/devices/pairings')
@login_required
def api_get_pairings():
    """获取所有设备配对关系"""
    try:
        return jsonify({
            "success": True,
            "pairings": config.DEVICE_PAIRINGS,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/device/switch', methods=['POST'])
@login_required
def api_device_switch():
    """控制插座开关（开启时检查冷却期）"""
    try:
        data = request.get_json()
        device_id = data.get('device_id', '')
        on = bool(data.get('on', False))

        if device_id not in config.DEVICES:
            return jsonify({"success": False, "message": "设备不存在"})
        if not _check_device_owner(device_id):
            return jsonify({"success": False, "message": "无权控制他人的设备"})

        # 开启时检查冷却期
        if on:
            cooldown = auto_power_off.is_in_cooldown(device_id)
            if cooldown["in_cooldown"]:
                return jsonify({
                    "success": False,
                    "message": f"设备处于违规冷却期，剩余{cooldown['remaining_minutes']}分钟，请稍后再试"
                })

        device_client.control_device_switch(device_id, on)

        # 开启成功后解除断电事件
        if on:
            auto_power_off.release_event(device_id)

        return jsonify({"success": True, "message": "开关已" + ("开启" if on else "关闭")})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/device/auto_off_status')
@login_required
def api_device_auto_off_status():
    """获取设备自动断电状态"""
    try:
        device_id = request.args.get('device_id', '').strip()
        if not device_id:
            return jsonify({"error": "缺少 device_id"}), 400
        event = auto_power_off.get_latest_event(device_id)
        return jsonify({"event": event})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/power_off/history')
@login_required
def api_power_off_history():
    """获取断电历史"""
    try:
        device_id = request.args.get('device_id', '').strip()
        history = auto_power_off.get_history(device_id if device_id else None)
        return jsonify({"history": history, "count": len(history)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/smart_policy/status')
@login_required
def api_smart_policy_status():
    """获取智能断电策略状态"""
    try:
        status = smart_power_policy.get_policy_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============ AI 周报 API ============

@app.route('/api/weekly_report/generate', methods=['POST'])
@login_required
def api_weekly_report_generate():
    """生成本周用电周报"""
    try:
        phone = session.get('phone')
        nickname = session.get('nickname')
        result = weekly_report.generate_report(phone, nickname)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": "周报生成失败: " + str(e)}), 500


@app.route('/api/weekly_report/latest')
@login_required
def api_weekly_report_latest():
    """获取最新一期周报"""
    try:
        phone = session.get('phone')
        report = weekly_report.get_latest_report(phone)
        return jsonify({"report": report})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/weekly_report/history')
@login_required
def api_weekly_report_history():
    """获取周报历史"""
    try:
        phone = session.get('phone')
        history = weekly_report.get_report_history(phone)
        return jsonify({"history": history, "count": len(history)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/weekly_report/space/generate', methods=['POST'])
@login_required
def api_weekly_report_space_generate():
    """生成空间周报"""
    try:
        phone = session.get('phone')
        space_id = request.json.get('space_id')
        if not space_id:
            return jsonify({"success": False, "message": "缺少 space_id 参数"}), 400
        result = weekly_report.generate_space_report(space_id, phone)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": "空间周报生成失败: " + str(e)}), 500


@app.route('/api/weekly_report/space/latest')
@login_required
def api_weekly_report_space_latest():
    """获取空间最新一期周报"""
    try:
        space_id = request.args.get('space_id')
        if not space_id:
            return jsonify({"error": "缺少 space_id 参数"}), 400
        report = weekly_report.get_latest_space_report(space_id)
        return jsonify({"report": report})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============ 测试模式 API ============

@app.route('/api/test_mode', methods=['POST'])
def api_test_mode():
    """设置测试模式"""
    print("[DEBUG] api_test_mode called")
    try:
        data = request.get_json()
        print("[DEBUG] request data:", data)
        enabled = data.get('enabled', False)
        power_w = data.get('power_w', 1200)
        temperature_c = data.get('temperature_c', 25)
        humidity_percent = data.get('humidity_percent', 60)
        
        if not enabled:
            import auto_power_off
            auto_power_off.clear_events()
            import energy_history
            energy_history.clear_today_records()
            if hasattr(config, '_test_start_time'):
                delattr(config, '_test_start_time')
            print("[DEBUG] 测试模式关闭，已清除自动断电记录和今日采集数据")
        
        config.TEST_MODE = enabled
        config.TEST_POWER_W = power_w
        config.TEST_TEMPERATURE_C = temperature_c
        config.TEST_HUMIDITY_PERCENT = humidity_percent
        
        return jsonify({
            "success": True,
            "test_mode": config.TEST_MODE,
            "power_w": config.TEST_POWER_W,
            "temperature_c": config.TEST_TEMPERATURE_C,
            "humidity_percent": config.TEST_HUMIDITY_PERCENT,
            "message": f"测试模式已{'开启' if enabled else '关闭'}"
        })
    except Exception as e:
        print("[DEBUG] api_test_mode error:", str(e))
        return jsonify({"error": str(e)}), 500


@app.route('/api/test_mode/status')
def api_test_mode_status():
    """获取当前测试模式状态"""
    print("[DEBUG] api_test_mode_status called")
    return jsonify({
        "test_mode": config.TEST_MODE,
        "power_w": config.TEST_POWER_W,
        "temperature_c": getattr(config, 'TEST_TEMPERATURE_C', 25),
        "humidity_percent": getattr(config, 'TEST_HUMIDITY_PERCENT', 60),
        "violation_threshold": config.VIOLATION_THRESHOLDS.get("violation_watts", 800),
        "warning_threshold": config.VIOLATION_THRESHOLDS.get("warning_watts", 450),
    })


# ============ 电器指纹识别 API ============

@app.route('/api/appliance/identify')
@login_required
def api_appliance_identify():
    """电器识别（根据功率和持续时间）"""
    try:
        power_w = request.args.get('power_w', type=float)
        duration = request.args.get('duration', type=float)
        if power_w is None:
            return jsonify({"error": "缺少 power_w 参数"}), 400
        result = appliance_fingerprint.identify_appliance(power_w, duration)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============ 浏览器推送通知 (Web Push) API ============

@app.route('/api/push/vapid_public_key')
def api_push_vapid_public_key():
    """获取 VAPID 公钥，前端订阅推送时需要"""
    try:
        import push_notification
        return jsonify({"public_key": push_notification.get_vapid_public_key()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/push/subscribe', methods=['POST'])
@login_required
def api_push_subscribe():
    """保存用户浏览器的推送订阅"""
    try:
        import push_notification
        data = request.get_json()
        subscription = data.get('subscription')
        if not subscription:
            return jsonify({"success": False, "message": "订阅信息不能为空"}), 400

        user_phone = session.get('phone', '')
        ok = push_notification.save_subscription(user_phone, subscription)
        if ok:
            return jsonify({"success": True, "message": "订阅成功"})
        return jsonify({"success": False, "message": "订阅信息不完整"}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/push/unsubscribe', methods=['POST'])
@login_required
def api_push_unsubscribe():
    """取消用户的推送订阅"""
    try:
        import push_notification
        data = request.get_json()
        endpoint = data.get('endpoint', '')
        if not endpoint:
            return jsonify({"success": False, "message": "endpoint 不能为空"}), 400

        push_notification.delete_subscription(endpoint)
        return jsonify({"success": True, "message": "已取消订阅"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/push/send_test', methods=['POST'])
@login_required
def api_push_send_test():
    """发送测试推送（仅用于开发测试，发送到当前登录用户）"""
    try:
        import push_notification
        user_phone = session.get('phone', '')
        ok, result = push_notification.send_push_to_user(
            user_phone,
            title="EcoWise 测试通知",
            body="如果你看到这条消息，说明浏览器推送功能正常！",
            tag="test",
            require_interaction=True,
        )
        return jsonify({"success": ok, "result": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/test-push', methods=['POST'])
@login_required
def api_test_push():
    """测试推送接口：接收 user_phone，向该用户的订阅发送一条测试推送"""
    try:
        import push_notification
        data = request.get_json() or {}
        user_phone = data.get('user_phone', '').strip()
        if not user_phone:
            user_phone = session.get('phone', '')
        if not user_phone:
            return jsonify({"success": False, "message": "缺少 user_phone"}), 400

        ok, result = push_notification.send_push_to_user(
            user_phone,
            title="EcoWise 测试通知",
            body="如果你看到这条消息，说明浏览器推送功能正常！",
            tag="test",
            require_interaction=True,
        )
        return jsonify({"success": ok, "result": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ============ 用户位置 API ============
@app.route('/api/user/location', methods=['POST'])
@login_required
def api_user_location():
    """接收用户浏览器定位，存到 session 中供 AI 天气查询使用"""
    try:
        data = request.get_json() or {}
        lat = data.get('lat')
        lng = data.get('lng')
        if lat is not None and lng is not None:
            session['user_lat'] = lat
            session['user_lng'] = lng
            return jsonify({"success": True, "message": "位置已保存"})
        return jsonify({"success": False, "message": "缺少经纬度"}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ============ 模拟模式路由注册 ============
try:
    from simulation import register_simulation_routes
    register_simulation_routes(app)
    print("[模拟模式] 路由已注册")
except ImportError:
    pass

# ============ 闹钟路由注册 ============
try:
    from alarm_clock import register_alarm_routes, start_alarm_thread
    register_alarm_routes(app)
    start_alarm_thread()
    print("[闹钟] 路由已注册，后台线程已启动")
except ImportError as e:
    print(f"[闹钟] 注册失败: {e}")

if __name__ == '__main__':
    import os
    CLEAR_DATA_ON_START = os.environ.get("CLEAR_DATA_ON_START", "false").lower() == "true"

    if CLEAR_DATA_ON_START:
        import os as _os, json as _json
        _dir = _os.path.dirname(_os.path.abspath(__file__))
        _temp_dir = _os.path.join(_os.environ.get("TEMP", _os.environ.get("TMP", _dir)), "Ecowise")

        _devices_file = _os.path.join(_temp_dir, "devices.json")
        if _os.path.exists(_devices_file):
            try:
                with open(_devices_file, "w", encoding="utf-8") as _f:
                    _json.dump([], _f, ensure_ascii=False)
            except PermissionError:
                pass

        _db_file = _os.path.join(_temp_dir, "energy_log.db")
        if _os.path.exists(_db_file):
            try:
                _os.remove(_db_file)
            except PermissionError:
                pass
        
        try:
            import user_auth
            user_auth._get_db().execute("DELETE FROM users")
            user_auth._get_db().commit()
        except:
            pass

        print("[测试模式] 已清空网页端添加的设备和数据库数据（保留配置文件中的默认设备）")
        print("[测试模式] 确认功能无误后，将 CLEAR_DATA_ON_START 改为 False")

    print("="*50)
    print("EcoWise 宿舍助理 - Flask 网页版")
    print("="*50)
    print("运行地址: http://localhost:5000")
    print("手机访问: http://你的电脑IP:5000")
    print("="*50)
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
