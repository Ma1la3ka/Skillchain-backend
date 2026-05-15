"""Job-related routes"""
from flask import Blueprint, request, jsonify
from database_helper import get_db

jobs_bp = Blueprint('jobs', __name__, url_prefix='/api/job')


@jobs_bp.route("/payment-details")
def api_job_payment_details():
    """Get payment details for a job"""
    job_id = request.args.get("job_id")
    user_id = request.args.get("user_id")
    if not job_id:
        return jsonify({"error": "job_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT id, title, amount,
                  collection_account_number, collection_bank_name, collection_bank_code,
                  escrow_paid, escrow_paid_at, escrow_amount_received,
                  status, worker_id
           FROM jobs WHERE id = %s AND client_id = %s""",
        (job_id, user_id)
    )
    job = cur.fetchone()
    cur.close()
    conn.close()

    if not job:
        return jsonify({"error": "Job not found"}), 404

    job["amount"] = float(job["amount"] or 0)
    job["escrow_amount_received"] = float(job["escrow_amount_received"] or 0) if job["escrow_amount_received"] else None
    job["escrow_paid"] = bool(job["escrow_paid"])
    job["escrow_paid_at"] = str(job["escrow_paid_at"]) if job["escrow_paid_at"] else None

    return jsonify(job)


@jobs_bp.route("/escrow-status")
def api_escrow_status():
    """Check escrow payment status"""
    job_id = request.args.get("job_id")
    user_id = request.args.get("user_id")
    if not job_id:
        return jsonify({"error": "job_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT id, amount, escrow_paid, escrow_paid_at, escrow_amount_received,
                  collection_account_number, collection_bank_name, status
           FROM jobs WHERE id = %s""",
        (job_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({"error": "Not found"}), 404

    row["amount"] = float(row["amount"] or 0)
    row["escrow_paid"] = bool(row["escrow_paid"])
    row["escrow_amount_received"] = float(row["escrow_amount_received"] or 0) if row["escrow_amount_received"] else None
    row["escrow_paid_at"] = str(row["escrow_paid_at"]) if row["escrow_paid_at"] else None

    return jsonify(row)
