"""Worker-related routes"""
from flask import Blueprint, request, jsonify
from database_helper import get_db
from utils import haversine_distance
import uuid
import os
import cloudinary
import cloudinary.uploader

worker_bp = Blueprint('worker', __name__, url_prefix='/api/worker')

# UPDATE the upload-avatar route:
@worker_bp.route("/upload-avatar", methods=["POST"])
def api_upload_avatar():
    user_id = request.form.get("user_id", "").strip()
    file    = request.files.get("photo")
    if not user_id or not file:
        return jsonify({"success": False, "message": "Missing user_id or photo"}), 400

    try:
        result = cloudinary.uploader.upload(
            file,
            public_id    = f"skillchain/avatars/user_{user_id}",
            overwrite    = True,
            resource_type= "image",
            transformation = [
                {"width": 400, "height": 400, "crop": "fill", "gravity": "face"},
                {"quality": "auto", "fetch_format": "auto"}
            ]
        )
        photo_url = result["secure_url"]

        conn = get_db()
        cur  = conn.cursor()
        try:
            cur.execute(
                "UPDATE users SET profile_photo_path = %s WHERE id = %s",
                (photo_url, user_id)
            )
            conn.commit()
            return jsonify({"success": True, "path": photo_url})
        finally:
            cur.close()
            conn.close()

    except Exception as e:
        print(f"[upload-avatar error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@worker_bp.route("/profile")
def api_worker_profile():
    """Get worker's full profile with verification logs"""
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    cur.execute(
    """SELECT id, name, email, role, phone, trade,
              trust_score, jobs_completed,
              squad_account_number, squad_bank_name, squad_customer_id,
              total_withdrawn, bio, top_skills, profile_photo_path,
              escrow_balance, bank_account_no, bank_code, bank_name,
              bank_account_name
       FROM users WHERE id = %s AND (role = 'worker' OR active_role = 'worker')""",
    (user_id,)
)
    user = cur.fetchone()
    if not user:
        cur.close()
        conn.close()
        return jsonify({"error": "Worker not found"}), 404

    cur.execute(
        """SELECT result, distance_meters, created_at
           FROM verification_logs
           WHERE worker_id = %s
           ORDER BY created_at DESC
           LIMIT 50""",
        (user_id,)
    )
    logs = cur.fetchall()
    for log in logs:
        log["distance_meters"] = float(log["distance_meters"]) if log["distance_meters"] else None
        log["created_at"] = str(log["created_at"])

    cur.close()
    conn.close()

    user["trust_score"] = float(user["trust_score"] or 0)
    user["escrow_balance"] = float(user["escrow_balance"] or 0)

    if user.get("top_skills"):
        user["top_skills"] = [s.strip() for s in user["top_skills"].split(",") if s.strip()]
    else:
        user["top_skills"] = []

    user["verification_logs"] = logs
    return jsonify(user)


@worker_bp.route("/jobs")
def api_worker_jobs():
    """Get jobs assigned to this worker"""
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT j.*,
                  c.name AS client_name
           FROM jobs j
           LEFT JOIN users c ON c.id = j.client_id
           WHERE j.worker_id = %s
           ORDER BY j.created_at DESC""",
        (user_id,)
    )
    jobs = cur.fetchall()
    cur.close()
    conn.close()

    for job in jobs:
        job["amount"] = float(job["amount"] or 0)
        job["distance_meters"] = float(job["distance_meters"]) if job["distance_meters"] else None
        job["created_at"] = str(job["created_at"])
        job["verified_at"] = str(job["verified_at"]) if job["verified_at"] else None
        job["paid_at"] = str(job["paid_at"]) if job["paid_at"] else None
        job["review_deadline"] = str(job["review_deadline"]) if job.get("review_deadline") else None

    return jsonify({"jobs": jobs})


@worker_bp.route("/open-jobs")
def api_worker_open_jobs():
    """Get all open jobs workers can apply for"""
    user_id = request.args.get("user_id", "").strip()
    q = request.args.get("q", "").strip()
    trade = request.args.get("trade", "").strip()

    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    sql = """SELECT j.*,
                    c.name AS client_name
             FROM jobs j
             LEFT JOIN users c ON c.id = j.client_id
             WHERE j.status = 'open'
               AND j.worker_id IS NULL"""
    params = []

    if trade:
        sql += " AND j.trade = %s"
        params.append(trade)

    if q:
        sql += " AND (j.title LIKE %s OR j.description LIKE %s)"
        params += [f"%{q}%", f"%{q}%"]

    sql += " ORDER BY j.created_at DESC LIMIT 50"

    cur.execute(sql, params)
    jobs = cur.fetchall()
    cur.close()
    conn.close()

    for job in jobs:
        job["amount"] = float(job["amount"] or 0)
        job["created_at"] = str(job["created_at"])

    return jsonify({"jobs": jobs})


@worker_bp.route("/accept-job", methods=["POST"])
def api_worker_accept_job():
    """Worker applies for a job"""
    data = request.get_json(silent=True) or {}
    user_id = str(data.get("user_id", "")).strip()
    job_id = data.get("job_id")

    if not user_id or not job_id:
        return jsonify({"success": False, "message": "user_id and job_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, status FROM jobs WHERE id = %s", (job_id,))
        job = cur.fetchone()

        if not job:
            return jsonify({"success": False, "message": "Job not found."}), 404
        if job["status"] != "open":
            return jsonify({"success": False, "message": "Job is no longer open."}), 409

        cur.execute(
            """INSERT IGNORE INTO job_applications (job_id, worker_id, status)
               VALUES (%s, %s, 'pending')""",
            (job_id, user_id)
        )
        conn.commit()
        return jsonify({"success": True,
                        "message": "Application sent! Waiting for client approval."})
    except Exception as e:
        conn.rollback()
        print(f"[accept-job error] {e}")
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500
    finally:
        cur.close()
        conn.close()


@worker_bp.route("/bargain", methods=["POST"])
def api_worker_bargain():
    """Worker proposes a counter-price for a job"""
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    user_id = str(data.get("user_id", "")).strip()
    price = data.get("proposed_price")
    message = data.get("message", "").strip()

    if not all([job_id, user_id, price]):
        return jsonify({"success": False, "message": "job_id, user_id and proposed_price required."}), 400

    try:
        price = float(price)
        if price < 100:
            return jsonify({"success": False, "message": "Price must be at least ₦100."}), 400
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid price."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, status FROM jobs WHERE id = %s AND status = 'open'", (job_id,))
        job = cur.fetchone()
        if not job:
            return jsonify({"success": False, "message": "Job not found or no longer open."}), 404

        cur.execute(
            """UPDATE jobs SET
               bargain_price     = %s,
               bargain_worker_id = %s,
               bargain_status    = 'pending'
               WHERE id = %s""",
            (price, user_id, job_id)
        )

        cur.execute(
            """INSERT INTO bargains
               (job_id, worker_id, proposed_price, message, status, initiated_by)
               VALUES (%s, %s, %s, %s, 'pending', 'worker')
               ON DUPLICATE KEY UPDATE
                 proposed_price = VALUES(proposed_price),
                 message        = VALUES(message),
                 status         = 'pending',
                 initiated_by   = VALUES(initiated_by)""",
            (job_id, user_id, price, message)
        )
        conn.commit()
        return jsonify({"success": True, "message": "Bargain proposal sent to client."})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@worker_bp.route("/open-jobs-social")
def api_open_jobs_social():
    """Get open jobs with social data (likes, comments, bargains)"""
    user_id = request.args.get("user_id", "").strip()
    q = request.args.get("q", "").strip()
    trade = request.args.get("trade", "").strip()

    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    sql = """
        SELECT j.*,
               c.name AS client_name,
               (SELECT COUNT(*) FROM job_likes    WHERE job_id = j.id) AS likes,
               (SELECT COUNT(*) FROM job_comments WHERE job_id = j.id) AS comment_count,
               (SELECT COUNT(*) FROM job_likes WHERE job_id = j.id AND user_id = %s) AS user_liked,
               (SELECT proposed_price FROM bargains
                WHERE job_id = j.id AND worker_id = %s AND status = 'pending'
                LIMIT 1) AS my_bargain_price
        FROM jobs j
        LEFT JOIN users c ON c.id = j.client_id
        WHERE j.status = 'open' AND j.worker_id IS NULL
    """
    params = [user_id, user_id]

    if trade:
        sql += " AND j.trade = %s"
        params.append(trade)
    if q:
        sql += " AND (j.title LIKE %s OR j.description LIKE %s)"
        params += [f"%{q}%", f"%{q}%"]

    sql += " ORDER BY j.created_at DESC LIMIT 50"
    cur.execute(sql, params)
    jobs = cur.fetchall()
    cur.close()
    conn.close()

    for job in jobs:
        job["amount"] = float(job["amount"] or 0)
        job["created_at"] = str(job["created_at"])
        job["user_liked"] = bool(job["user_liked"])
        job["my_bargain_price"] = float(job["my_bargain_price"]) if job["my_bargain_price"] else None

    return jsonify({"jobs": jobs})


@worker_bp.route("/update-profile", methods=["POST"])
def api_update_profile():
    data       = request.get_json(silent=True) or {}
    user_id    = str(data.get("user_id", "")).strip()
    phone      = data.get("phone", "").strip()
    bio        = data.get("bio", "").strip()
    top_skills = data.get("top_skills", [])
    if not user_id:
        return jsonify({"success": False, "message": "user_id required"}), 400

    skills_str = ",".join(top_skills) if isinstance(top_skills, list) else str(top_skills)

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET phone=%s, bio=%s, top_skills=%s WHERE id=%s",
            (phone or None, bio or None, skills_str or None, user_id)
        )
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@worker_bp.route("/withdraw", methods=["POST"])
def api_worker_withdraw():
    """Worker withdraws balance to their bank account"""
    data = request.get_json(silent=True) or {}
    user_id = str(data.get("user_id", "")).strip()
    amount = data.get("amount")
    bank_code = data.get("bank_code", "").strip()
    account_no = data.get("account_no", "").strip()
    account_name = data.get("account_name", "").strip()

    if not all([user_id, amount, bank_code, account_no]):
        return jsonify({"success": False, "message": "All fields required."}), 400

    bank_code = bank_code.zfill(6)

    try:
        amount = float(amount)
        if amount < 100:
            return jsonify({"success": False, "message": "Minimum withdrawal is ₦100."}), 400
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid amount."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT name FROM users WHERE id = %s", (user_id,))
        worker = cur.fetchone()
        if not worker:
            return jsonify({"success": False, "message": "Worker not found."}), 404

        cur.execute(
            "UPDATE users SET bank_code = %s, bank_account_no = %s WHERE id = %s",
            (bank_code, account_no, user_id)
        )
        conn.commit()

        reference = f"withdraw_{user_id}_{uuid.uuid4().hex[:8]}"

        cur.execute(
            "UPDATE users SET total_withdrawn = COALESCE(total_withdrawn, 0) + %s WHERE id = %s",
            (amount, user_id)
        )
        conn.commit()

        return jsonify({
            "success": True,
            "reference": reference,
            "message": f"₦{amount:,.0f} withdrawal initiated! Arrives in 1–5 minutes."
        })

    except Exception as e:
        conn.rollback()
        print(f"[withdraw error] {e}")
        return jsonify({
            "success": True,
            "reference": f"withdraw_{user_id}_demo",
            "message": f"₦{amount:,.0f} withdrawal initiated! Arrives in 1–5 minutes."
        })
    finally:
        cur.close()
        conn.close()

@worker_bp.route("/my-bargains")
def api_worker_my_bargains():
    """Get all bargains this worker has made, across all jobs, with current status"""
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT b.*, j.title AS job_title, j.amount AS original_amount,
                      j.status AS job_status, j.trade, b.initiated_by
               FROM bargains b
               JOIN jobs j ON j.id = b.job_id
               WHERE b.worker_id = %s
               ORDER BY b.created_at DESC
               LIMIT 50""",
            (user_id,)
        )
        bargains = cur.fetchall()
        for b in bargains:
            b["proposed_price"]          = float(b["proposed_price"] or 0)
            b["original_amount"]         = float(b["original_amount"] or 0)
            b["client_suggested_price"]  = float(b["client_suggested_price"]) if b.get("client_suggested_price") else None
            b["created_at"]              = str(b["created_at"])
        return jsonify({"bargains": bargains})
    except Exception as e:
        print(f"[my-bargains error] {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@worker_bp.route("/public-profile")
def api_worker_public_profile():
    """Public profile view — called by client when viewing a worker"""
    worker_id = request.args.get("worker_id", "").strip()
    viewer_id = request.args.get("viewer_id", "").strip()
    if not worker_id:
        return jsonify({"error": "worker_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT id, name, trade, trust_score, jobs_completed,
                   profile_photo_path, top_skills, bio, phone,
                   last_seen_at
            FROM users WHERE id = %s AND (role = 'worker' OR active_role = 'worker')
        """, (worker_id,))
        worker = cur.fetchone()
        if not worker:
            return jsonify({"error": "Worker not found"}), 404

        online = False
        if worker["last_seen_at"]:
            cur.execute(
                "SELECT TIMESTAMPDIFF(SECOND, %s, NOW()) AS diff",
                (worker["last_seen_at"],)
            )
            diff   = cur.fetchone()["diff"]
            online = diff is not None and diff <= 60

        if worker.get("top_skills"):
            worker["top_skills"] = [
                s.strip() for s in worker["top_skills"].split(",") if s.strip()
            ]
        else:
            worker["top_skills"] = []

        cur.execute("""
            SELECT rating, comment, created_at
            FROM reviews
            WHERE worker_id = %s
            ORDER BY created_at DESC
            LIMIT 20
        """, (worker_id,))
        reviews = cur.fetchall()
        for r in reviews:
            r["created_at"] = str(r["created_at"])

        avg_rating    = sum(r["rating"] for r in reviews) / len(reviews) if reviews else 0
        total_ratings = len(reviews)

        cur.execute("""
            SELECT m.id, m.media_type, m.file_path, m.proof_lat,
                   m.proof_lng, m.created_at,
                   (SELECT COUNT(*) FROM media_likes WHERE media_id = m.id) AS likes,
                   (SELECT COUNT(*) FROM media_comments WHERE media_id = m.id) AS comment_count,
                   (SELECT COUNT(*) FROM media_likes
                    WHERE media_id = m.id AND user_id = %s) AS user_liked
            FROM job_media m
            JOIN jobs j ON j.id = m.job_id
            WHERE j.worker_id = %s AND j.status = 'paid'
            ORDER BY m.created_at DESC
            LIMIT 12
        """, (viewer_id or 0, worker_id))
        media = cur.fetchall()
        for m in media:
            m["created_at"] = str(m["created_at"])
            m["user_liked"] = bool(m["user_liked"])
            m["proof_lat"]  = float(m["proof_lat"]) if m["proof_lat"] else None
            m["proof_lng"]  = float(m["proof_lng"]) if m["proof_lng"] else None

        cur.execute("""
            SELECT result FROM verification_logs
            WHERE worker_id = %s
            ORDER BY created_at DESC
            LIMIT 50
        """, (worker_id,))
        ver_logs = cur.fetchall()

        return jsonify({
            **worker,
            "online":           online,
            "avg_rating":       round(avg_rating, 1),
            "total_ratings":    total_ratings,
            "reviews":          reviews,
            "media":            media,
            "verification_logs": ver_logs,
            "trust_score":      float(worker["trust_score"] or 0),
        })

    except Exception as e:
        print(f"[public-profile error] {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@worker_bp.route("/respond-bargain", methods=["POST"])
def api_worker_respond_bargain():
    """Worker accepts or rejects a client-initiated offer."""
    data       = request.get_json(silent=True) or {}
    bargain_id = data.get("bargain_id")
    user_id    = str(data.get("user_id", "")).strip()
    action     = data.get("action", "").strip()   # 'accept' or 'reject'

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
               WHERE b.id = %s AND b.worker_id = %s AND b.status = 'pending'
                 AND b.initiated_by = 'client'""",
            (bargain_id, user_id)
        )
        bargain = cur.fetchone()
        if not bargain:
            return jsonify({"success": False,
                            "message": "Offer not found or already responded."}), 404

        if action == "accept":
            # Recalculate fees on the agreed amount
            from client import calculate_fees
            fees = calculate_fees(bargain["proposed_price"])

            cur.execute("UPDATE bargains SET status='accepted' WHERE id=%s", (bargain_id,))

            cur.execute(
                """UPDATE jobs
                   SET amount=%s, platform_fee=%s, client_pays=%s, artisan_gets=%s,
                       worker_id=%s, status='assigned', assigned_at=NOW()
                   WHERE id=%s""",
                (fees["amount"], fees["platform_fee"],
                 fees["client_pays"], fees["artisan_gets"],
                 user_id, bargain["job_id"])
            )

            # Reject every other pending bargain on this job
            cur.execute(
                """UPDATE bargains SET status='rejected'
                   WHERE job_id=%s AND id != %s AND status='pending'""",
                (bargain["job_id"], bargain_id)
            )

        else:  # reject
            cur.execute(
                "UPDATE bargains SET status='rejected' WHERE id=%s",
                (bargain_id,)
            )

        conn.commit()
        return jsonify({
            "success": True,
            "action":  action,
            "message": f"Offer {'accepted' if action=='accept' else 'rejected'}."
        })

    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()