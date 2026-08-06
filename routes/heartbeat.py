"""Presence/heartbeat and lightweight user-info lookup for chat headers"""
from flask import Blueprint, request, jsonify
from database_helper import get_db

heartbeat_bp = Blueprint('heartbeat', __name__, url_prefix='/api')

ONLINE_THRESHOLD_SECONDS = 60


@heartbeat_bp.route("/heartbeat", methods=["GET", "POST"])
def api_heartbeat():
    # GET requests (from UptimeRobot or browser) — just return alive status
    if request.method == "GET":
        return jsonify({"success": True, "status": "alive"})

    # POST requests (from dashboard JS) — update last_seen_at
    data    = request.get_json(silent=True) or {}
    user_id = str(data.get("user_id", "")).strip()
    if not user_id:
        return jsonify({"success": False}), 400

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("UPDATE users SET last_seen_at = NOW() WHERE id = %s", (user_id,))
        conn.commit()
        return jsonify({"success": True})
    finally:
        cur.close()
        conn.close()


@heartbeat_bp.route("/user-status")
def api_user_status():
    """Get a user's basic public info + online/offline status — for chat headers."""
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT id, name, role, trade, profile_photo_path, last_seen_at
               FROM users WHERE id = %s""",
            (user_id,)
        )
        u = cur.fetchone()
        if not u:
            return jsonify({"error": "User not found"}), 404

        online = False
        if u["last_seen_at"]:
            cur.execute("SELECT TIMESTAMPDIFF(SECOND, %s, NOW()) AS diff", (u["last_seen_at"],))
            diff = cur.fetchone()["diff"]
            online = diff is not None and diff <= ONLINE_THRESHOLD_SECONDS

        return jsonify({
            "id": u["id"], "name": u["name"], "role": u["role"], "trade": u["trade"],
            "profile_photo_path": u["profile_photo_path"],
            "online": online,
            "last_seen_at": str(u["last_seen_at"]) if u["last_seen_at"] else None
        })
    finally:
        cur.close()
        conn.close()