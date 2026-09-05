"""Job-related routes"""
from flask import Blueprint, request, jsonify
from database_helper import get_db

jobs_bp = Blueprint('jobs', __name__, url_prefix='/api/job')

@jobs_bp.route("/payment-details")
def api_job_payment_details():
    """Get payment details for a job (or a specific worker's slot on a multi-slot gig)"""
    job_id        = request.args.get("job_id")
    job_worker_id = request.args.get("job_worker_id")
    user_id       = request.args.get("user_id")

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    if job_worker_id:
        cur.execute(
            """SELECT jw.id, jw.amount, jw.client_pays, jw.artisan_gets, jw.platform_fee,
                      jw.collection_account_number, jw.collection_bank_name, jw.collection_bank_code,
                      jw.escrow_paid, jw.escrow_paid_at, jw.escrow_amount_received,
                      jw.status, jw.worker_id, j.title, j.client_id
               FROM job_workers jw JOIN jobs j ON j.id = jw.job_id
               WHERE jw.id = %s AND j.client_id = %s""",
            (job_worker_id, user_id)
        )
        job = cur.fetchone()
    else:
        if not job_id:
            return jsonify({"error": "job_id or job_worker_id required"}), 400
        cur.execute(
            """SELECT id, title, amount, client_pays, artisan_gets, platform_fee,
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

    job["amount"]       = float(job["amount"] or 0)
    job["client_pays"]  = float(job["client_pays"] or job["amount"])
    job["artisan_gets"] = float(job["artisan_gets"] or job["amount"])
    job["escrow_amount_received"] = float(job["escrow_amount_received"] or 0) if job["escrow_amount_received"] else None
    job["escrow_paid"] = bool(job["escrow_paid"])
    job["escrow_paid_at"] = str(job["escrow_paid_at"]) if job["escrow_paid_at"] else None

    return jsonify(job)

@jobs_bp.route("/escrow-status")
def api_escrow_status():
    """Check escrow payment status for a job (or a specific worker's slot)"""
    job_id        = request.args.get("job_id")
    job_worker_id = request.args.get("job_worker_id")
    user_id       = request.args.get("user_id")

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    if job_worker_id:
        cur.execute(
            """SELECT id, amount, escrow_paid, escrow_paid_at, escrow_amount_received,
                      collection_account_number, collection_bank_name, status
               FROM job_workers WHERE id = %s AND worker_id = %s""",
            (job_worker_id, user_id)
        )
        row = cur.fetchone()
    else:
        if not job_id:
            return jsonify({"error": "job_id or job_worker_id required"}), 400
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