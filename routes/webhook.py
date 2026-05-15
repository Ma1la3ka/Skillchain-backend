"""Squad webhook and payment routes"""
import json
import hmac
import hashlib
import os
from flask import Blueprint, request, jsonify
from database_helper import get_db
from config import SQUAD_KEY

webhook_bp = Blueprint('webhook', __name__, url_prefix='/api')


@webhook_bp.route("/squad/webhook", methods=["POST"])
def api_squad_webhook():
    """Handle Squad webhook for payment confirmation"""
    raw_body = request.get_data()
    squad_sig = request.headers.get("x-squad-encrypted-body", "")
    computed_sig = hmac.new(
        SQUAD_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha512
    ).hexdigest().upper()

    if squad_sig.upper() != computed_sig:
        print(f"[webhook] Signature mismatch. Got: {squad_sig[:20]}…")
        return jsonify({"status": "signature_invalid"}), 200

    try:
        payload = json.loads(raw_body)
    except Exception:
        return jsonify({"status": "bad_json"}), 200

    print(f"[webhook] Received: {json.dumps(payload, indent=2)}")

    event = payload.get("Event", payload.get("event", ""))

    if event not in ("virtual_account_created_funded", "charge.success", "transfer.success"):
        return jsonify({"status": "ignored", "event": event}), 200

    data = payload.get("Body", payload.get("data", {}))
    reference = data.get("customer_identifier", data.get("reference", ""))
    amount_kobo = data.get("amount", data.get("transaction_amount", 0))
    amount_naira = float(amount_kobo) / 100 if amount_kobo else 0.0
    transaction_ref = data.get("transaction_reference", data.get("Reference", ""))

    print(f"[webhook] reference={reference} amount=₦{amount_naira:.2f}")

    if not reference:
        return jsonify({"status": "no_reference"}), 200

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT id, amount, status, escrow_paid, worker_id
               FROM jobs WHERE escrow_reference = %s""",
            (reference,)
        )
        job = cur.fetchone()

        if not job:
            print(f"[webhook] No job found for reference={reference}")
            return jsonify({"status": "job_not_found"}), 200

        if job["escrow_paid"]:
            print(f"[webhook] Job {job['id']} already marked escrow_paid")
            return jsonify({"status": "already_paid"}), 200

        expected_naira = float(job["amount"])

        if abs(amount_naira - expected_naira) > 1.0:
            print(f"[webhook] Amount mismatch. Expected ₦{expected_naira} got ₦{amount_naira}")
            cur.execute(
                """UPDATE jobs SET escrow_amount_received = %s
                   WHERE id = %s""",
                (amount_naira, job["id"])
            )
            conn.commit()
            return jsonify({
                "status": "amount_mismatch",
                "expected": expected_naira,
                "received": amount_naira
            }), 200

        cur.execute(
            """UPDATE jobs SET
               escrow_paid             = 1,
               escrow_paid_at          = NOW(),
               escrow_amount_received  = %s
               WHERE id = %s""",
            (amount_naira, job["id"])
        )
        conn.commit()
        print(f"[webhook] ✅ Job {job['id']} escrow marked paid ₦{amount_naira}")

        return jsonify({"status": "ok", "job_id": job["id"]}), 200

    except Exception as e:
        conn.rollback()
        print(f"[webhook] DB error: {e}")
        return jsonify({"status": "db_error"}), 200
    finally:
        cur.close()
        conn.close()


@webhook_bp.route("/dev/simulate-payment", methods=["POST"])
def api_simulate_payment():
    """DEV ONLY: Simulate Squad webhook"""
    if os.environ.get("FLASK_ENV") == "production":
        return jsonify({"error": "Not available in production"}), 403

    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "job_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, amount, escrow_reference FROM jobs WHERE id = %s", (job_id,))
        job = cur.fetchone()
        if not job:
            return jsonify({"error": "Job not found"}), 404

        cur.execute(
            """UPDATE jobs SET
               escrow_paid            = 1,
               escrow_paid_at         = NOW(),
               escrow_amount_received = %s
               WHERE id = %s""",
            (float(job["amount"]), job_id)
        )
        conn.commit()
        return jsonify({"success": True, "message": f"Job {job_id} escrow simulated as paid ₦{job['amount']}"})
    finally:
        cur.close()
        conn.close()
