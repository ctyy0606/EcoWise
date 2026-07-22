"""
EcoWise 宿舍备忘录模块
======================
用户可以在网页或AI助手中添加备忘录：
- "2026年7月25日下午3点去图书馆"
- "下周一下午开会"
- "明天早上交作业"
- 不指定时间时，默认早上9点提醒

备忘录到期时通过浏览器推送通知提醒用户。
"""

import os
import sqlite3
import json
import threading
import time
import re
from datetime import datetime, timedelta, date

DB_PATH = os.path.join(
    os.environ.get("TEMP", os.environ.get("TMP", os.path.expanduser("~"))),
    "Ecowise", "energy_log.db"
)


# ============ 数据库操作 ============

def _get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    max_retries = 3
    last_error = None
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            break
        except sqlite3.OperationalError as e:
            last_error = e
            print(f"[memo] Database connection attempt {attempt+1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))
            else:
                raise last_error
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_phone  TEXT NOT NULL,
            memo_date   TEXT NOT NULL,        -- 日期 "YYYY-MM-DD"
            memo_time   TEXT NOT NULL,         -- 时间 "HH:MM"
            content     TEXT NOT NULL,         -- 备忘录内容
            created_at  TEXT NOT NULL,
            notified    INTEGER NOT NULL DEFAULT 0  -- 0=未通知, 1=已通知
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_memos_due
        ON memos(notified, memo_date, memo_time)
    """)
    conn.commit()
    return conn


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today_str():
    return date.today().strftime("%Y-%m-%d")


def add_memo(user_phone, memo_date, memo_time, content):
    """添加一条备忘录。

    Args:
        user_phone: 用户手机号
        memo_date: 日期 "YYYY-MM-DD"
        memo_time: 时间 "HH:MM"
        content: 备忘录内容

    Returns:
        dict: {"success": bool, "message": str, "id": int or None}
    """
    try:
        # 验证日期时间格式
        dt = datetime.strptime(f"{memo_date} {memo_time}", "%Y-%m-%d %H:%M")
        if dt < datetime.now():
            return {"success": False, "message": "提醒时间不能早于当前时间", "id": None}

        conn = _get_db()
        try:
            cursor = conn.execute(
                "INSERT INTO memos (user_phone, memo_date, memo_time, content, created_at, notified) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (user_phone, memo_date, memo_time, content, _now_str()),
            )
            conn.commit()
            alarm_id = cursor.lastrowid
            return {
                "success": True,
                "message": f"备忘录已添加：{memo_date} {memo_time} 提醒你「{content}」",
                "id": alarm_id,
            }
        finally:
            conn.close()
    except ValueError:
        return {"success": False, "message": "日期时间格式错误", "id": None}


def get_due_memos():
    """获取所有到期但未通知的备忘录。

    Returns:
        list of dict: [{"id", "user_phone", "content", "memo_date", "memo_time"}, ...]
    """
    now = _now_str()
    today = _now_str()[:10]
    now_time = _now_str()[11:16]
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, user_phone, content, memo_date, memo_time FROM memos "
            "WHERE notified = 0 AND (memo_date < ? OR (memo_date = ? AND memo_time <= ?))",
            (today, today, now_time),
        ).fetchall()
        return [
            {
                "id": r[0],
                "user_phone": r[1],
                "content": r[2],
                "memo_date": r[3],
                "memo_time": r[4],
            }
            for r in rows
        ]
    finally:
        conn.close()


def mark_notified(memo_id):
    """将指定备忘录标记为已通知。"""
    conn = _get_db()
    try:
        conn.execute("UPDATE memos SET notified = 1 WHERE id = ?", (memo_id,))
        conn.commit()
    finally:
        conn.close()


def get_user_memos(user_phone, limit=20):
    """获取用户最近的备忘录列表。"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, memo_date, memo_time, content, created_at, notified "
            "FROM memos WHERE user_phone = ? "
            "ORDER BY memo_date DESC, memo_time DESC LIMIT ?",
            (user_phone, limit),
        ).fetchall()
        return [
            {
                "id": r[0],
                "memo_date": r[1],
                "memo_time": r[2],
                "content": r[3],
                "created_at": r[4],
                "notified": bool(r[5]),
            }
            for r in rows
        ]
    finally:
        conn.close()


def delete_memo(memo_id, user_phone):
    """删除指定备忘录（仅限本人）。"""
    conn = _get_db()
    try:
        conn.execute(
            "DELETE FROM memos WHERE id = ? AND user_phone = ?",
            (memo_id, user_phone),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


# ============ 自然语言解析 ============

_CN_NUM_MAP = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10,
}


def _cn_to_num(text):
    """将中文数字转为阿拉伯数字，如"二十五" → 25。"""
    try:
        return int(text)
    except ValueError:
        pass
    # 处理中文数字
    if text in _CN_NUM_MAP:
        return _CN_NUM_MAP[text]
    # 处理"十"、"二十"等
    total = 0
    for ch in text:
        if ch in _CN_NUM_MAP:
            total = total * 10 + _CN_NUM_MAP[ch] if total < 10 else total + _CN_NUM_MAP[ch]
    return total if total > 0 else None


def parse_memo_from_text(text):
    """从自然语言中解析备忘录意图。

    支持格式：
    - "2026年7月25日下午3点去图书馆"
    - "下周一下午开会"
    - "明天早上交作业"
    - "今天下午5点打球"
    - "7月25日去北京"  → 默认早上9点
    - "下周五开会"      → 默认早上9点

    Returns:
        dict: {
            "has_memo": bool,
            "memo_date": "YYYY-MM-DD" or None,
            "memo_time": "HH:MM" or None,
            "content": str or None,
            "default_time_used": bool  # 是否使用了默认时间
        }
    """
    # 检测是否包含备忘录关键词
    alarm_keywords = ["提醒我", "提醒我", "备忘录", "记一下", "别忘了", "记得"]
    # 检测是否包含日期/时间关键词（可能没有上面的关键词，直接说"明天去图书馆"）
    date_keywords = [
        "今天", "明天", "后天", "昨天", "前天",
        "星期一", "周二", "星期三", "周四", "周五", "周六", "周日",
        "周一", "周二", "周三", "周四", "周五", "周六", "周日",
        "下周一", "下周二", "下周三", "下周四", "下周五", "下周六", "下周日",
        "下周一", "下周二", "下周三", "下周四", "下周五", "下周六", "下周日",
        "今年", "下个月", "下星期",
        "月", "日", "号",
    ]

    has_keyword = any(kw in text for kw in alarm_keywords + date_keywords)
    if not has_keyword:
        return {"has_memo": False, "memo_date": None, "memo_time": None, "content": None, "default_time_used": False}

    now = datetime.now()
    today = date.today()
    memo_date = None
    memo_time = None
    default_time_used = False
    content = text

    # 1. 提取日期
    # 匹配 "YYYY年M月D日" 或 "M月D日"
    date_match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if date_match:
        year = int(date_match.group(1))
        month = int(date_match.group(2))
        day = int(date_match.group(3))
        memo_date = f"{year:04d}-{month:02d}-{day:02d}"
        content = text.replace(date_match.group(0), "").strip()

    if not memo_date:
        date_match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text)
        if date_match:
            month = int(date_match.group(1))
            day = int(date_match.group(2))
            year = today.year
            # 如果月份小于当前月份，可能是明年
            if month < today.month or (month == today.month and day < today.day):
                year += 1
            memo_date = f"{year:04d}-{month:02d}-{day:02d}"
            content = text.replace(date_match.group(0), "").strip()

    # 匹配 "明天"、"后天"、"今天"
    if not memo_date:
        if "后天" in text:
            memo_date = (today + timedelta(days=2)).strftime("%Y-%m-%d")
            content = text.replace("后天", "").strip()
        elif "明天" in text:
            memo_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
            content = text.replace("明天", "").strip()
        elif "今天" in text:
            memo_date = today.strftime("%Y-%m-%d")
            content = text.replace("今天", "").strip()

    # 匹配 "下周一"、"下周二" 等
    if not memo_date:
        weekday_map = {
            "下周一": 0, "下周二": 1, "下周三": 2, "下周四": 3, "下周五": 4, "下周六": 5, "下周日": 6,
            "下星期一": 0, "下星期二": 1, "下星期三": 2, "下星期四": 3, "下星期五": 4, "下星期六": 5, "下星期日": 6,
        }
        for kw, wd in weekday_map.items():
            if kw in text:
                days_ahead = wd - today.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                memo_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
                content = text.replace(kw, "").strip()
                break

    # 匹配 "周一"、"周二" 等（本周）
    if not memo_date:
        weekday_map = {
            "周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6,
            "星期一": 0, "星期二": 1, "星期三": 2, "星期四": 3, "星期五": 4, "星期六": 5, "星期日": 6,
        }
        for kw, wd in weekday_map.items():
            if kw in text:
                days_ahead = wd - today.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                memo_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
                content = text.replace(kw, "").strip()
                break

    # 2. 提取时间
    # 匹配 "下午3点"、"上午10点"、"3点"、"3点半"、"15:30"
    time_match = re.search(r"(早上|上午|中午|下午|晚上|凌晨)?\s*(\d{1,2})\s*[点:：时]\s*(\d{1,2})?\s*分?", text)
    if time_match:
        period = time_match.group(1) or ""
        hour = int(time_match.group(2))
        minute = int(time_match.group(3)) if time_match.group(3) else 0

        if period in ("下午", "晚上") and hour < 12:
            hour += 12
        elif period in ("早上", "上午") and hour >= 12:
            hour -= 12

        if hour >= 24:
            hour = 23
        if minute >= 60:
            minute = 59

        memo_time = f"{hour:02d}:{minute:02d}"
        # 从内容中移除时间部分
        time_str = time_match.group(0)
        content = content.replace(time_str, "").strip()

    # 匹配 "15:30" 格式
    if not memo_time:
        time_match = re.search(r"(\d{1,2}):(\d{2})", text)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
            if hour >= 24:
                hour = 23
            if minute >= 60:
                minute = 59
            memo_time = f"{hour:02d}:{minute:02d}"
            content = content.replace(time_match.group(0), "").strip()

    # 3. 如果没指定时间，默认早上9点
    if not memo_time:
        memo_time = "09:00"
        default_time_used = True

    # 4. 如果没指定日期，默认今天
    if not memo_date:
        memo_date = today.strftime("%Y-%m-%d")

    # 清理内容
    content = content.strip("，。！？,.!?、 ")
    content = content.replace("提醒我", "").replace("备忘录", "").replace("记一下", "").replace("别忘了", "").replace("记得", "").strip()
    # 再去掉首尾标点
    content = content.strip("，。！？,.!?、 ")
    if not content:
        content = "备忘提醒"

    if len(content) > 100:
        content = content[:100]

    # 验证日期时间是否有效
    try:
        dt = datetime.strptime(f"{memo_date} {memo_time}", "%Y-%m-%d %H:%M")
        if dt < now:
            # 如果日期已过，尝试推到下一年或下一周
            if memo_date == today.strftime("%Y-%m-%d") and memo_time and memo_time <= _now_str()[11:16]:
                # 今天的时间已过，推到明天
                memo_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                memo_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    except ValueError:
        memo_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        memo_time = "09:00"

    return {
        "has_memo": True,
        "memo_date": memo_date,
        "memo_time": memo_time,
        "content": content,
        "default_time_used": default_time_used,
    }


# ============ 后台检查线程 ============

_memo_thread_started = False


def send_test_memo(user_phone):
    """手动发送测试备忘录推送。"""
    try:
        import push_notification
        success, result = push_notification.send_push_to_user(
            user_phone,
            title="EcoWise 备忘录测试",
            body="这是一条测试备忘录推送消息。如果你收到这条消息，说明推送功能正常！",
            tag="memo_test",
            require_interaction=True,
        )
        if success:
            print(f"[备忘录] 测试推送已发送至 {user_phone}")
            return {"success": True, "message": "测试推送已发送"}
        else:
            print(f"[备忘录] 测试推送失败 {user_phone}: {result}")
            return {"success": False, "message": f"推送失败: {result}"}
    except Exception as e:
        print(f"[备忘录] 测试推送异常: {e}")
        return {"success": False, "message": f"推送异常: {e}"}


def _check_memos_loop():
    """后台线程：每分钟检查一次到期备忘录，发送推送通知。"""
    while True:
        try:
            due_memos = get_due_memos()
            if due_memos:
                print(f"[备忘录] 发现 {len(due_memos)} 条到期备忘录")
            for memo in due_memos:
                try:
                    import push_notification
                    success, result = push_notification.send_push_to_user(
                        memo["user_phone"],
                        title="EcoWise 备忘录提醒",
                        body=memo["content"],
                        tag="memo",
                        require_interaction=True,
                    )
                    if success:
                        print(f"[备忘录] 已发送提醒: {memo['user_phone']} - {memo['content']}")
                    else:
                        print(f"[备忘录] 发送失败(用户{memo['user_phone']}): {result}")
                    # 无论推送是否成功，都标记为已通知（避免重复尝试）
                    mark_notified(memo["id"])
                except Exception as e:
                    print(f"[备忘录] 发送异常: {e}")
                    try:
                        mark_notified(memo["id"])
                    except Exception:
                        pass
        except Exception as e:
            print(f"[备忘录] 检查线程异常: {e}")
        time.sleep(30)


def start_memo_thread():
    """启动备忘录后台检查线程（全局只启动一次）。"""
    global _memo_thread_started
    if _memo_thread_started:
        return
    _memo_thread_started = True
    thread = threading.Thread(target=_check_memos_loop, daemon=True)
    thread.start()
    print("[备忘录] 后台检查线程已启动")


# ============ API 路由注册 ============

def register_memo_routes(app):
    """向 Flask app 注册备忘录相关 API 路由。"""

    @app.route('/api/memos', methods=['GET'])
    def api_get_memos():
        """获取当前用户的备忘录列表。"""
        from flask import jsonify, session
        user_phone = session.get('phone', '')
        memos = get_user_memos(user_phone)
        return jsonify({"memos": memos})

    @app.route('/api/memos/add', methods=['POST'])
    def api_add_memo():
        """手动添加备忘录。"""
        from flask import jsonify, request, session
        try:
            data = request.get_json() or {}
            user_phone = session.get('phone', '')
            memo_date = data.get('memo_date', '')
            memo_time = data.get('memo_time', '')
            content = data.get('content', '备忘提醒')
            if not user_phone:
                return jsonify({"success": False, "message": "未登录"}), 401
            if not memo_date or not memo_time:
                return jsonify({"success": False, "message": "缺少日期或时间"}), 400
            result = add_memo(user_phone, memo_date, memo_time, content)
            return jsonify(result)
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500

    @app.route('/api/memos/test_push', methods=['POST'])
    def api_test_memo_push():
        """手动测试备忘录推送。"""
        from flask import jsonify, session
        try:
            user_phone = session.get('phone', '')
            if not user_phone:
                return jsonify({"success": False, "message": "未登录"}), 401
            result = send_test_memo(user_phone)
            return jsonify(result)
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500

    @app.route('/api/memos/delete', methods=['POST'])
    def api_delete_memo():
        """删除指定备忘录。"""
        from flask import jsonify, request, session
        try:
            data = request.get_json() or {}
            memo_id = data.get('id')
            user_phone = session.get('phone', '')
            if not memo_id:
                return jsonify({"success": False, "message": "缺少备忘录ID"}), 400
            ok = delete_memo(memo_id, user_phone)
            return jsonify({"success": ok, "message": "已删除" if ok else "删除失败"})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500

    @app.route('/api/memos/parse', methods=['POST'])
    def api_parse_memo():
        """解析自然语言中的备忘录意图（AI 辅助）。"""
        from flask import jsonify, request
        try:
            data = request.get_json() or {}
            text = data.get('text', '')
            if not text:
                return jsonify({"success": False, "message": "缺少文本"}), 400
            result = parse_memo_from_text(text)
            return jsonify(result)
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500