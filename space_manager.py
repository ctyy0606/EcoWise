"""
EcoWise 宿舍助理 - 空间管理模块
================================
用户可创建"空间"（如一个宿舍），通过手机号邀请室友加入。
空间内成员可查看电费分摊（每人所有设备电费总和，不显示具体设备）。
实时数据和历史用电仍为私有，仅本人可见。
"""
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", os.path.expanduser("~"))), "Ecowise", "energy_log.db")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS spaces (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            creator_phone TEXT NOT NULL,
            created_at    TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS space_members (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            space_id      INTEGER NOT NULL,
            member_phone  TEXT NOT NULL,
            alias         TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'pending',
            invited_at    TEXT NOT NULL,
            joined_at     TEXT,
            UNIQUE(space_id, member_phone),
            FOREIGN KEY(space_id) REFERENCES spaces(id)
        )
    """)
    conn.commit()
    return conn


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_space(name, creator_phone):
    """创建空间，创建者自动成为 accepted 成员。返回 (success, message, space_id)"""
    name = name.strip()
    if not name:
        return False, "空间名称不能为空", None
    if not creator_phone:
        return False, "未登录", None

    conn = _get_db()
    try:
        cur = conn.execute(
            "INSERT INTO spaces (name, creator_phone, created_at) VALUES (?, ?, ?)",
            (name, creator_phone, _now()),
        )
        space_id = cur.lastrowid
        conn.execute(
            "INSERT INTO space_members (space_id, member_phone, alias, status, invited_at, joined_at) VALUES (?, ?, '', 'accepted', ?, ?)",
            (space_id, creator_phone, _now(), _now()),
        )
        conn.commit()
        return True, "空间创建成功", space_id
    except Exception as e:
        return False, f"创建失败: {e}", None
    finally:
        conn.close()


def invite_member(space_id, inviter_phone, invitee_phone, alias=""):
    """
    邀请室友加入空间。
    - 检查邀请者是否是空间成员
    - 检查被邀请者是否已注册
    - 检查是否已在空间中或已有待处理邀请
    返回 (success, message)
    """
    if not invitee_phone:
        return False, "请输入手机号"
    if inviter_phone == invitee_phone:
        return False, "不能邀请自己"

    conn = _get_db()
    try:
        space = conn.execute("SELECT creator_phone FROM spaces WHERE id=?", (space_id,)).fetchone()
        if not space:
            return False, "空间不存在"

        inviter = conn.execute(
            "SELECT status FROM space_members WHERE space_id=? AND member_phone=?",
            (space_id, inviter_phone),
        ).fetchone()
        if not inviter or inviter[0] != 'accepted':
            return False, "你不是该空间成员，无权邀请"

        existing = conn.execute(
            "SELECT status FROM space_members WHERE space_id=? AND member_phone=?",
            (space_id, invitee_phone),
        ).fetchone()
        if existing:
            if existing[0] == 'accepted':
                return False, "该用户已是空间成员"
            elif existing[0] == 'pending':
                return False, "已向该用户发送过邀请，请等待对方确认"
            elif existing[0] == 'rejected':
                conn.execute(
                    "UPDATE space_members SET alias=?, status='pending', invited_at=? WHERE space_id=? AND member_phone=?",
                    (alias.strip(), _now(), space_id, invitee_phone),
                )
                conn.commit()
                return True, "已重新发送邀请"

        import user_auth
        if not user_auth.user_exists(invitee_phone):
            return False, "该手机号未注册"

        conn.execute(
            "INSERT INTO space_members (space_id, member_phone, alias, status, invited_at) VALUES (?, ?, ?, 'pending', ?)",
            (space_id, invitee_phone, alias.strip(), _now()),
        )
        conn.commit()
        return True, "邀请已发送"
    except Exception as e:
        return False, f"邀请失败: {e}"
    finally:
        conn.close()


def get_pending_invitations(phone):
    """获取某用户收到的所有待处理邀请。返回列表，每项含 space_id, space_name, inviter_nickname, invited_at"""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT sm.id, sm.space_id, s.name, s.creator_phone, sm.invited_at
               FROM space_members sm
               JOIN spaces s ON sm.space_id = s.id
               WHERE sm.member_phone=? AND sm.status='pending'
               ORDER BY sm.invited_at DESC""",
            (phone,),
        ).fetchall()

        import user_auth
        result = []
        for row in rows:
            inviter_nick = user_auth.get_nickname(row[3]) or row[3]
            result.append({
                "invitation_id": row[0],
                "space_id": row[1],
                "space_name": row[2],
                "inviter": inviter_nick,
                "invited_at": row[4],
            })
        return result
    finally:
        conn.close()


def respond_invitation(invitation_id, phone, accept):
    """
    接受或拒绝邀请。
    返回 (success, message)
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT space_id, status FROM space_members WHERE id=? AND member_phone=?",
            (invitation_id, phone),
        ).fetchone()
        if not row:
            return False, "邀请不存在"
        if row[1] != 'pending':
            return False, "该邀请已处理"

        if accept:
            conn.execute(
                "UPDATE space_members SET status='accepted', joined_at=? WHERE id=?",
                (_now(), invitation_id),
            )
            conn.commit()
            return True, "已加入空间"
        else:
            conn.execute(
                "UPDATE space_members SET status='rejected' WHERE id=?",
                (invitation_id,),
            )
            conn.commit()
            return True, "已拒绝邀请"
    except Exception as e:
        return False, f"操作失败: {e}"
    finally:
        conn.close()


def get_user_spaces(phone):
    """获取用户已加入的所有空间（含自己创建的）。返回列表"""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT s.id, s.name, s.creator_phone, s.created_at, sm.joined_at
               FROM spaces s
               JOIN space_members sm ON s.id = sm.space_id
               WHERE sm.member_phone=? AND sm.status='accepted'
               ORDER BY s.created_at DESC""",
            (phone,),
        ).fetchall()
        result = []
        for row in rows:
            result.append({
                "space_id": row[0],
                "space_name": row[1],
                "is_creator": row[2] == phone,
                "created_at": row[3],
                "joined_at": row[4],
            })
        return result
    finally:
        conn.close()


def get_space_members(space_id, requester_phone):
    """
    获取空间成员列表（仅 accepted 成员可见）。
    返回 (success, members) 或 (False, message)
    每个成员含: phone, nickname, alias, role(creator/member), joined_at
    """
    conn = _get_db()
    try:
        requester = conn.execute(
            "SELECT status FROM space_members WHERE space_id=? AND member_phone=?",
            (space_id, requester_phone),
        ).fetchone()
        if not requester or requester[0] != 'accepted':
            return False, "你不是该空间成员，无权查看"

        space = conn.execute("SELECT creator_phone FROM spaces WHERE id=?", (space_id,)).fetchone()
        if not space:
            return False, "空间不存在"
        creator_phone = space[0]

        rows = conn.execute(
            """SELECT member_phone, alias, joined_at
               FROM space_members
               WHERE space_id=? AND status='accepted'
               ORDER BY joined_at ASC""",
            (space_id,),
        ).fetchall()

        import user_auth
        members = []
        for row in rows:
            phone = row[0]
            nickname = user_auth.get_nickname(phone) or phone
            alias = row[1]
            members.append({
                "phone": phone,
                "nickname": nickname,
                "alias": alias,
                "display_name": alias if alias else nickname,
                "role": "creator" if phone == creator_phone else "member",
                "joined_at": row[2],
            })
        return True, members
    finally:
        conn.close()


def set_member_alias(space_id, setter_phone, member_phone, alias):
    """
    设置成员备注名（仅空间创建者可设置）。
    返回 (success, message)
    """
    conn = _get_db()
    try:
        space = conn.execute("SELECT creator_phone FROM spaces WHERE id=?", (space_id,)).fetchone()
        if not space:
            return False, "空间不存在"
        if space[0] != setter_phone:
            return False, "仅空间创建者可设置备注名"

        member = conn.execute(
            "SELECT status FROM space_members WHERE space_id=? AND member_phone=?",
            (space_id, member_phone),
        ).fetchone()
        if not member or member[0] != 'accepted':
            return False, "该成员不存在或未加入"

        conn.execute(
            "UPDATE space_members SET alias=? WHERE space_id=? AND member_phone=?",
            (alias.strip(), space_id, member_phone),
        )
        conn.commit()
        return True, "备注名已更新"
    except Exception as e:
        return False, f"设置失败: {e}"
    finally:
        conn.close()


def get_space_bill(space_id, requester_phone):
    """
    计算空间内所有成员的电费分摊（每人所有设备电费总和）。
    返回 (success, result) 或 (False, message)
    result 含: space_name, total_kwh, total_yuan, per_person (每人: display_name, kwh, yuan)
    """
    ok, members = get_space_members(space_id, requester_phone)
    if not ok:
        return False, members

    conn = _get_db()
    try:
        space = conn.execute("SELECT name FROM spaces WHERE id=?", (space_id,)).fetchone()
        space_name = space[0] if space else "未知空间"
    finally:
        conn.close()

    import device_client
    import billing
    import config

    all_devices = device_client.get_all_devices()

    member_nicknames = {m["nickname"]: m for m in members}

    person_kwh = {}
    for dev in all_devices:
        if "error" in dev:
            continue
        owner_nick = dev.get("owner", "")
        if owner_nick not in member_nicknames:
            continue
        kwh = dev.get("energy_kwh")
        if kwh is None:
            continue
        m = member_nicknames[owner_nick]
        display = m["display_name"]
        person_kwh[display] = person_kwh.get(display, 0) + kwh

    per_person = []
    total_kwh = 0.0
    for display, kwh in person_kwh.items():
        yuan = kwh * config.ELECTRICITY_PRICE_PER_KWH
        per_person.append({
            "name": display,
            "kwh": round(kwh, 4),
            "yuan": round(yuan, 2),
        })
        total_kwh += kwh

    per_person.sort(key=lambda x: x["yuan"], reverse=True)

    for m in members:
        display = m["display_name"]
        if display not in person_kwh:
            per_person.append({
                "name": display,
                "kwh": 0,
                "yuan": 0,
            })

    return True, {
        "space_name": space_name,
        "total_kwh": round(total_kwh, 4),
        "total_yuan": round(total_kwh * config.ELECTRICITY_PRICE_PER_KWH, 2),
        "per_person": per_person,
        "price_per_kwh": config.ELECTRICITY_PRICE_PER_KWH,
    }


def leave_space(space_id, phone):
    """
    离开空间。创建者不能离开（需先删除空间）。
    返回 (success, message)
    """
    conn = _get_db()
    try:
        space = conn.execute("SELECT creator_phone FROM spaces WHERE id=?", (space_id,)).fetchone()
        if not space:
            return False, "空间不存在"
        if space[0] == phone:
            return False, "创建者不能离开空间，请先删除空间"

        member = conn.execute(
            "SELECT status FROM space_members WHERE space_id=? AND member_phone=?",
            (space_id, phone),
        ).fetchone()
        if not member:
            return False, "你不在该空间中"
        if member[0] != 'accepted':
            return False, "你尚未加入该空间"

        conn.execute(
            "DELETE FROM space_members WHERE space_id=? AND member_phone=?",
            (space_id, phone),
        )
        conn.commit()
        return True, "已离开空间"
    except Exception as e:
        return False, f"操作失败: {e}"
    finally:
        conn.close()


def delete_space(space_id, phone):
    """
    删除空间（仅创建者可删除）。
    返回 (success, message)
    """
    conn = _get_db()
    try:
        space = conn.execute("SELECT creator_phone FROM spaces WHERE id=?", (space_id,)).fetchone()
        if not space:
            return False, "空间不存在"
        if space[0] != phone:
            return False, "仅创建者可删除空间"

        conn.execute("DELETE FROM space_members WHERE space_id=?", (space_id,))
        conn.execute("DELETE FROM spaces WHERE id=?", (space_id,))
        conn.commit()
        return True, "空间已删除"
    except Exception as e:
        return False, f"删除失败: {e}"
    finally:
        conn.close()
