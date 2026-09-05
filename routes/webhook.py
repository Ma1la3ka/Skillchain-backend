"""Paystack webhook and payment routes"""
import os
from flask import Blueprint, request, jsonify
from database_helper import get_db
from paystack_service import verify_webhook_signature
from utils import release_job_payment

webhook_bp = Blueprint('webhook', __name__, url_prefix='/api')


@webhook_bp.route("/paystack/webhook", methods=["POST"])
def api_paystack_webhook():
    """Handle Paystack webhook for payment confirmation"""
    raw_body = request.get_data()
    signature = request.headers.get("x-paystack-signature", "")

    if not verify_webhook_signature(raw_body, signature):
        print("[webhook] Signature mismatch — rejecting.")
        return jsonify({"status": "signature_invalid"}), 200

    try:
        payload = request.get_json(force=True)
    except Exception:
        return jsonify({"status": "bad_json"}), 200

    event = payload.get("event", "")
    print(f"[webhook] Received event: {event}")

    if event != "charge.success":
        return jsonify({"status": "ignored", "event": event}), 200

    data = payload.get("data", {})
    reference = data.get("reference", "")
    amount_kobo = data.get("amount", 0)
    amount_naira = float(amount_kobo) / 100 if amount_kobo else 0.0

    print(f"[webhook] reference={reference} amount=₦{amount_naira:.2f}")

    if not reference:
        return jsonify({"status": "no_reference"}), 200

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        # ── Check jobs table first ──
        cur.execute(
            """SELECT id, amount, status, escrow_paid, worker_id
               FROM jobs WHERE escrow_reference = %s""",
            (reference,)
        )
        job = cur.fetchone()
        source = "jobs" if job else None

        # ── Fall back to job_workers (multi-slot quick gigs) ──
        if not job:
            cur.execute(
                """SELECT id, amount, status, escrow_paid, worker_id
                   FROM job_workers WHERE escrow_reference = %s""",
                (reference,)
            )
            job = cur.fetchone()
            source = "job_workers" if job else None

        if not job:
            print(f"[webhook] No job or job_worker found for reference={reference}")
            return jsonify({"status": "job_not_found"}), 200

        if job["escrow_paid"]:
            print(f"[webhook] {source} {job['id']} already marked escrow_paid")
            return jsonify({"status": "already_paid"}), 200

        expected_naira = float(job["amount"])

        if abs(amount_naira - expected_naira) > 1.0:
            print(f"[webhook] Amount mismatch. Expected ₦{expected_naira} got ₦{amount_naira}")
            cur.execute(
                f"UPDATE {source} SET escrow_amount_received = %s WHERE id = %s",
                (amount_naira, job["id"])
            )
            conn.commit()
            return jsonify({
                "status": "amount_mismatch",
                "expected": expected_naira,
                "received": amount_naira
            }), 200

        cur.execute(
            f"""UPDATE {source} SET
               escrow_paid            = 1,
               escrow_paid_at         = NOW(),
               escrow_amount_received = %s
               WHERE id = %s""",
            (amount_naira, job["id"])
        )
        conn.commit()
        print(f"[webhook] ✅ {source} {job['id']} escrow marked paid ₦{amount_naira}")

        return jsonify({"status": "ok", "id": job["id"], "source": source}), 200

    except Exception as e:
        conn.rollback()
        print(f"[webhook] DB error: {e}")
        return jsonify({"status": "db_error"}), 200
    finally:
        cur.close()
        conn.close()


@webhook_bp.route("/dev/simulate-payment", methods=["POST"])
def api_simulate_payment():
    """DEV ONLY: Simulate a successful escrow payment without calling Paystack.
    Accepts either job_id (regular job / single-slot gig) or job_worker_id
    (multi-slot gig slot)."""
    if os.environ.get("FLASK_ENV") == "production":
        return jsonify({"error": "Not available in production"}), 403

    data          = request.get_json(silent=True) or {}
    job_id        = data.get("job_id")
    job_worker_id = data.get("job_worker_id")

    if not job_id and not job_worker_id:
        return jsonify({"error": "job_id or job_worker_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        if job_worker_id:
            table, id_val = "job_workers", job_worker_id
        else:
            table, id_val = "jobs", job_id

        cur.execute(
            f"SELECT id, amount, client_pays, escrow_reference FROM {table} WHERE id = %s",
            (id_val,)
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404

        pay_amount = float(row["client_pays"] or row["amount"])

        cur.execute(
            f"""UPDATE {table} SET
               escrow_paid            = 1,
               escrow_paid_at         = NOW(),
               escrow_amount_received = %s
               WHERE id = %s""",
            (pay_amount, id_val)
        )
        conn.commit()
        return jsonify({
            "success": True,
            "message": f"{table} {id_val} escrow simulated as paid ₦{pay_amount:,.2f}"
        })
    finally:
        cur.close()
        conn.close()


@webhook_bp.route("/dev/force-release/<int:job_id>", methods=["POST"])
def api_dev_force_release(job_id):
    """DEV ONLY: Force the 24h auto-release logic to run for one job right now."""
    if os.environ.get("FLASK_ENV") == "production":
        return jsonify({"error": "Not available in production"}), 403

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        job = cur.fetchone()
        if not job:
            return jsonify({"error": "Job not found"}), 404
        if job["status"] != "verified":
            return jsonify({"error": f"Job status is '{job['status']}', not 'verified'."}), 400

        ref = release_job_payment(job, cur)
        conn.commit()
        if ref is None:
            return jsonify({"success": False, "message": "Job was already resolved by another process."}), 409
        return jsonify({"success": True, "message": f"Job {job_id} force-released.", "transfer_reference": ref})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()