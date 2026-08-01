"""Authentication routes"""
from flask import Blueprint, request, render_template, session, redirect, url_for, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import random
from database_helper import get_db
from utils import send_reset_email

auth_bp = Blueprint('auth', __name__)

WORKER_DASHBOARD = "https://skillchain-frontend-omega.vercel.app//Worker_dashboard/index.html"
CLIENT_DASHBOARD = "https://skillchain-frontend-omega.vercel.app//Client_dashboard/index.html"


@auth_bp.route("/", methods=["GET"])
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["POST"])
def login():
    data     = request.get_json(silent=True) or {}
    email    = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"success": False, "message": "Email and password are required."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT id, name, email, role, active_role, has_client_profile, password_hash
           FROM users WHERE email = %s""",
        (email,)
    )
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not (user and check_password_hash(user["password_hash"], password)):
        return jsonify({"success": False, "message": "Invalid email or password."}), 401

    # ── Resolve where to send them ────────────────────────────────────────────
    primary_role       = user["role"]                     # permanent from registration
    has_client_profile = bool(user.get("has_client_profile", 0))
    last_active_role   = user.get("active_role") or primary_role

    session["user_id"]     = user["id"]
    session["role"]        = primary_role
    session["active_role"] = last_active_role

    base_user = {
        "id":                 user["id"],
        "name":               user["name"],
        "role":               primary_role,
        "email":              user["email"],
        "has_client_profile": has_client_profile,
        "active_role":        last_active_role,
    }

    # ── Case 1: pure client — no artisan profile, go straight to client ──────
    if primary_role == "client" and not has_client_profile:
        return jsonify({
            "success":  True,
            "redirect": CLIENT_DASHBOARD,
            "user":     base_user,
            "pick_role": False,
        })

    # ── Case 2: artisan with NO client profile — go straight to worker ───────
    if primary_role == "worker" and not has_client_profile:
        return jsonify({
            "success":  True,
            "redirect": WORKER_DASHBOARD,
            "user":     base_user,
            "pick_role": False,
        })

    # ── Case 3: artisan WITH client profile — ask which mode ─────────────────
    # We still respect last_active_role for auto-redirect if preferred,
    # but we send pick_role: true so the frontend can show the picker
    # instead of redirecting blindly. The redirect field is the fallback
    # in case the frontend ignores pick_role.
    fallback_redirect = (
        CLIENT_DASHBOARD if last_active_role == "client" else WORKER_DASHBOARD
    )
    return jsonify({
        "success":         True,
        "redirect":        fallback_redirect,
        "user":            base_user,
        "pick_role":       True,   # ← frontend shows picker
        "last_active_role": last_active_role,
        "worker_url":      WORKER_DASHBOARD,
        "client_url":      CLIENT_DASHBOARD,
    })


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    name     = request.form.get("name", "").strip()
    email    = request.form.get("email", "").strip().lower()
    phone    = request.form.get("phone", "").strip()
    password = request.form.get("password", "")
    role     = request.form.get("role", "worker")
    trade    = request.form.get("trade", None)

    errors = {}
    if not name or len(name) < 2:
        errors["name"] = "Please enter your full name."
    if not email or "@" not in email:
        errors["email"] = "Please enter a valid email address."
    if not password or len(password) < 6:
        errors["password"] = "Password must be at least 6 characters."
    if role == "worker" and not trade:
        errors["trade"] = "Please select your trade."
    if role == "client":
        trade = None
    if errors:
        return jsonify({"success": False, "errors": errors}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            return jsonify({"success": False,
                            "errors": {"email": "This email is already registered."}}), 409

        pw_hash = generate_password_hash(password)
        cur.execute(
            """INSERT INTO users
               (name, email, password_hash, role, phone, trade, has_client_profile, active_role)
               VALUES (%s, %s, %s, %s, %s, %s, 0, NULL)""",
            (name, email, pw_hash, role, phone, trade)
        )
        conn.commit()

        return jsonify({
            "success":  True,
            "redirect": "https://skillchain-frontend-omega.vercel.app//Login/index.html",
        })

    except Exception as e:
        conn.rollback()
        print(f"Register error: {e}")
        return jsonify({"success": False,
                        "errors": {"general": "Server error. Please try again."}}), 500
    finally:
        cur.close()
        conn.close()


@auth_bp.route("/api/me")
def api_me():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT id, name, email, role, active_role, has_client_profile,
                  trust_score, jobs_completed
           FROM users WHERE id = %s""",
        (session["user_id"],)
    )
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user:
        return jsonify({"error": "User not found"}), 404

    user["active_role"] = user["active_role"] or user["role"]
    return jsonify(user)


@auth_bp.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    role = session.get("active_role") or session.get("role", "worker")
    return redirect(WORKER_DASHBOARD if role == "worker" else CLIENT_DASHBOARD)


@auth_bp.route("/logout-api", methods=["POST"])
def logout_api():
    session.clear()
    return jsonify({"success": True})


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html")

    data  = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    if not email:
        return jsonify({"success": False, "message": "Email is required."}), 400

    try:
        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        cur.execute("SELECT id, name FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        if not user:
            cur.close(); conn.close()
            return jsonify({"success": True,
                            "message": "If that email exists, a reset code has been sent."})

        token  = str(random.randint(100000, 999999))
        expiry = datetime.now() + timedelta(minutes=10)
        cur.execute(
            "INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES (%s,%s,%s)",
            (user["id"], token, expiry)
        )
        conn.commit()
        cur.close(); conn.close()
        send_reset_email(email, token, user.get("name", "User"))
        return jsonify({"success": True,
                        "message": "If that email exists, a reset code has been sent."})
    except Exception as e:
        print(f"[forgot-password error] {e}")
        return jsonify({"success": False, "message": "Server error."}), 500


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    token = request.args.get("token") or (request.get_json(silent=True) or {}).get("token")
    if request.method == "GET":
        return render_template("reset_password.html", token=token)

    data     = request.get_json(silent=True) or {}
    token    = data.get("token", "")
    password = data.get("password", "")
    if not token or not password or len(password) < 6:
        return jsonify({"success": False, "message": "Invalid request."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM password_reset_tokens WHERE token=%s AND used=0", (token,))
    record = cur.fetchone()
    if not record or datetime.now() > record["expires_at"]:
        cur.close(); conn.close()
        return jsonify({"success": False, "message": "Token expired or already used."}), 400

    try:
        pw_hash = generate_password_hash(password)
        cur.execute("UPDATE users SET password_hash=%s WHERE id=%s", (pw_hash, record["user_id"]))
        cur.execute("UPDATE password_reset_tokens SET used=1 WHERE id=%s", (record["id"],))
        conn.commit()
        return jsonify({"success": True,
                        "redirect": "https://skillchain-frontend-omega.vercel.app//Login/index.html"})
    except Exception as e:
        print(f"[reset-password error] {e}")
        return jsonify({"success": False, "message": "Server error."}), 500
    finally:
        cur.close(); conn.close()


@auth_bp.route("/reset-password-final", methods=["POST"])
def reset_password_final():
    data     = request.get_json(silent=True) or {}
    email    = data.get("email", "").strip().lower()
    token    = data.get("token", "")
    password = data.get("password", "")
    if not all([email, token, password]) or len(password) < 6:
        return jsonify({"success": False, "message": "Invalid request."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT prt.* FROM password_reset_tokens prt
           JOIN users u ON u.id = prt.user_id
           WHERE u.email=%s AND prt.token=%s AND prt.used=0""",
        (email, token)
    )
    record = cur.fetchone()
    if not record or datetime.now() > record["expires_at"]:
        cur.close(); conn.close()
        return jsonify({"success": False, "message": "Token expired or invalid."}), 400

    try:
        pw_hash = generate_password_hash(password)
        cur.execute("UPDATE users SET password_hash=%s WHERE id=%s", (pw_hash, record["user_id"]))
        cur.execute("UPDATE password_reset_tokens SET used=1 WHERE id=%s", (record["id"],))
        conn.commit()
        return jsonify({"success": True,
                        "redirect": "https://skillchain-frontend-omega.vercel.app//Login/index.html"})
    except Exception as e:
        print(f"[reset-password-final error] {e}")
        return jsonify({"success": False, "message": "Server error."}), 500
    finally:
        cur.close(); conn.close()