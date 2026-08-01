"""Role switching routes — lets a user toggle between artisan and client context"""
from flask import Blueprint, request, jsonify, session
from database_helper import get_db

switch_bp = Blueprint('switch', __name__, url_prefix='/api')

WORKER_DASHBOARD = "https://skillchain-frontend-omega.vercel.app//Worker_dashboard/index.html"
CLIENT_DASHBOARD = "https://skillchain-frontend-omega.vercel.app//Client_dashboard/index.html"


@switch_bp.route("/switch-role/status", methods=["GET"])
def switch_role_status():
    """
    Frontend calls this on dashboard load to know:
     - what the user's current active role is
     - whether they already have a client profile (so we know if we need the modal)
    """
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT id, name, role, active_role, has_client_profile FROM users WHERE id = %s",
        (user_id,)
    )
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user:
        return jsonify({"error": "User not found"}), 404

    # Resolve the role the user is currently operating as
    current_role = user["active_role"] or user["role"]

    return jsonify({
        "user_id":            user["id"],
        "primary_role":       user["role"],           # permanent, from registration
        "active_role":        current_role,           # what they're acting as right now
        "has_client_profile": bool(user["has_client_profile"]),
        "needs_setup":        not bool(user["has_client_profile"]) and user["role"] == "worker"
    })


@switch_bp.route("/switch-role/activate-client", methods=["POST"])
def activate_client():
    """
    Called when a worker confirms they want to create a client profile
    (first-time only — the 'Use same credentials' confirmation step).
    Sets has_client_profile = 1 and switches active_role to 'client'.
    """
    data    = request.get_json(silent=True) or {}
    user_id = str(data.get("user_id", "")).strip()

    if not user_id:
        return jsonify({"success": False, "message": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id, role, has_client_profile FROM users WHERE id = %s",
            (user_id,)
        )
        user = cur.fetchone()
        if not user:
            return jsonify({"success": False, "message": "User not found"}), 404

        if user["role"] != "worker":
            return jsonify({"success": False, "message": "Only artisans can activate a client profile"}), 400

        # Mark client profile as activated + switch active role
        cur.execute(
            "UPDATE users SET has_client_profile = 1, active_role = 'client' WHERE id = %s",
            (user_id,)
        )
        conn.commit()

        # Update session so server-side checks reflect the switch
        session["active_role"] = "client"

        return jsonify({
            "success":   True,
            "role":      "client",
            "redirect":  CLIENT_DASHBOARD,
            "message":   "Client profile activated! Redirecting…"
        })

    except Exception as e:
        conn.rollback()
        print(f"[activate-client error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@switch_bp.route("/switch-role/switch", methods=["POST"])
def switch_role():
    """
    Fast-path switch for users who already have both profiles.
    No confirmation needed — just flip active_role and return the redirect URL.
    """
    data      = request.get_json(silent=True) or {}
    user_id   = str(data.get("user_id", "")).strip()
    target    = data.get("target_role", "").strip()   # 'worker' or 'client'

    if not user_id or target not in ("worker", "client"):
        return jsonify({"success": False, "message": "user_id and target_role required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id, role, has_client_profile FROM users WHERE id = %s",
            (user_id,)
        )
        user = cur.fetchone()
        if not user:
            return jsonify({"success": False, "message": "User not found"}), 404

        # Guard: can only switch to client if profile exists
        if target == "client" and not user["has_client_profile"]:
            return jsonify({
                "success":    False,
                "needs_setup": True,
                "message":    "Client profile not yet activated"
            }), 400

        cur.execute(
            "UPDATE users SET active_role = %s WHERE id = %s",
            (target, user_id)
        )
        conn.commit()
        session["active_role"] = target

        redirect_url = CLIENT_DASHBOARD if target == "client" else WORKER_DASHBOARD

        return jsonify({
            "success":  True,
            "role":     target,
            "redirect": redirect_url
        })

    except Exception as e:
        conn.rollback()
        print(f"[switch-role error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@switch_bp.route("/switch-role/me", methods=["GET"])
def switch_role_me():
    """
    Lightweight endpoint called by BOTH dashboards on load.
    Returns the user's active role so the UI can show/hide the switch button
    and enforce the 'client can't accept jobs' rule.
    """
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT id, name, email, role, active_role, has_client_profile,
                  trust_score, trade
           FROM users WHERE id = %s""",
        (user_id,)
    )
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user:
        return jsonify({"error": "Not found"}), 404

    current_role = user["active_role"] or user["role"]
    return jsonify({
        "id":                 user["id"],
        "name":               user["name"],
        "email":              user["email"],
        "primary_role":       user["role"],
        "active_role":        current_role,
        "has_client_profile": bool(user["has_client_profile"]),
        "trade":              user["trade"],
        "trust_score":        float(user["trust_score"] or 0),
        # convenience flags for UI
        "can_switch_to_client": user["role"] == "worker",
        "can_switch_to_worker": current_role == "client" and user["role"] == "worker",
    })