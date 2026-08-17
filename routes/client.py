"""Client-side routes for SkillChain"""
from flask import Blueprint, request, jsonify
from database_helper import get_db
from datetime import datetime, timedelta

client_bp = Blueprint('client', __name__, url_prefix='/api/client')

# ── Fee constants ──────────────────────────────────────────────────────────────
PLATFORM_FEE_RATE  = 0.05    # 5% total
CLIENT_FEE_RATE    = 0.025   # 2.5% from client
ARTISAN_FEE_RATE   = 0.025   # 2.5% from artisan
MAX_PLATFORM_FEE   = 2500.00 # ₦2,500 total cap
MAX_CLIENT_FEE     = 1250.00 # ₦1,250 per side cap
MAX_ARTISAN_FEE    = 1250.00 # ₦1,250 per side cap


def calculate_fees(amount):
    """
    Calculate platform fees with cap.
    
    Below ₦50,000: 2.5% each side
    Above ₦50,000: capped at ₦1,250 each side (₦2,500 total)
    
    Returns dict with all fee breakdown values.
    """
    amount = float(amount)

    client_fee   = min(round(amount * CLIENT_FEE_RATE,  2), MAX_CLIENT_FEE)
    artisan_fee  = min(round(amount * ARTISAN_FEE_RATE, 2), MAX_ARTISAN_FEE)
    platform_fee = client_fee + artisan_fee

    client_pays  = round(amount + client_fee,  2)
    artisan_gets = round(amount - artisan_fee, 2)

    return {
        "amount":       amount,
        "client_fee":   client_fee,
        "artisan_fee":  artisan_fee,
        "platform_fee": platform_fee,
        "client_pays":  client_pays,
        "artisan_gets": artisan_gets,
    }

@client_bp.route("/profile")
def api_client_profile():
    """Get client's full profile with job stats"""
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT id, name, email, role, phone, bio,
                      top_skills, profile_photo_path,
                      bank_account_no, bank_code,
                      bank_name, bank_account_name,
                      has_client_profile, active_role
               FROM users WHERE id = %s""",
            (user_id,)
        )
        user = cur.fetchone()
        if not user:
            return jsonify({"error": "User not found"}), 404

        # Convert top_skills comma string → list
        if user.get("top_skills"):
            user["top_skills"] = [
                s.strip() for s in user["top_skills"].split(",") if s.strip()
            ]
        else:
            user["top_skills"] = []

        # Job stats
        cur.execute(
            """SELECT
                 COUNT(*)                                                        AS total_jobs,
                 SUM(CASE WHEN status = 'paid'   THEN 1     ELSE 0   END)       AS completed_jobs,
                 SUM(CASE WHEN status = 'paid'   THEN amount ELSE 0  END)       AS total_spent,
                 SUM(CASE WHEN status IN ('open','assigned','pending_verification')
                          THEN amount ELSE 0 END)                                AS in_escrow
               FROM jobs WHERE client_id = %s""",
            (user_id,)
        )
        stats = cur.fetchone()

        user["total_jobs"]     = int(stats["total_jobs"]     or 0)
        user["completed_jobs"] = int(stats["completed_jobs"] or 0)
        user["total_spent"]    = float(stats["total_spent"]  or 0)
        user["in_escrow"]      = float(stats["in_escrow"]    or 0)

        return jsonify(user)

    except Exception as e:
        print(f"[client-profile error] {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@client_bp.route("/send-offer", methods=["POST"])
def api_client_send_offer():
    """
    Client sends a formal price offer to an artisan through chat.
    Reuses the bargains table — same as worker bargaining but initiated by client.
    """
    data      = request.get_json(silent=True) or {}
    job_id    = data.get("job_id")
    client_id = str(data.get("user_id", "")).strip()
    worker_id = str(data.get("worker_id", "")).strip()
    amount    = data.get("amount")
    message   = data.get("message", "").strip()

    if not all([job_id, client_id, worker_id, amount]):
        return jsonify({"success": False,
                        "message": "job_id, user_id, worker_id and amount required."}), 400

    try:
        amount = float(amount)
        if amount < 100:
            return jsonify({"success": False, "message": "Minimum offer is ₦100"}), 400
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid amount"}), 400

    fees = calculate_fees(amount)

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        # Verify job belongs to this client
        cur.execute(
            "SELECT id, status FROM jobs WHERE id = %s AND client_id = %s",
            (job_id, client_id)
        )
        job = cur.fetchone()
        if not job:
            return jsonify({"success": False,
                            "message": "Job not found or not yours."}), 404

        if job["status"] not in ("open", "pending_review"):
            return jsonify({"success": False,
                            "message": f"Job is already {job['status']} — cannot send offer."}), 400

        # Cancel any previous pending offers on this job between these two parties
        cur.execute(
            """UPDATE bargains SET status = 'cancelled'
               WHERE job_id = %s AND worker_id = %s AND status = 'pending'""",
            (job_id, worker_id)
        )

        # Insert new offer — initiated_by='client' distinguishes from worker bargains
                # Insert or update — same as worker bargain but initiated by client
        # FIND:
        cur.execute(
            """INSERT INTO bargains
            (job_id, worker_id, proposed_price, message, status,
                initiated_by, created_at)
            VALUES (%s, %s, %s, %s, 'pending', 'client', NOW())""",
            (job_id, worker_id, amount, message or f"Price offer: ₦{amount:,.0f}")[:255]  )
        
        bargain_id = cur.lastrowid
        conn.commit()

        return jsonify({
            "success":    True,
            "bargain_id": bargain_id,
            "fee_breakdown": {
                "agreed_amount": amount,
                "client_pays":   fees["client_pays"],
                "artisan_gets":  fees["artisan_gets"],
                "platform_fee":  fees["platform_fee"],
                "cap_applied":   fees["client_fee"] >= MAX_CLIENT_FEE,
            }
        })

    except Exception as e:
        conn.rollback()
        print(f"[send-offer error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()

        
@client_bp.route("/post-job", methods=["POST"])
def api_post_job():
    """Post a new job with fee calculation"""
    user_id      = str(request.form.get("user_id", "")).strip()
    title        = request.form.get("title", "").strip()
    description  = request.form.get("description", "").strip()
    trade        = request.form.get("trade", "").strip()
    site_address = request.form.get("site_address", "").strip()
    lat          = request.form.get("site_lat")
    lng          = request.form.get("site_lng")
    raw_amount   = request.form.get("amount", 0)

    if not all([user_id, title, trade, site_address, raw_amount]):
        return jsonify({"success": False,
                        "message": "user_id, title, trade, site_address and amount are required."}), 400

    try:
        fees = calculate_fees(raw_amount)
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid amount."}), 400

    if fees["amount"] < 500:
        return jsonify({"success": False, "message": "Minimum job amount is ₦500."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """INSERT INTO jobs
               (client_id, title, description, trade,
                site_address, site_lat, site_lng,
                amount, platform_fee, client_pays, artisan_gets,
                status, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open', NOW())""",
            (user_id, title, description, trade,
             site_address, lat, lng,
             fees["amount"], fees["platform_fee"],
             fees["client_pays"], fees["artisan_gets"])
        )
        conn.commit()
        job_id = cur.lastrowid

        # ── Handle uploaded media files ──────────────────────────
        import os
        for key in request.files:
            if key.startswith("media_"):
                file = request.files[key]
                if file and file.filename:
                    ext   = os.path.splitext(file.filename)[1].lower() or ".jpg"
                    fname = f"job_{job_id}_{key}{ext}"
                    path  = os.path.join("static", "job_media", fname)
                    os.makedirs(os.path.join("static", "job_media"), exist_ok=True)
                    file.save(path)
                    media_type = "video" if file.mimetype.startswith("video/") else "image"
                    cur.execute(
                        """INSERT INTO job_media (job_id, uploader_id, file_path, media_type)
                           VALUES (%s, %s, %s, %s)""",
                        (job_id, user_id, path, media_type)
                    )
        conn.commit()

        return jsonify({
            "success":     True,
            "job_id":      job_id,
            "fee_breakdown": {
                "job_amount":   fees["amount"],
                "your_fee":     fees["client_fee"],
                "you_pay":      fees["client_pays"],
                "artisan_gets": fees["artisan_gets"],
                "platform_fee": fees["platform_fee"],
                "cap_applied":  fees["client_fee"] >= MAX_CLIENT_FEE,
            }
        })
    except Exception as e:
        conn.rollback()
        print(f"[post-job error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ── Get Jobs ──────────────────────────────────────────────────────────────────
@client_bp.route("/jobs")
def api_client_jobs():
    """Get all jobs posted by this client"""
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT j.*,
                      j.platform_fee, j.client_pays, j.artisan_gets,
                      u.name       AS worker_name,
                      u.trade      AS worker_trade,
                      u.trust_score AS worker_trust,
                      u.phone      AS worker_phone
               FROM jobs j
               LEFT JOIN users u ON u.id = j.worker_id
               WHERE j.client_id = %s
               ORDER BY j.created_at DESC""",
            (user_id,)
        )
        jobs = cur.fetchall()
        for j in jobs:
            j["amount"]       = float(j["amount"]       or 0)
            j["platform_fee"] = float(j["platform_fee"] or 0)
            j["client_pays"]  = float(j["client_pays"]  or 0)
            j["artisan_gets"] = float(j["artisan_gets"] or 0)
            j["worker_trust"] = float(j["worker_trust"] or 0) if j["worker_trust"] else None
            j["created_at"]   = str(j["created_at"])
            j["paid_at"]      = str(j["paid_at"]) if j.get("paid_at") else None
        return jsonify({"jobs": jobs})
    except Exception as e:
        print(f"[client-jobs error] {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ── Get Applicants ────────────────────────────────────────────────────────────
@client_bp.route("/applicants")
def api_client_applicants():
    """Get all applicants for a specific job"""
    job_id  = request.args.get("job_id",  "").strip()
    user_id = request.args.get("user_id", "").strip()
    if not job_id or not user_id:
        return jsonify({"error": "job_id and user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        # Verify job belongs to client
        cur.execute("SELECT id FROM jobs WHERE id=%s AND client_id=%s", (job_id, user_id))
        if not cur.fetchone():
            return jsonify({"error": "Job not found or not yours"}), 404

        cur.execute(
            """SELECT ja.id, ja.worker_id, ja.status, ja.created_at,
                      u.name, u.trade, u.trust_score, u.jobs_completed, u.phone
               FROM job_applications ja
               JOIN users u ON u.id = ja.worker_id
               WHERE ja.job_id = %s
               ORDER BY u.trust_score DESC""",
            (job_id,)
        )
        apps = cur.fetchall()
        for a in apps:
            a["trust_score"]    = float(a["trust_score"]    or 0)
            a["jobs_completed"] = int(a["jobs_completed"]   or 0)
            a["created_at"]     = str(a["created_at"])
        return jsonify({"applicants": apps})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ── Job Applicants (all pending worker applications across client's jobs) ──────
@client_bp.route("/job-applicants")
def api_client_job_applicants():
    """Get all pending worker applications for this client's jobs (used for dashboard banner)"""
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT ja.id, ja.job_id, ja.worker_id, ja.status, ja.created_at,
                      j.title,
                      u.name AS worker_name, u.trade AS worker_trade,
                      u.trust_score AS worker_trust, u.jobs_completed AS worker_jobs
               FROM job_applications ja
               JOIN jobs j  ON j.id = ja.job_id
               JOIN users u ON u.id = ja.worker_id
               WHERE j.client_id = %s AND ja.status = 'pending'
               ORDER BY ja.created_at DESC""",
            (user_id,)
        )
        apps = cur.fetchall()
        for a in apps:
            a["worker_trust"] = float(a["worker_trust"] or 0)
            a["worker_jobs"]  = int(a["worker_jobs"] or 0)
            a["created_at"]   = str(a["created_at"])
        return jsonify({"applicants": apps})
    except Exception as e:
        print(f"[job-applicants error] {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ── Pending Review Jobs (work submitted, awaiting client approve/dispute) ──────
@client_bp.route("/pending-review-jobs")
def api_client_pending_review_jobs():
    """Get jobs where worker submitted GPS-verified proof and client hasn't reviewed yet"""
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT j.*, u.name AS worker_name
               FROM jobs j
               LEFT JOIN users u ON u.id = j.worker_id
               WHERE j.client_id = %s AND j.status = 'verified'
               ORDER BY j.verified_at DESC""",
            (user_id,)
        )
        jobs = cur.fetchall()
        for j in jobs:
            j["amount"]          = float(j["amount"] or 0)
            j["distance_meters"] = float(j["distance_meters"]) if j.get("distance_meters") else None
            j["created_at"]      = str(j["created_at"])
            j["verified_at"]     = str(j["verified_at"]) if j.get("verified_at") else None
        return jsonify({"jobs": jobs})
    except Exception as e:
        print(f"[pending-review-jobs error] {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ── Assign Worker ─────────────────────────────────────────────────────────────
@client_bp.route("/assign-worker", methods=["POST"])
def api_assign_worker():
    """Assign a worker to a job"""
    data      = request.get_json(silent=True) or {}
    job_id    = data.get("job_id")
    worker_id = str(data.get("worker_id", "")).strip()
    user_id   = str(data.get("user_id",   "")).strip()

    if not all([job_id, worker_id, user_id]):
        return jsonify({"success": False, "message": "job_id, worker_id and user_id required."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT * FROM jobs WHERE id=%s AND client_id=%s AND status='open'",
            (job_id, user_id)
        )
        job = cur.fetchone()
        if not job:
            return jsonify({"success": False, "message": "Job not found, not yours, or already assigned."}), 404

        cur.execute(
            "UPDATE jobs SET worker_id=%s, status='assigned', assigned_at=NOW() WHERE id=%s",
            (worker_id, job_id)
        )
        # Reject other applicants
        cur.execute(
            """UPDATE job_applications SET status='rejected'
               WHERE job_id=%s AND worker_id != %s""",
            (job_id, worker_id)
        )
        cur.execute(
            "UPDATE job_applications SET status='accepted' WHERE job_id=%s AND worker_id=%s",
            (job_id, worker_id)
        )
        conn.commit()
        return jsonify({"success": True, "message": "Worker assigned successfully."})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ── Approve Job ───────────────────────────────────────────────────────────────
@client_bp.route("/approve-job", methods=["POST"])
def api_approve_job():
    """
    Client approves completed job.
    Credits artisan's escrow_balance in DB.
    Actual bank transfer happens when artisan withdraws.
    """
    data    = request.get_json(silent=True) or {}
    job_id  = data.get("job_id")
    user_id = str(data.get("user_id", "")).strip()

    if not job_id or not user_id:
        return jsonify({"success": False, "message": "job_id and user_id required."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT * FROM jobs
               WHERE id=%s AND client_id=%s
               AND status IN ('verified','pending_verification')""",
            (job_id, user_id)
        )
        job = cur.fetchone()
        if not job:
            return jsonify({"success": False,
                            "message": "Job not found or not eligible for approval."}), 404

        # Use stored artisan_gets (fee-adjusted amount)
        # Fall back to raw amount if column not yet populated
        artisan_gets = float(job.get("artisan_gets") or job["amount"] or 0)

        # Mark job paid
        cur.execute(
            "UPDATE jobs SET status='paid', paid_at=NOW() WHERE id=%s",
            (job_id,)
        )

        # Credit artisan's internal balance — no Squad/Paystack call here.
        # Money moves to bank when artisan clicks Withdraw.
        cur.execute(
            """UPDATE users
               SET escrow_balance = escrow_balance + %s,
                   total_earned   = total_earned   + %s
               WHERE id = %s""",
            (artisan_gets, artisan_gets, job["worker_id"])
        )

        conn.commit()
        return jsonify({
            "success":     True,
            "message":     "Job approved. Artisan's balance has been credited.",
            "artisan_gets": artisan_gets,
        })
    except Exception as e:
        conn.rollback()
        print(f"[approve-job error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ── Dispute Job ───────────────────────────────────────────────────────────────
@client_bp.route("/dispute-job", methods=["POST"])
def api_dispute_job():
    """Client raises a dispute on a verified job"""
    data    = request.get_json(silent=True) or {}
    job_id  = data.get("job_id")
    user_id = str(data.get("user_id", "")).strip()
    reason  = data.get("reason", "").strip()

    if not job_id or not user_id or not reason:
        return jsonify({"success": False, "message": "job_id, user_id and reason required."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT * FROM jobs WHERE id=%s AND client_id=%s AND status='verified'",
            (job_id, user_id)
        )
        job = cur.fetchone()
        if not job:
            return jsonify({"success": False,
                            "message": "Job not found or not eligible for dispute."}), 404

        cur.execute(
            "UPDATE jobs SET status='disputed', dispute_reason=%s, disputed_at=NOW() WHERE id=%s",
            (reason, job_id)
        )
        conn.commit()
        return jsonify({"success": True, "message": "Dispute raised. Our team will review within 24 hours."})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@client_bp.route("/bargains")
def api_client_bargains():
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT b.*,
                      j.title        AS job_title,
                      j.amount       AS job_amount,
                      u.name         AS worker_name,
                      u.trade,
                      u.trust_score,
                      COALESCE(b.initiated_by, 'worker') AS initiated_by
               FROM bargains b
               JOIN jobs  j ON j.id  = b.job_id
               JOIN users u ON u.id  = b.worker_id
               WHERE j.client_id = %s
               ORDER BY b.created_at DESC""",
            (user_id,)
        )
        bargains = cur.fetchall()
        for b in bargains:
            b["proposed_price"] = float(b["proposed_price"] or 0)
            b["job_amount"]     = float(b["job_amount"]     or 0)
            b["trust_score"]    = float(b["trust_score"]    or 0)
            b["created_at"]     = str(b["created_at"])
        return jsonify({"bargains": bargains})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@client_bp.route("/respond-bargain", methods=["POST"])
def api_respond_bargain():
    """Client accepts or rejects a counter-offer. On reject, client may
    optionally suggest a price the worker should try instead."""
    data             = request.get_json(silent=True) or {}
    bargain_id       = data.get("bargain_id")
    user_id          = str(data.get("user_id", "")).strip()
    action           = data.get("action", "").strip()   # 'accept' or 'reject'
    suggested_price  = data.get("suggested_price")       # optional, only used on reject

    if not bargain_id or not user_id or action not in ("accept", "reject"):
        return jsonify({"success": False,
                        "message": "bargain_id, user_id and action (accept/reject) required."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT b.*, j.client_id, j.status AS job_status
               FROM bargains b
               JOIN jobs j ON j.id = b.job_id
               WHERE b.id=%s AND j.client_id=%s AND b.status='pending'""",
            (bargain_id, user_id)
        )
        bargain = cur.fetchone()
        if not bargain:
            return jsonify({"success": False, "message": "Bargain not found or already responded."}), 404

        if action == "accept":
            cur.execute("UPDATE bargains SET status='accepted' WHERE id=%s", (bargain_id,))

            # Recalculate fees on new agreed amount
            fees = calculate_fees(bargain["proposed_price"])
            cur.execute(
                """UPDATE jobs
                   SET amount=%s, platform_fee=%s, client_pays=%s, artisan_gets=%s,
                       worker_id=%s, status='assigned', assigned_at=NOW()
                   WHERE id=%s""",
                (fees["amount"], fees["platform_fee"],
                 fees["client_pays"], fees["artisan_gets"],
                 bargain["worker_id"], bargain["job_id"])
            )

            # Auto-reject every other pending bargain on this job —
            # the job is no longer open for negotiation once one is accepted.
            cur.execute(
                "UPDATE bargains SET status='rejected' WHERE job_id=%s AND id != %s AND status='pending'",
                (bargain["job_id"], bargain_id)
            )

        else:  # reject
            try:
                suggested = float(suggested_price) if suggested_price else None
                if suggested is not None and suggested < 100:
                    suggested = None
            except (ValueError, TypeError):
                suggested = None

            cur.execute(
                "UPDATE bargains SET status='rejected', client_suggested_price=%s WHERE id=%s",
                (suggested, bargain_id)
            )

        conn.commit()
        return jsonify({
            "success": True,
            "action":  action,
            "message": f"Counter-offer {'accepted' if action=='accept' else 'rejected'}."
        })
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ── Review Worker Application (assign or decline) ──────────────────────────────
@client_bp.route("/review-worker", methods=["POST"])
def api_review_worker():
    """Client approves or declines a worker's application to a job"""
    data      = request.get_json(silent=True) or {}
    job_id    = data.get("job_id")
    worker_id = str(data.get("worker_id", "")).strip()
    user_id   = str(data.get("user_id", "")).strip()
    action    = data.get("action", "").strip()  # 'assign' or 'decline'

    if not all([job_id, worker_id, user_id]) or action not in ("assign", "decline"):
        return jsonify({"success": False, "message": "job_id, worker_id, user_id and action (assign/decline) required."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT ja.id FROM job_applications ja
               JOIN jobs j ON j.id = ja.job_id
               WHERE ja.job_id=%s AND ja.worker_id=%s AND j.client_id=%s AND ja.status='pending'""",
            (job_id, worker_id, user_id)
        )
        if not cur.fetchone():
            return jsonify({"success": False, "message": "Application not found or already resolved."}), 404

        if action == "assign":
            cur.execute("SELECT status FROM jobs WHERE id=%s AND client_id=%s", (job_id, user_id))
            job = cur.fetchone()
            if not job or job["status"] != "open":
                return jsonify({"success": False, "message": "Job is no longer open."}), 409

            cur.execute(
                "UPDATE jobs SET worker_id=%s, status='assigned', assigned_at=NOW() WHERE id=%s",
                (worker_id, job_id)
            )
            cur.execute(
                "UPDATE job_applications SET status='rejected' WHERE job_id=%s AND worker_id != %s",
                (job_id, worker_id)
            )
            cur.execute(
                "UPDATE job_applications SET status='accepted' WHERE job_id=%s AND worker_id=%s",
                (job_id, worker_id)
            )
        else:  # decline
            cur.execute(
                "UPDATE job_applications SET status='rejected' WHERE job_id=%s AND worker_id=%s",
                (job_id, worker_id)
            )

        conn.commit()
        return jsonify({"success": True, "action": action})
    except Exception as e:
        conn.rollback()
        print(f"[review-worker error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()



# ── Review Job Submission (approve payment or dispute) ─────────────────────────
@client_bp.route("/review-job", methods=["POST"])
def api_review_job():
    """Client approves (releases payment) or disputes a verified job submission"""
    data    = request.get_json(silent=True) or {}
    job_id  = data.get("job_id")
    user_id = str(data.get("user_id", "")).strip()
    action  = data.get("action", "").strip()   # 'approve' or 'dispute'
    reason  = data.get("reason", "").strip()

    if not job_id or not user_id or action not in ("approve", "dispute"):
        return jsonify({"success": False, "message": "job_id, user_id and action (approve/dispute) required."}), 400
    if action == "dispute" and not reason:
        return jsonify({"success": False, "message": "A reason is required to dispute."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT * FROM jobs WHERE id=%s AND client_id=%s AND status='verified'",
            (job_id, user_id)
        )
        job = cur.fetchone()
        if not job:
            return jsonify({"success": False, "already_resolved": True,
                            "message": "Job not found or already resolved."}), 404

        if action == "approve":
            artisan_gets = float(job.get("artisan_gets") or job["amount"] or 0)
            cur.execute("UPDATE jobs SET status='paid', paid_at=NOW() WHERE id=%s", (job_id,))
            cur.execute(
                """UPDATE users SET escrow_balance = escrow_balance + %s,
                   total_earned = total_earned + %s WHERE id=%s""",
                (artisan_gets, artisan_gets, job["worker_id"])
            )
            message = "Payment released to the artisan."
        else:
            cur.execute(
                "UPDATE jobs SET status='disputed', dispute_reason=%s, disputed_at=NOW() WHERE id=%s",
                (reason, job_id)
            )
            message = "Dispute submitted. Our team will review within 24 hours."

        conn.commit()
        return jsonify({"success": True, "action": action, "message": message})
    except Exception as e:
        conn.rollback()
        print(f"[review-job error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ── Rate Worker ──────────────────────────────────────────────────────────────
@client_bp.route("/rate-worker", methods=["POST"])
def api_rate_worker():
    """Client rates a worker after a verified/paid job"""
    data    = request.get_json(silent=True) or {}
    job_id  = data.get("job_id")
    user_id = str(data.get("user_id", "")).strip()
    rating  = data.get("rating")
    comment = data.get("comment", "").strip()

    if not job_id or not user_id or not rating:
        return jsonify({"success": False, "message": "job_id, user_id and rating required."}), 400

    try:
        rating = int(rating)
        if rating < 1 or rating > 5:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Rating must be between 1 and 5."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT * FROM jobs WHERE id=%s AND client_id=%s
               AND status IN ('verified','paid') AND distance_meters <= 100""",
            (job_id, user_id)
        )
        job = cur.fetchone()
        if not job:
            return jsonify({"success": False, "message": "Job not eligible for rating."}), 404
        if job.get("client_rating") is not None:
            return jsonify({"success": False, "message": "This job has already been rated."}), 409

        cur.execute(
            "UPDATE jobs SET client_rating=%s, client_rating_comment=%s WHERE id=%s",
            (rating, comment, job_id)
        )
        conn.commit()
        return jsonify({"success": True, "message": "Rating saved."})
    except Exception as e:
        conn.rollback()
        print(f"[rate-worker error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ── Delete Job ───────────────────────────────────────────────────────────────
@client_bp.route("/delete-job", methods=["DELETE"])
def api_delete_job():
    """Delete a job — only allowed while status is still 'open'"""
    data    = request.get_json(silent=True) or {}
    job_id  = data.get("job_id")
    user_id = str(data.get("user_id", "")).strip()

    if not job_id or not user_id:
        return jsonify({"success": False, "message": "job_id and user_id required."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id FROM jobs WHERE id=%s AND client_id=%s AND status='open'",
            (job_id, user_id)
        )
        if not cur.fetchone():
            return jsonify({"success": False, "message": "Job not found, not yours, or no longer open."}), 404

        cur.execute("DELETE FROM job_applications WHERE job_id=%s", (job_id,))
        cur.execute("DELETE FROM bargains WHERE job_id=%s", (job_id,))
        cur.execute("DELETE FROM jobs WHERE id=%s", (job_id,))
        conn.commit()
        return jsonify({"success": True, "message": "Job deleted."})
    except Exception as e:
        conn.rollback()
        print(f"[delete-job error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ── Fee Preview ───────────────────────────────────────────────────────────────
@client_bp.route("/fee-preview")
def api_fee_preview():
    """
    Lightweight endpoint so the post-job form can show a live fee
    breakdown before the client submits. Call with ?amount=10000
    """
    try:
        amount = float(request.args.get("amount", 0))
        fees   = calculate_fees(amount)
        return jsonify({
            "success":    True,
            "amount":     fees["amount"],
            "your_fee":   fees["client_fee"],
            "you_pay":    fees["client_pays"],
            "artisan_gets": fees["artisan_gets"],
            "platform_fee": fees["platform_fee"],
            "cap_applied":  fees["client_fee"] >= MAX_CLIENT_FEE,
            "cap_at":       MAX_CLIENT_FEE,
        })
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid amount"}), 400