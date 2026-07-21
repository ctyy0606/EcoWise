"""
EcoWise 宿舍助理 - AI 闹钟/提醒模块
=====================================
用户可以通过 AI 对话设置闹钟提醒（如"提醒我明天早上8点去上课"）。
闹钟到期时，通过浏览器推送通知发送提醒。

闹钟存储于 SQLite 数据库，后台线程每分钟检查一次到期闹钟。
"""

import os
import sqlite3
import json
import threading
import time
import re
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.expanduser("~"))), "Ecowise", "energy_log.db")


# ============ 1. 数据库操作 ============

def _get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alarms (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_phone  TEXT NOT NULL,
            remind_at   TEXT NOT NULL,        -- 到期时间 "YYYY-MM-DD HH:MM"
            message     TEXT NOT NULL,         -- 提醒内容
            created_at  TEXT NOT NULL,
            notified    INTEGER NOT NULL DEFAULT 0  -- 0=未通知, 1=已通知
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_alarms_due
        ON alarms(notified, remind_at)
    """)
    conn.commit()
    return conn


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def add_alarm(user_phone, remind_at, message):
    """添加一条闹钟提醒。

    Args:
        user_phone: 用户手机号
        remind_at: 提醒时间 "YYYY-MM-DD HH:MM"
        message: 提醒内容文本

    Returns:
        dict: {"success": bool, "message": str, "id": int or None}
    """
    try:
        # 验证时间格式
        dt = datetime.strptime(remind_at, "%Y-%m-%d %H:%M")
        if dt < datetime.now():
            return {"success": False, "message": "提醒时间不能早于当前时间", "id": None}

        conn = _get_db()
        try:
            cursor = conn.execute(
                "INSERT INTO alarms (user_phone, remind_at, message, created_at, notified) "
                "VALUES (?, ?, ?, ?, 0)",
                (user_phone, remind_at, message, _now_str()),
            )
            conn.commit()
            alarm_id = cursor.lastrowid
            return {"success": True, "message": f"闹钟已设置，将在 {remind_at} 提醒你", "id": alarm_id}
        finally:
            conn.close()
    except ValueError:
        return {"success": False, "message": "时间格式错误，请使用 YYYY-MM-DD HH:MM 格式", "id": None}


def get_due_alarms():
    """获取所有到期但未通知的闹钟。

    Returns:
        list of dict: [{"id", "user_phone", "message"}, ...]
    """
    now = _now_str()
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, user_phone, message FROM alarms "
            "WHERE notified = 0 AND remind_at <= ?",
            (now,),
        ).fetchall()
        return [{"id": r[0], "user_phone": r[1], "message": r[2]} for r in rows]
    finally:
        conn.close()


def mark_notified(alarm_id):
    """将指定闹钟标记为已通知。"""
    conn = _get_db()
    try:
        conn.execute("UPDATE alarms SET notified = 1 WHERE id = ?", (alarm_id,))
        conn.commit()
    finally:
        conn.close()


def get_user_alarms(user_phone, limit=10):
    """获取用户最近的闹钟列表。"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, remind_at, message, created_at, notified "
            "FROM alarms WHERE user_phone = ? "
            "ORDER BY remind_at DESC LIMIT ?",
            (user_phone, limit),
        ).fetchall()
        return [
            {
                "id": r[0],
                "remind_at": r[1],
                "message": r[2],
                "created_at": r[3],
                "notified": bool(r[4]),
            }
            for r in rows
        ]
    finally:
        conn.close()


def delete_alarm(alarm_id, user_phone):
    """删除指定闹钟（仅限本人）。"""
    conn = _get_db()
    try:
        conn.execute(
            "DELETE FROM alarms WHERE id = ? AND user_phone = ?",
            (alarm_id, user_phone),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


# ============ 2. AI 自然语言解析 ============

def parse_alarm_from_text(text):
    """从自然语言中解析闹钟意图。

    支持格式示例：
    - "提醒我明天早上8点去上课"
    - "设置闹钟下午3点半收衣服"
    - "明天晚上10点提醒我睡觉"
    - "10分钟后提醒我关灯"

    Returns:
        dict: {"has_alarm": bool, "remind_at": "YYYY-MM-DD HH:MM" or None, "message": str or None}
    """
    # 检测是否包含闹钟关键词
    alarm_keywords = ["提醒我", "提醒我", "叫我", "闹钟", "定时", "到点", "到时"]
    has_alarm = any(kw in text for kw in alarm_keywords)
    if not has_alarm:
        return {"has_alarm": False, "remind_at": None, "message": None}

    now = datetime.now()

    # 提取时间信息
    remind_at = None
    message = text

    # 1) 匹配 "X分钟后"
    min_match = re.search(r"(\d+)\s*分钟\s*后", text)
    if min_match:
        minutes = int(min_match.group(1))
        remind_at = (now + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
        # 提取闹钟内容（去掉时间部分）
        message = re.sub(r"提醒我.*?分钟后", "", text).strip().lstrip("提醒我").strip()
        if not message:
            message = "闹钟提醒"

    # 2) 匹配 "X小时后"
    hour_match = re.search(r"(\d+)\s*小时\s*后", text)
    if hour_match and not remind_at:
        hours = int(hour_match.group(1))
        remind_at = (now + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
        message = re.sub(r"提醒我.*?小时后", "", text).strip().lstrip("提醒我").strip()
        if not message:
            message = "闹钟提醒"

    # 3) 匹配 "明天早上X点" / "明天上午X点"
    if not remind_at:
        m = re.search(r"明天\s*(早上|上午|下午|晚上)?\s*(\d+)\s*[点:时]?\s*(\d+)?\s*分?", text)
        if m:
            period = m.group(1) or "早上"
            hour = int(m.group(2))
            minute = int(m.group(3)) if m.group(3) else 0
            if period in ("下午", "晚上") and hour < 12:
                hour += 12
            elif period in ("早上", "上午") and hour >= 12:
                hour -= 12
            tomorrow = now + timedelta(days=1)
            remind_at = tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
            remainder = re.sub(r"明天.*?(?:点|时|分)", "", text, count=1)
            message = re.sub(r"提醒我", "", remainder).strip() or "闹钟提醒"

    # 4) 匹配 "今天晚上X点" / "今晚X点"
    if not remind_at:
        m = re.search(r"(今晚|今天晚上|今天下午)\s*(\d+)\s*[点:时]?\s*(\d+)?\s*分?", text)
        if m:
            hour = int(m.group(2))
            minute = int(m.group(3)) if m.group(3) else 0
            if "下午" in m.group(1) or "晚上" in m.group(1):
                if hour < 12:
                    hour += 12
            remind_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
            if remind_at < _now_str():
                # 如果今天已经过了，就是明天
                remind_at = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
            remainder = re.sub(r"(今晚|今天晚上|今天下午).*?(?:点|时|分)", "", text, count=1)
            message = re.sub(r"提醒我", "", remainder).strip() or "闹钟提醒"

    # 5) 匹配 "X点X分"（今天）
    if not remind_at:
        m = re.search(r"(\d+)\s*[点:时]\s*(\d+)\s*分", text)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2))
            remind_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
            if remind_at < _now_str():
                remind_at = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
            message = re.sub(r"提醒我", "", text).strip()

    if not remind_at:
        # 兜底：无法解析时间，设置为30分钟后
        remind_at = (now + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M")
        message = text.replace("提醒我", "").strip() or "闹钟提醒"

    # 清理闹钟内容
    message = message.strip("，。！？,.!?")
    if not message:
        message = "闹钟提醒"
    if len(message) > 100:
        message = message[:100]

    return {"has_alarm": True, "remind_at": remind_at, "message": message}


# ============ 3. 后台检查线程 ============

_alarm_thread_started = False


def _check_alarms_loop():
    """后台线程：每分钟检查一次到期闹钟，发送推送通知。"""
    while True:
        try:
            due_alarms = get_due_alarms()
            for alarm in due_alarms:
                try:
                    # 发送推送通知
                    import push_notification
                    push_notification.send_push_to_user(
                        alarm["user_phone"],
                        title="⏰ EcoWise 闹钟提醒",
                        body=alarm["message"],
                        tag="alarm",
                        require_interaction=True,
                    )
                    mark_notified(alarm["id"])
                    print(f"[闹钟] 已发送提醒: {alarm['user_phone']} - {alarm['message']}")
                except Exception as e:
                    print(f"[闹钟] 发送失败: {e}")
                    # 即使发送失败也标记为已通知，避免重复发送
                    try:
                        mark_notified(alarm["id"])
                    except Exception:
                        pass
        except Exception as e:
            print(f"[闹钟] 检查线程异常: {e}")

        time.sleep(60)  # 每分钟检查一次


def start_alarm_thread():
    """启动闹钟后台检查线程（全局只启动一次）。"""
    global _alarm_thread_started
    if _alarm_thread_started:
        return
    _alarm_thread_started = True
    thread = threading.Thread(target=_check_alarms_loop, daemon=True)
    thread.start()
    print("[闹钟] 后台检查线程已启动")


# ============ 4. API 路由注册 ============

def register_alarm_routes(app):
    """向 Flask app 注册闹钟相关 API 路由。"""

    @app.route('/api/alarms', methods=['GET'])
    def api_get_alarms():
        """获取当前用户的闹钟列表。"""
        from flask import jsonify, session
        user_phone = session.get('phone', '')
        alarms = get_user_alarms(user_phone)
        return jsonify({"alarms": alarms})

    @app.route('/api/alarms/add', methods=['POST'])
    def api_add_alarm():
        """手动添加闹钟。"""
        from flask import jsonify, request, session
        try:
            data = request.get_json() or {}
            user_phone = session.get('phone', '')
            remind_at = data.get('remind_at', '')
            message = data.get('message', '闹钟提醒')
            if not user_phone:
                return jsonify({"success": False, "message": "未登录"}), 401
            if not remind_at:
                return jsonify({"success": False, "message": "缺少提醒时间"}), 400
            result = add_alarm(user_phone, remind_at, message)
            return jsonify(result)
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500

    @app.route('/api/alarms/delete', methods=['POST'])
    def api_delete_alarm():
        """删除指定闹钟。"""
        from flask import jsonify, request, session
        try:
            data = request.get_json() or {}
            alarm_id = data.get('id')
            user_phone = session.get('phone', '')
            if not alarm_id:
                return jsonify({"success": False, "message": "缺少闹钟ID"}), 400
            ok = delete_alarm(alarm_id, user_phone)
            return jsonify({"success": ok, "message": "已删除" if ok else "删除失败"})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500

    @app.route('/api/alarms/parse', methods=['POST'])
    def api_parse_alarm():
        """解析自然语言中的闹钟意图（AI 辅助）。"""
        from flask import jsonify, request
        try:
            data = request.get_json() or {}
            text = data.get('text', '')
            if not text:
                return jsonify({"success": False, "message": "缺少文本"}), 400
            result = parse_alarm_from_text(text)
            return jsonify(result)
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500