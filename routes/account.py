"""Account deletion (required by Google Play and the App Store).
Personal data is erased/anonymised; money records (jobs, ledger, payouts) are kept for accounting."""
import secrets
from flask import Blueprint, request, jsonify, g
from werkzeug.security import check_password_hash, generate_password_hash
from database_helper import get_db
from utils import money

account_bp = Blueprint('account', __name__, url_prefix='/api/account')
ACTIVE = ("assigned", "pending_verification", "verified", "disputed")


@account_bp.route("/delete", methods=["POST"])
def api_delete_account():
    password = str((request.get_json(silent=True) or {}).get("password", ""))
    if not password:
        return jsonify({"success": False, "message": "Enter your password to confirm."}), 400
    uid = g.uid
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, password_hash, escrow_balance FROM users WHERE id=%s FOR UPDATE", (uid,))
        u = cur.fetchone()
        if not u or not check_password_hash(u["password_hash"], password):
            conn.rollback()
            return jsonify({"success": False, "message": "Incorrect password."}), 403
        if money(u["escrow_balance"]) > 0:
            conn.rollback()
            return jsonify({"success": False, "message": "Withdraw your wallet balance first, then delete your account."}), 409

        marks = ",".join(["%s"] * len(ACTIVE))
        cur.execute(f"""SELECT COUNT(*) AS c FROM jobs
                        WHERE (client_id=%s OR worker_id=%s)
                          AND (status IN ({marks}) OR (status='open' AND client_id=%s AND COALESCE(escrow_paid,0)=1))""",
                    (uid, uid, *ACTIVE, uid))
        if cur.fetchone()["c"]:
            conn.rollback()
            return jsonify({"success": False, "message": "You still have a job in progress or waiting for payment. Finish or resolve it first."}), 409
        cur.execute("SELECT COUNT(*) AS c FROM payouts WHERE user_id=%s AND status IN ('processing','needs_review')", (uid,))
        if cur.fetchone()["c"]:
            conn.rollback()
            return jsonify({"success": False, "message": "A withdrawal is still processing. Try again once it lands."}), 409

        try:   # unfunded open posts are removed; if linked records block it, they simply stay anonymous
            cur.execute("DELETE FROM jobs WHERE client_id=%s AND status='open' AND COALESCE(escrow_paid,0)=0", (uid,))
        except Exception as e:
            print(f"[delete-account] open jobs kept: {e}")

        cur.execute("""UPDATE users SET name='Deleted user', email=%s, phone=NULL, password_hash=%s,
                       profile_photo_path=NULL, bank_account_no=NULL, bank_code=NULL, bank_name=NULL,
                       bank_account_name=NULL, paystack_recipient_code=NULL, withdrawal_pin_hash=NULL,
                       shop_lat=NULL, shop_lng=NULL, escrow_balance=0
                       WHERE id=%s""",
                    (f"deleted_{uid}_{secrets.token_hex(4)}@deleted.invalid",
                     generate_password_hash(secrets.token_urlsafe(32)), uid))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        print(f"[delete-account error] {e}")
        return jsonify({"success": False, "message": "Could not delete the account. Please try again."}), 500
    finally:
        cur.close()
        conn.close()


@account_bp.route("/device", methods=["POST"])
def api_register_device():
    """Called by the app after login so this phone can receive push notifications."""
    token = str((request.get_json(silent=True) or {}).get("token", "")).strip()
    if not token or len(token) > 250:
        return jsonify({"success": False}), 400
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""INSERT INTO device_tokens (user_id, token, platform) VALUES (%s, %s, 'android')
                       ON DUPLICATE KEY UPDATE user_id = VALUES(user_id), updated_at = NOW()""", (g.uid, token))
        conn.commit()
        return jsonify({"success": True})
    finally:
        cur.close()
        conn.close()


@account_bp.route("/device/remove", methods=["POST"])
def api_remove_device():
    """Called on logout so the next person on this phone does not get your alerts."""
    token = str((request.get_json(silent=True) or {}).get("token", "")).strip()
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM device_tokens WHERE token = %s AND user_id = %s", (token, g.uid))
        conn.commit()
        return jsonify({"success": True})
    finally:
        cur.close()
        conn.close()
