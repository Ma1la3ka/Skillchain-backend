"""In-app notification feed (the bell). Rows are written by push.send_many."""
from flask import Blueprint, request, jsonify, g
from database_helper import get_db

notifs_bp = Blueprint('notifs', __name__, url_prefix='/api/notifications')


def _role():
    r = (request.args.get("role") or (request.get_json(silent=True) or {}).get("role") or "").strip()
    return r if r in ("worker", "client") else "client"


@notifs_bp.route("/list", methods=["GET"])
def api_list():
    limit = min(max(int(request.args.get("limit", 40) or 40), 1), 50)
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""SELECT id, type, title, body, job_id, is_read, TIMESTAMPDIFF(SECOND, created_at, NOW()) AS age
                       FROM user_notifications WHERE user_id = %s AND role = %s ORDER BY id DESC LIMIT %s""",
                    (g.uid, _role(), limit))
        rows = cur.fetchall()
        cur.execute("SELECT COUNT(*) AS c FROM user_notifications WHERE user_id = %s AND role = %s AND is_read = 0",
                    (g.uid, _role()))
        unread = int(cur.fetchone()["c"])
        for r in rows:
            r["is_read"] = bool(r["is_read"])
            r["age"] = int(r["age"] or 0)
        return jsonify({"notifications": rows, "unread": unread})
    finally:
        cur.close()
        conn.close()


@notifs_bp.route("/read", methods=["POST"])
def api_read():
    data = request.get_json(silent=True) or {}
    ids = [int(i) for i in (data.get("ids") or []) if str(i).isdigit()]
    conn = get_db()
    cur = conn.cursor()
    try:
        if data.get("all"):
            cur.execute("UPDATE user_notifications SET is_read = 1 WHERE user_id = %s AND role = %s", (g.uid, _role()))
        elif ids:
            cur.execute(f"UPDATE user_notifications SET is_read = 1 WHERE user_id = %s AND role = %s AND id IN ({','.join(['%s'] * len(ids))})",
                        (g.uid, _role(), *ids))
        conn.commit()
        return jsonify({"success": True})
    finally:
        cur.close()
        conn.close()


@notifs_bp.route("/clear", methods=["POST"])
def api_clear():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM user_notifications WHERE user_id = %s AND role = %s", (g.uid, _role()))
        conn.commit()
        return jsonify({"success": True})
    finally:
        cur.close()
        conn.close()
