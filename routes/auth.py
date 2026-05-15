"""Authentication routes"""
from flask import Blueprint, request, render_template, session, redirect, url_for, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import random
from database_helper import get_db
from utils import squad_create_virtual_account

auth_bp = Blueprint('auth', __name__)


@auth_bp.route("/", methods=["GET"])
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"success": False, "message": "Email and password are required."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name, email, role, password_hash FROM users WHERE email = %s", (email,))
    user = cur.fetchone()
    cur.close()
    conn.close()

    if user and check_password_hash(user["password_hash"], password):
        session["user_id"] = user["id"]
        session["role"] = user["role"]
        redirect_url = (
            "https://skillchain-frontend-omega.vercel.app//Worker_dashboard/index.html"
            if user["role"] == "worker"
            else "https://skillchain-frontend-omega.vercel.app//Client_dashboard/index.html"
        )
        return jsonify({
            "success": True,
            "redirect": redirect_url,
            "user": {
                "id": user["id"],
                "name": user["name"],
                "role": user["role"],
                "email": user["email"]
            }
        })

    return jsonify({"success": False, "message": "Invalid email or password."}), 401


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "worker")
    trade = request.form.get("trade", None)

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
    cur = conn.cursor(dictionary=True)

    try:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            return jsonify({"success": False,
                            "errors": {"email": "This email is already registered."}}), 409

        pw_hash = generate_password_hash(password)
        cur.execute(
            """INSERT INTO users (name, email, password_hash, role, phone, trade)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (name, email, pw_hash, role, phone, trade)
        )
        user_id = cur.lastrowid

        squad_data = None
        if role == "worker":
            squad_data = squad_create_virtual_account(user_id, name, email, phone)
            cur.execute(
                """UPDATE users SET
                   squad_account_number = %s,
                   squad_bank_name      = %s,
                   squad_customer_id    = %s
                   WHERE id = %s""",
                (squad_data["account_number"], squad_data["bank_name"],
                 squad_data["customer_id"], user_id)
            )

        conn.commit()

        return jsonify({
            "success": True,
            "redirect": "https://skillchain-frontend-omega.vercel.app//Login/index.html",
            "squad": squad_data
        })

    except Exception as e:
        conn.rollback()
        print(f"Register error: {e}")
        return jsonify({"success": False,
                        "errors": {"general": "Server error. Please try again."}}), 500
    finally:
        cur.close()
        conn.close()


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html")

    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()

    if not email:
        return jsonify({"success": False, "message": "Email is required."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM users WHERE email = %s", (email,))
    user = cur.fetchone()

    if not user:
        cur.close()
        conn.close()
        return jsonify({"success": True,
                        "message": "If that email is registered, a code has been sent."})

    token = str(random.randint(100000, 999999))
    expiry = datetime.now() + timedelta(minutes=10)

    try:
        cur.execute(
            "INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES (%s, %s, %s)",
            (user["id"], token, expiry)
        )
        conn.commit()
        print(f"\n{'='*40}")
        print(f"PASSWORD RESET TOKEN for {email}: {token}")
        print(f"Expires: {expiry}")
        print(f"{'='*40}\n")
        return jsonify({"success": True, "message": "Reset code generated! Check your email."})
    except Exception as e:
        print(f"Forgot password error: {e}")
        return jsonify({"success": False, "message": "Server error."}), 500
    finally:
        cur.close()
        conn.close()


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    token = request.args.get("token") or (request.get_json(silent=True) or {}).get("token")

    if request.method == "GET":
        return render_template("reset_password.html", token=token)

    data = request.get_json(silent=True) or {}
    token = data.get("token", "")
    password = data.get("password", "")

    if not token or not password or len(password) < 6:
        return jsonify({"success": False, "message": "Invalid request."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM password_reset_tokens WHERE token = %s AND used = 0", (token,)
    )
    record = cur.fetchone()

    if not record or datetime.now() > record["expires_at"]:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Token expired or already used."}), 400

    try:
        pw_hash = generate_password_hash(password)
        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                    (pw_hash, record["user_id"]))
        cur.execute("UPDATE password_reset_tokens SET used = 1 WHERE id = %s", (record["id"],))
        conn.commit()
        return jsonify({"success": True, "message": "Password updated!",
                        "redirect": "https://skillchain-frontend-omega.vercel.app//Login/index.html"})
    except Exception as e:
        print(f"Reset password error: {e}")
        return jsonify({"success": False, "message": "Server error."}), 500
    finally:
        cur.close()
        conn.close()


@auth_bp.route("/reset-password-final", methods=["POST"])
def reset_password_final():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    token = data.get("token", "")
    password = data.get("password", "")

    if not all([email, token, password]) or len(password) < 6:
        return jsonify({"success": False, "message": "Invalid request."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT prt.* FROM password_reset_tokens prt
           JOIN users u ON u.id = prt.user_id
           WHERE u.email = %s AND prt.token = %s AND prt.used = 0""",
        (email, token)
    )
    record = cur.fetchone()

    if not record or datetime.now() > record["expires_at"]:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Token expired or invalid."}), 400

    try:
        pw_hash = generate_password_hash(password)
        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                    (pw_hash, record["user_id"]))
        cur.execute("UPDATE password_reset_tokens SET used = 1 WHERE id = %s", (record["id"],))
        conn.commit()
        return jsonify({"success": True, "message": "Password updated!",
                        "redirect": "https://skillchain-frontend-omega.vercel.app//Login/index.html"})
    except Exception as e:
        print(f"Reset error: {e}")
        return jsonify({"success": False, "message": "Server error."}), 500
    finally:
        cur.close()
        conn.close()


@auth_bp.route("/api/me")
def api_me():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT id, name, email, role, trust_score, jobs_completed FROM users WHERE id = %s",
        (session["user_id"],)
    )
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user:
        return jsonify({"error": "User not found"}), 404

    return jsonify(user)


@auth_bp.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    return redirect(
        "https://skillchain-frontend-omega.vercel.app//Worker_dashboard/index.html"
        if session.get("role") == "worker"
        else "https://skillchain-frontend-omega.vercel.app//Client_dashboard/index.html"
    )


@auth_bp.route("/logout-api", methods=["POST"])
def logout_api():
    session.clear()
    return jsonify({"success": True})


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
