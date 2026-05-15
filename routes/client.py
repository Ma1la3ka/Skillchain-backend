"""Client-related routes"""
from flask import Blueprint, request, jsonify
from database_helper import get_db
from utils import squad_create_collection_account
import uuid

client_bp = Blueprint('client', __name__, url_prefix='/api/client')


@client_bp.route("/jobs")
def api_client_jobs():
    """Get all jobs posted by this client"""
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT j.*,
                  w.name AS worker_name, w.trust_score AS worker_trust,
                  j.bargain_price, j.bargain_status, j.bargain_worker_id,
                  j.collection_account_number, j.collection_bank_name,
                  j.escrow_paid, j.escrow_amount_received
           FROM jobs j
           LEFT JOIN users w ON j.worker_id = w.id
           WHERE j.client_id = %s
           ORDER BY j.created_at DESC""",
        (user_id,)
    )
    jobs = cur.fetchall()
    cur.close()
    conn.close()

    for job in jobs:
        job["amount"] = float(job["amount"] or 0)
        job["distance_meters"] = float(job["distance_meters"]) if job["distance_meters"] else None
        job["worker_trust"] = float(job["worker_trust"]) if job["worker_trust"] else None
        job["created_at"] = str(job["created_at"])
        job["verified_at"] = str(job["verified_at"]) if job["verified_at"] else None
        job["paid_at"] = str(job["paid_at"]) if job["paid_at"] else None
        job["bargain_price"] = float(job["bargain_price"]) if job["bargain_price"] else None
        job["escrow_paid"] = bool(job["escrow_paid"])
    return jsonify({"jobs": jobs})


@client_bp.route("/post-job", methods=["POST"])
def api_post_job():
    """Client posts a new job"""
    user_id = request.form.get("user_id", "").strip()
    role = request.form.get("role", "").strip()

    if not user_id or role != "client":
        return jsonify({"error": "Unauthorized"}), 401

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    site_address = request.form.get("site_address", "").strip()
    trade = request.form.get("trade", "").strip() or None

    errors = {}
    if not title:
        errors["title"] = "Job title is required."
    if not site_address:
        errors["address"] = "Site address is required."

    amount = site_lat = site_lng = None
    try:
        amount = float(request.form.get("amount", ""))
        site_lat = float(request.form.get("site_lat", ""))
        site_lng = float(request.form.get("site_lng", ""))
        if amount < 100:
            errors["amount"] = "Amount must be at least ₦100."
    except (ValueError, TypeError):
        errors["amount"] = "Enter a valid amount."

    if errors:
        return jsonify({"success": False, "errors": errors}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT name FROM users WHERE id = %s", (user_id,))
        client_row = cur.fetchone()
        client_name = client_row["name"] if client_row else "Client"

        escrow_ref = f"escrow_{uuid.uuid4().hex[:12]}"
        cur.execute(
            """INSERT INTO jobs
               (client_id, title, description, site_address,
                site_lat, site_lng, amount, trade, escrow_reference)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (user_id, title, description, site_address,
             site_lat, site_lng, amount, trade, escrow_ref)
        )
        job_id = cur.lastrowid
        conn.commit()

        cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
        client_row2 = cur.fetchone()
        client_email = client_row2["email"] if client_row2 else "client@skillchain.com"
        squad = squad_create_collection_account(job_id, amount, client_email)

        if squad.get("account_number"):
            cur.execute(
                """UPDATE jobs SET
                   collection_account_number = %s,
                   collection_bank_name      = %s,
                   collection_bank_code      = %s,
                   escrow_reference          = %s
                   WHERE id = %s""",
                (squad["account_number"], squad["bank_name"],
                 squad["bank_code"], squad.get("reference", escrow_ref),
                 job_id)
            )
            conn.commit()

        return jsonify({
            "success": True,
            "job_id": job_id,
            "payment": {
                "account_number": squad.get("account_number", ""),
                "bank_name": squad.get("bank_name", ""),
                "amount": amount,
                "instructions": f"Transfer exactly ₦{amount:,.0f} to this account to fund escrow"
            }
        })

    except Exception as e:
        conn.rollback()
        print(f"[post-job error] {e}")
        return jsonify({"success": False, "errors": {"general": str(e)}}), 500
    finally:
        cur.close()
        conn.close()


@client_bp.route("/delete-job", methods=["DELETE"])
def api_delete_job():
    """Client deletes an open job"""
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    user_id = data.get("user_id")

    if not job_id or not user_id:
        return jsonify({"success": False, "message": "Missing data."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    try:
        cur.execute(
            "SELECT id, status FROM jobs WHERE id = %s AND client_id = %s",
            (job_id, user_id)
        )
        job = cur.fetchone()

        if not job:
            return jsonify({"success": False, "message": "Job not found."}), 404

        if job["status"] not in ("open",):
            return jsonify({
                "success": False,
                "message": "You can only delete jobs that haven't been accepted yet."
            }), 400

        cur.execute("DELETE FROM job_applications WHERE job_id = %s", (job_id,))
        cur.execute("DELETE FROM jobs WHERE id = %s AND client_id = %s", (job_id, user_id))
        conn.commit()
        return jsonify({"success": True})

    except Exception as e:
        conn.rollback()
        print(f"Delete job error: {e}")
        return jsonify({"success": False, "message": "Server error."}), 500
    finally:
        cur.close()
        conn.close()


@client_bp.route("/job-applicants")
def api_job_applicants():
    """Get list of applicants for client's jobs"""
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT ja.job_id, ja.worker_id, ja.status AS app_status,
                  ja.created_at,
                  j.title, j.amount, j.status AS job_status,
                  w.name AS worker_name, w.trade AS worker_trade,
                  w.trust_score AS worker_trust,
                  w.jobs_completed AS worker_jobs
           FROM job_applications ja
           JOIN jobs  j ON j.id  = ja.job_id
           JOIN users w ON w.id  = ja.worker_id
           WHERE j.client_id = %s AND ja.status = 'pending'
           ORDER BY ja.created_at DESC""",
        (user_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    for r in rows:
        r["worker_trust"] = float(r["worker_trust"] or 0)
        r["amount"] = float(r["amount"] or 0)
        r["created_at"] = str(r["created_at"])

    return jsonify({"applicants": rows})


@client_bp.route("/review-worker", methods=["POST"])
def api_client_review_worker():
    """Client approves or declines a worker application"""
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    user_id = str(data.get("user_id", "")).strip()
    worker_id = str(data.get("worker_id", "")).strip()
    action = data.get("action")

    if not all([job_id, user_id, worker_id, action]) or action not in ("assign", "decline"):
        return jsonify({"success": False, "message": "Missing fields."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT * FROM jobs WHERE id = %s AND client_id = %s",
            (job_id, user_id)
        )
        job = cur.fetchone()
        if not job:
            return jsonify({"success": False, "message": "Job not found."}), 404

        if action == "assign":
            cur.execute(
                """UPDATE jobs SET status = 'assigned', worker_id = %s
                   WHERE id = %s""",
                (worker_id, job_id)
            )
            cur.execute(
                """UPDATE job_applications SET status = 'assigned'
                   WHERE job_id = %s AND worker_id = %s""",
                (job_id, worker_id)
            )
            cur.execute(
                """UPDATE job_applications SET status = 'declined'
                   WHERE job_id = %s AND worker_id != %s""",
                (job_id, worker_id)
            )
            conn.commit()
            return jsonify({"success": True, "action": "assign",
                            "message": "Worker assigned!"})
        else:
            cur.execute(
                """UPDATE job_applications SET status = 'declined'
                   WHERE job_id = %s AND worker_id = %s""",
                (job_id, worker_id)
            )
            conn.commit()
            return jsonify({"success": True, "action": "decline",
                            "message": "Worker declined."})

    except Exception as e:
        conn.rollback()
        print(f"[review-worker error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@client_bp.route("/pay-escrow", methods=["POST"])
def api_pay_escrow():
    """Client marks escrow as paid"""
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    user_id = str(data.get("user_id", "")).strip()

    if not job_id or not user_id:
        return jsonify({"success": False, "message": "Missing fields."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id, status, client_id, amount FROM jobs WHERE id = %s AND client_id = %s",
            (job_id, user_id)
        )
        job = cur.fetchone()
        if not job:
            return jsonify({"success": False, "message": "Job not found."}), 404
        if job["status"] not in ("open", "assigned"):
            return jsonify({"success": False, "message": "Job cannot be funded at this stage."}), 400

        cur.execute(
            "UPDATE jobs SET escrow_paid = 1, escrow_paid_at = NOW() WHERE id = %s",
            (job_id,)
        )
        conn.commit()
        return jsonify({"success": True, "message": "Escrow funded. Worker can now begin."})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@client_bp.route("/bargains")
def api_client_bargains():
    """Get all pending bargains for client's jobs"""
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT b.*, j.title AS job_title, j.amount AS original_amount,
                  w.name AS worker_name, w.trust_score AS worker_trust,
                  w.jobs_completed AS worker_jobs
           FROM bargains b
           JOIN jobs j ON j.id = b.job_id
           JOIN users w ON w.id = b.worker_id
           WHERE j.client_id = %s AND b.status = 'pending'
           ORDER BY b.created_at DESC""",
        (user_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    for r in rows:
        r["proposed_price"] = float(r["proposed_price"])
        r["original_amount"] = float(r["original_amount"])
        r["worker_trust"] = float(r["worker_trust"] or 0)
        r["created_at"] = str(r["created_at"])
    return jsonify({"bargains": rows})


@client_bp.route("/respond-bargain", methods=["POST"])
def api_respond_bargain():
    """Client accepts or rejects a bargain"""
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    user_id = str(data.get("user_id", "")).strip()
    action = data.get("action")

    if not all([job_id, user_id, action]) or action not in ("accept", "reject"):
        return jsonify({"success": False, "message": "job_id, user_id and action required."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT j.*, u.name AS client_name
               FROM jobs j
               JOIN users u ON u.id = j.client_id
               WHERE j.id = %s AND j.client_id = %s AND j.bargain_status = 'pending'""",
            (job_id, user_id)
        )
        job = cur.fetchone()

        if not job:
            return jsonify({"success": False, "message": "No pending bargain found for this job."}), 404

        if job["status"] not in ("open",):
            return jsonify({"success": False, "message": "Job is no longer open."}), 400

        if action == "reject":
            cur.execute(
                """UPDATE jobs SET
                   bargain_status = 'rejected',
                   bargain_price  = NULL,
                   bargain_worker_id = NULL
                   WHERE id = %s""",
                (job_id,)
            )
            cur.execute(
                "UPDATE bargains SET status = 'rejected' WHERE job_id = %s AND status = 'pending'",
                (job_id,)
            )
            conn.commit()
            return jsonify({"success": True, "action": "reject"})

        # ACCEPT BARGAIN
        agreed_amount = float(job["bargain_price"])
        agreed_worker_id = job["bargain_worker_id"]

        cur.execute(
            """UPDATE jobs SET
               amount            = %s,
               worker_id         = %s,
               status            = 'assigned',
               bargain_status    = 'accepted'
               WHERE id = %s""",
            (agreed_amount, agreed_worker_id, job_id)
        )

        cur.execute(
            "UPDATE bargains SET status = 'rejected' WHERE job_id = %s AND status = 'pending'",
            (job_id,)
        )
        cur.execute(
            """UPDATE bargains SET status = 'accepted'
               WHERE job_id = %s AND worker_id = %s""",
            (job_id, agreed_worker_id)
        )

        conn.commit()

        cur.execute("SELECT email FROM users WHERE id = %s", (job['client_id'],))
        cl = cur.fetchone()
        client_email = cl["email"] if cl else "client@skillchain.com"
        squad = squad_create_collection_account(job_id, agreed_amount, client_email)

        if squad.get("account_number"):
            cur.execute(
                """UPDATE jobs SET
                   collection_account_number = %s,
                   collection_bank_name      = %s,
                   collection_bank_code      = %s,
                   escrow_reference          = %s
                   WHERE id = %s""",
                (squad["account_number"], squad["bank_name"],
                 squad.get("bank_code", ""), squad.get("reference", ""),
                 job_id)
            )
            conn.commit()

        return jsonify({
            "success": True,
            "action": "accept",
            "payment": {
                "account_number": squad.get("account_number", ""),
                "bank_name": squad.get("bank_name", ""),
                "amount": agreed_amount,
            }
        })

    except Exception as e:
        conn.rollback()
        print(f"[respond-bargain error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@client_bp.route("/rate-worker", methods=["POST"])
def api_rate_worker():
    """Client rates a worker after job completion"""
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    user_id = str(data.get("user_id", "")).strip()
    rating = data.get("rating")
    comment = data.get("comment", "").strip()

    if not all([job_id, user_id, rating]):
        return jsonify({"success": False, "message": "job_id, user_id and rating required."}), 400

    try:
        rating = int(rating)
        if not (1 <= rating <= 5):
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Rating must be 1–5."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT id, status, distance_meters, worker_id, client_rating
               FROM jobs WHERE id = %s AND client_id = %s""",
            (job_id, user_id)
        )
        job = cur.fetchone()
        if not job:
            return jsonify({"success": False, "message": "Job not found."}), 404

        if job["status"] not in ("verified", "paid"):
            return jsonify({"success": False,
                            "message": "You can only rate after the job is verified."}), 400

        if job["distance_meters"] is None or float(job["distance_meters"]) > 100:
            return jsonify({
                "success": False,
                "can_rate": False,
                "message": "Worker was not within the job site GPS boundary — rating is disabled."
            }), 403

        if job["client_rating"] is not None:
            return jsonify({"success": False, "message": "You have already rated this job."}), 400

        cur.execute(
            """UPDATE jobs SET client_rating = %s, client_rating_comment = %s,
                               client_rated_at = NOW()
               WHERE id = %s""",
            (rating, comment, job_id)
        )

        cur.execute(
            """UPDATE users SET
               trust_score = (
                 SELECT ROUND(AVG(client_rating), 2)
                 FROM jobs
                 WHERE worker_id = %s AND client_rating IS NOT NULL
               )
               WHERE id = %s""",
            (job["worker_id"], job["worker_id"])
        )

        conn.commit()
        return jsonify({"success": True, "message": "Rating saved."})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@client_bp.route("/pending-workers")
def api_client_pending_workers():
    """Get assigned workers waiting for escrow funding"""
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT j.id, j.title, j.amount, j.status,
                  w.id AS worker_id, w.name AS worker_name,
                  w.trade AS worker_trade, w.trust_score AS worker_trust,
                  w.jobs_completed AS worker_jobs, w.phone AS worker_phone
           FROM jobs j
           JOIN users w ON w.id = j.worker_id
           WHERE j.client_id = %s
             AND j.status = 'assigned'
             AND j.escrow_paid = 0
           ORDER BY j.created_at DESC""",
        (user_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    for r in rows:
        r["amount"] = float(r["amount"] or 0)
        r["worker_trust"] = float(r["worker_trust"] or 0)
    return jsonify({"pending": rows})


@client_bp.route("/jobs-social")
def api_client_jobs_social():
    """Get client's jobs with social data (likes, comments, bargains)"""
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT j.*,
                  w.name AS worker_name, w.trust_score AS worker_trust,
                  (SELECT COUNT(*) FROM job_likes    WHERE job_id = j.id) AS likes,
                  (SELECT COUNT(*) FROM job_comments WHERE job_id = j.id) AS comment_count,
                  (SELECT COUNT(*) FROM bargains WHERE job_id = j.id AND status = 'pending') AS bargain_count,
                  j.escrow_paid,
                  j.client_rating
           FROM jobs j
           LEFT JOIN users w ON w.id = j.worker_id
           WHERE j.client_id = %s
           ORDER BY j.created_at DESC""",
        (user_id,)
    )
    jobs = cur.fetchall()
    cur.close()
    conn.close()

    for job in jobs:
        job["amount"] = float(job["amount"] or 0)
        job["distance_meters"] = float(job["distance_meters"]) if job["distance_meters"] else None
        job["worker_trust"] = float(job["worker_trust"]) if job["worker_trust"] else None
        job["created_at"] = str(job["created_at"])
        job["verified_at"] = str(job["verified_at"]) if job["verified_at"] else None
        job["paid_at"] = str(job["paid_at"]) if job["paid_at"] else None
        job["escrow_paid"] = bool(job["escrow_paid"])

    return jsonify({"jobs": jobs})


@client_bp.route("/retry-payment/<int:job_id>", methods=['POST'])
def retry_payment(job_id):
    """Retry Squad payment link generation"""
    try:
        conn = get_db()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT j.amount, u.email
               FROM jobs j JOIN users u ON u.id = j.client_id
               WHERE j.id = %s""",
            (job_id,)
        )
        job = cur.fetchone()
        if not job:
            return jsonify({"success": False, "message": "Job not found"}), 404

        squad_data = squad_create_collection_account(job_id, job['amount'], job['email'])

        if squad_data and squad_data.get("account_number"):
            cur.execute(
                """UPDATE jobs SET
                   collection_account_number = %s,
                   collection_bank_name      = %s,
                   collection_bank_code      = %s,
                   escrow_reference          = %s
                   WHERE id = %s""",
                (squad_data["account_number"], squad_data["bank_name"],
                 squad_data["bank_code"], squad_data.get("reference"), job_id)
            )
            conn.commit()
            return jsonify({"success": True, "message": "Payment link generated!"})
        else:
            return jsonify({"success": False, "message": "Squad API failed. Check KYC settings."})
    except Exception as e:
        print(f"Error in retry-payment: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()
