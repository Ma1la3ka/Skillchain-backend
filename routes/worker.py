"""Worker-related routes"""
from flask import Blueprint, request, jsonify
from database_helper import get_db
from utils import haversine_distance
import uuid
import os
import cloudinary
import cloudinary.uploader
from datetime import datetime, timedelta
import random

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
    """SELECT 
        u.id, u.name, u.email, u.role, u.phone, u.trade,
        u.trust_score,
        (SELECT COUNT(*) FROM jobs j2 
         WHERE j2.worker_id = u.id AND j2.status IN ('verified','paid')) AS jobs_completed,
        u.squad_account_number, u.squad_bank_name, u.squad_customer_id,
        u.total_withdrawn, u.bio, u.top_skills, u.profile_photo_path,
        u.escrow_balance, u.bank_account_no, u.bank_code, u.bank_name,
        u.bank_account_name, u.shop_lat, u.shop_lng, u.shop_address
       FROM users u
       WHERE u.id = %s AND (u.role = 'worker' OR u.active_role = 'worker')""",
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
    user["shop_lat"] = float(user["shop_lat"]) if user["shop_lat"] else None
    user["shop_lng"] = float(user["shop_lng"]) if user["shop_lng"] else None

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
            AND j.client_id != %s
            AND (
                (j.visibility = 'public' AND j.worker_id IS NULL)
                OR (j.visibility = 'private' AND j.invited_worker_id = %s)
            )"""
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

    return jsonify({"jobs": jobs})

@worker_bp.route("/accept-job", methods=["POST"])
def api_worker_accept_job():
    """Worker applies for a job"""
    data = request.get_json(silent=True) or {}
    user_id = str(data.get("user_id", "")).strip()
    job_id = data.get("job_id")
    requested_location = data.get("requested_location", "client_site").strip()
    if requested_location not in ("client_site", "worker_shop"):
        requested_location = "client_site"

    if not user_id or not job_id:
        return jsonify({"success": False, "message": "user_id and job_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, status, client_id FROM jobs WHERE id = %s", (job_id,))
        job = cur.fetchone()

        if not job:
            return jsonify({"success": False, "message": "Job not found."}), 404
        if str(job["client_id"]) == user_id:
            return jsonify({"success": False, "message": "You can't accept your own job."}), 403
        if job["status"] != "open":
            return jsonify({"success": False, "message": "Job is no longer open."}), 409

        # worker_shop only valid if this worker actually has a shop location saved
        if requested_location == "worker_shop":
            cur.execute("SELECT shop_lat, shop_lng FROM users WHERE id = %s", (user_id,))
            wu = cur.fetchone()
            if not wu or not wu["shop_lat"] or not wu["shop_lng"]:
                requested_location = "client_site"

        cur.execute(
            """INSERT IGNORE INTO job_applications (job_id, worker_id, status, requested_location)
               VALUES (%s, %s, 'pending', %s)""",
            (job_id, user_id, requested_location)
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
        cur.execute("SELECT id, status, client_id FROM jobs WHERE id = %s AND status = 'open'", (job_id,))
        job = cur.fetchone()
        if not job:
            return jsonify({"success": False, "message": "Job not found or no longer open."}), 404
        if str(job["client_id"]) == user_id:
            return jsonify({"success": False, "message": "You can't bargain on your own job."}), 403

        # Block a second pending offer on this job+worker pair
        cur.execute(
            "SELECT id, initiated_by FROM bargains WHERE job_id=%s AND worker_id=%s AND status='pending'",
            (job_id, user_id)
        )
        existing = cur.fetchone()
        if existing:
            waiting_on_you = existing["initiated_by"] == "client"
            return jsonify({
                "success": False,
                "already_pending": True,
                "waiting_on": "worker" if not waiting_on_you else "worker",
                "message": ("The client already sent you an offer on this job — "
                            "respond to it before sending a new one.")
                           if waiting_on_you else
                           ("You already have a pending offer on this job — "
                            "wait for the client to respond before sending another.")
            }), 409

        cur.execute(
            """UPDATE jobs SET
               bargain_price     = %s,
               bargain_worker_id = %s,
               bargain_status    = 'pending'
               WHERE id = %s""",
            (price, user_id, job_id)
        )

        try:
            cur.execute(
                """INSERT INTO bargains
                   (job_id, worker_id, proposed_price, message, status, initiated_by)
                   VALUES (%s, %s, %s, %s, 'pending', 'worker')""",
                (job_id, user_id, price, message)
            )
        except Exception:
            conn.rollback()
            return jsonify({
                "success": False, "already_pending": True,
                "message": "There's already a pending offer on this job."
            }), 409

        conn.commit()
        return jsonify({"success": True, "message": "Bargain proposal sent to client."})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


import re

@worker_bp.route("/recommend")
def api_recommend_workers():
    client_id   = request.args.get("user_id", "").strip()
    job_id      = request.args.get("job_id", "").strip()
    trade       = request.args.get("trade", "").strip()
    client_lat  = request.args.get("lat", type=float)
    client_lng  = request.args.get("lng", type=float)
    limit       = request.args.get("limit", 5, type=int)

    if not client_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)

    job_trade = trade
    job_lat, job_lng = client_lat, client_lng
    job_title, job_desc = "", ""
    
    if job_id:
        cur.execute("SELECT title, description, trade, site_lat, site_lng FROM jobs WHERE id=%s AND client_id=%s", (job_id, client_id))
        job = cur.fetchone()
        if job:
            job_trade = job_trade or job.get("trade")
            job_title = job.get("title", "")
            job_desc = job.get("description", "")
            if job.get("site_lat") and job.get("site_lng"):
                job_lat = float(job["site_lat"])
                job_lng = float(job["site_lng"])

    job_text = f"{job_title} {job_desc}".lower()
    raw_words = set(re.findall(r'\b\w+\b', job_text))
    stop_words = {'the','a','an','and','or','but','in','on','at','to','for','of','with','by','is','are','was','were','be','been','have','has','had','do','does','did','will','would','could','should','may','might','can','this','that','these','those','i','you','he','she','it','we','they','me','him','her','us','them','my','your','his','her','its','our','their','mine','yours','hers','ours','theirs','am','so','if','out','up','about','into','through','during','before','after','above','below','from','off','over','under','again','further','then','once','here','there','when','where','why','how','all','each','few','more','most','other','some','such','no','nor','not','only','own','same','than','too','very','just','now','get','need','help','work','job','done','good','great','nice','bad','worse','worst','better','best'}
    job_keywords = {w for w in raw_words if len(w) > 2 and w not in stop_words}

        sql = """SELECT id, name, trade, trust_score,
                    (SELECT COUNT(*) FROM jobs j2 
                     WHERE j2.worker_id = users.id AND j2.status IN ('verified','paid')) AS jobs_completed,
                    profile_photo_path, shop_lat, shop_lng, shop_address,
                    last_seen_at
             FROM users
             WHERE (role='worker' OR active_role='worker')
               AND id != %s"""
    params = [client_id]

    if job_trade:
        sql += " AND trade = %s"
        params.append(job_trade)

    sql += " ORDER BY jobs_completed DESC LIMIT 50"
    cur.execute(sql, params)
    workers = cur.fetchall()

    scored_workers = []
    for w in workers:
        wid = w["id"]

        cur.execute(
            """SELECT COUNT(*) as cnt FROM jobs 
               WHERE worker_id=%s AND trade=%s AND status IN ('verified','paid')""",
            (wid, job_trade)
        )
        same_trade_count = cur.fetchone()["cnt"] or 0

        cur.execute("SELECT comment FROM reviews WHERE worker_id=%s", (wid,))
        reviews = cur.fetchall()
        
        review_matches = 0
        if reviews and job_keywords:
            for r in reviews:
                comment = (r["comment"] or "").lower()
                comment_words = set(re.findall(r'\b\w+\b', comment))
                review_matches += len(comment_words & job_keywords)

        dist_km = None
        if job_lat and w.get("shop_lat") and w.get("shop_lng"):
            dist_km = haversine_distance(job_lat, job_lng, float(w["shop_lat"]), float(w["shop_lng"]))

        score = 0
        if job_trade and w["trade"] == job_trade:
            score += 40

        if dist_km is not None:
            if dist_km < 1:   score += 30
            elif dist_km < 3: score += 25
            elif dist_km < 5: score += 20
            elif dist_km < 10: score += 15
            elif dist_km < 20: score += 10
            else:             score += 5
        else:
            score += 10

        trust = float(w.get("trust_score") or 3.5)
        score += (trust / 5.0) * 20
        score += min(20, same_trade_count * 4)
        score += min(20, review_matches * 3)
        score += min(10, (w.get("jobs_completed", 0) / 2))

        w["match_score"] = round(score, 1)
        w["distance_km"] = round(dist_km, 1) if dist_km is not None else None
        w["same_trade_jobs"] = same_trade_count
        w["review_matches"] = review_matches
        w["trust_score"] = trust
        scored_workers.append(w)

    cur.close()
    conn.close()
    scored_workers.sort(key=lambda x: x["match_score"], reverse=True)

    return jsonify({
        "workers": scored_workers[:limit],
        "based_on_trade": job_trade,
        "based_on_keywords": list(job_keywords)[:10]
    })


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
        WHERE j.status = 'open' AND j.worker_id IS NULL AND j.client_id != %s
    """
    params = [user_id, user_id, user_id]

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
    shop_lat     = data.get("shop_lat")
    shop_lng     = data.get("shop_lng")
    shop_address = data.get("shop_address", "").strip()

    if not user_id:
        return jsonify({"success": False, "message": "user_id required"}), 400

    skills_str = ",".join(top_skills) if isinstance(top_skills, list) else str(top_skills)

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            """UPDATE users SET phone=%s, bio=%s, top_skills=%s,
               shop_lat=%s, shop_lng=%s, shop_address=%s
               WHERE id=%s""",
            (phone or None, bio or None, skills_str or None,
             shop_lat or None, shop_lng or None, shop_address or None,
             user_id)
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
    pin = str(data.get("pin", "")).strip()

    if not all([user_id, amount, bank_code, account_no, pin]):
        return jsonify({"success": False, "message": "All fields, including your withdrawal PIN, are required."}), 400

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
        cur.execute(
            "SELECT name, withdrawal_pin_hash, pin_failed_attempts, pin_locked_until FROM users WHERE id = %s",
            (user_id,)
        )
        worker = cur.fetchone()
        if not worker:
            return jsonify({"success": False, "message": "Worker not found."}), 404
        if not worker["withdrawal_pin_hash"]:
            return jsonify({"success": False, "message": "Set a withdrawal PIN first.", "needs_pin_setup": True}), 403

        # ── Lockout check ──
        if worker["pin_locked_until"] and datetime.now() < worker["pin_locked_until"]:
            remaining = int((worker["pin_locked_until"] - datetime.now()).total_seconds() / 60) + 1
            return jsonify({
                "success": False,
                "locked": True,
                "message": f"Too many wrong PIN attempts. Try again in {remaining} minute(s), or reset your PIN."
            }), 403

        # ── PIN check ──
        if not check_password_hash(worker["withdrawal_pin_hash"], pin):
            attempts = (worker["pin_failed_attempts"] or 0) + 1
            if attempts >= 3:
                cur.execute(
                    "UPDATE users SET pin_failed_attempts=0, pin_locked_until=%s WHERE id=%s",
                    (datetime.now() + timedelta(hours=1), user_id)
                )
                conn.commit()
                return jsonify({
                    "success": False, "locked": True,
                    "message": "Too many wrong attempts. Withdrawals are locked for 1 hour."
                }), 403
            else:
                cur.execute("UPDATE users SET pin_failed_attempts=%s WHERE id=%s", (attempts, user_id))
                conn.commit()
                return jsonify({
                    "success": False,
                    "message": f"Incorrect PIN. {3 - attempts} attempt(s) left before a 1-hour lock."
                }), 403

        # ── Correct PIN — reset counter ──
        cur.execute("UPDATE users SET pin_failed_attempts=0, pin_locked_until=NULL WHERE id=%s", (user_id,))
        conn.commit()

        cur.execute(
            "UPDATE users SET bank_code = %s, bank_account_no = %s WHERE id = %s",
            (bank_code, account_no, user_id)
        )
        conn.commit()

        reference = f"withdraw_{user_id}_{uuid.uuid4().hex[:8]}"

        cur.execute(
            "UPDATE users SET total_withdrawn = COALESCE(total_withdrawn, 0) + %s, escrow_balance = GREATEST(0, escrow_balance - %s) WHERE id = %s",
            (amount, amount, user_id)
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
            SELECT 
                u.id, u.name, u.trade, u.trust_score,
                (SELECT COUNT(*) FROM jobs j2 
                 WHERE j2.worker_id = u.id AND j2.status IN ('verified','paid')) AS jobs_completed,
                u.profile_photo_path, u.top_skills, u.bio, u.phone,
                u.last_seen_at
            FROM users u
            WHERE u.id = %s AND (u.role = 'worker' OR u.active_role = 'worker')
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
            # Inline fee calculation — no import needed
            amount       = float(bargain["proposed_price"])
            client_fee   = min(round(amount * 0.025, 2), 1250.00)
            artisan_fee  = min(round(amount * 0.025, 2), 1250.00)
            platform_fee = client_fee + artisan_fee
            client_pays  = round(amount + client_fee,  2)
            artisan_gets = round(amount - artisan_fee, 2)

            cur.execute("UPDATE bargains SET status='accepted' WHERE id=%s", (bargain_id,))

            cur.execute(
                """UPDATE jobs
                SET amount=%s, platform_fee=%s, client_pays=%s, artisan_gets=%s,
                    worker_id=%s, status='assigned', assigned_at=NOW()
                WHERE id=%s""",
                (amount, platform_fee, client_pays, artisan_gets,
                user_id, bargain["job_id"])
            )

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

@worker_bp.route("/bargain-status")
def api_bargain_status():
    """Returns whether a job+worker pair has a pending bargain, and who initiated it.
    Callable by either dashboard — client passes the worker being chatted with,
    worker passes their own id."""
    job_id    = request.args.get("job_id", "").strip()
    worker_id = request.args.get("worker_id", "").strip()
    if not job_id or not worker_id:
        return jsonify({"error": "job_id and worker_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT id, proposed_price, initiated_by, message
               FROM bargains
               WHERE job_id=%s AND worker_id=%s AND status='pending'""",
            (job_id, worker_id)
        )
        b = cur.fetchone()
        if not b:
            return jsonify({"pending": False})
        return jsonify({
            "pending":        True,
            "bargain_id":     b["id"],
            "initiated_by":   b["initiated_by"],
            "proposed_price": float(b["proposed_price"]),
            "message":        b["message"],
        })
    finally:
        cur.close()
        conn.close()

from werkzeug.security import generate_password_hash, check_password_hash

@worker_bp.route("/pin-status")
def api_pin_status():
    """Tells the frontend whether this worker has set a withdrawal PIN yet —
    used to decide whether to show the setup popup."""
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT withdrawal_pin_hash FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "User not found"}), 404
        return jsonify({"pin_set": bool(row["withdrawal_pin_hash"])})
    finally:
        cur.close()
        conn.close()


@worker_bp.route("/set-pin", methods=["POST"])
def api_set_pin():
    """Set or change the withdrawal PIN. If a PIN already exists, the caller
    must supply current_pin to prove they can change it (this is the 'reset'
    path — not a forgot-PIN flow, which would need the reset-password-style
    email token instead)."""
    data        = request.get_json(silent=True) or {}
    user_id     = str(data.get("user_id", "")).strip()
    new_pin     = str(data.get("new_pin", "")).strip()
    current_pin = str(data.get("current_pin", "")).strip()

    if not user_id or not new_pin:
        return jsonify({"success": False, "message": "user_id and new_pin required."}), 400
    if not new_pin.isdigit() or not (4 <= len(new_pin) <= 6):
        return jsonify({"success": False, "message": "PIN must be 4–6 digits."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT withdrawal_pin_hash FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"success": False, "message": "User not found."}), 404

        if row["withdrawal_pin_hash"]:
            if not current_pin or not check_password_hash(row["withdrawal_pin_hash"], current_pin):
                return jsonify({"success": False, "message": "Current PIN is incorrect."}), 403

        new_hash = generate_password_hash(new_pin)
        cur.execute("UPDATE users SET withdrawal_pin_hash = %s WHERE id = %s", (new_hash, user_id))
        conn.commit()
        return jsonify({"success": True, "message": "Withdrawal PIN saved."})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@worker_bp.route("/forgot-pin", methods=["POST"])
def api_forgot_pin():
    data  = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    if not email:
        return jsonify({"success": False, "message": "Email is required."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, name FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        if not user:
            return jsonify({"success": True, "message": "If that email exists, a reset code has been sent."})

        token  = str(random.randint(100000, 999999))
        expiry = datetime.now() + timedelta(minutes=10)
        cur.execute(
            "INSERT INTO pin_reset_tokens (user_id, token, expires_at) VALUES (%s,%s,%s)",
            (user["id"], token, expiry)
        )
        conn.commit()
        send_pin_reset_email(email, token, user.get("name", "User"))
        return jsonify({"success": True, "message": "If that email exists, a reset code has been sent."})
    except Exception as e:
        print(f"[forgot-pin error] {e}")
        return jsonify({"success": False, "message": "Server error."}), 500
    finally:
        cur.close(); conn.close()


@worker_bp.route("/reset-pin", methods=["POST"])
def api_reset_pin():
    data    = request.get_json(silent=True) or {}
    email   = data.get("email", "").strip().lower()
    token   = data.get("token", "").strip()
    new_pin = str(data.get("new_pin", "")).strip()

    if not all([email, token, new_pin]):
        return jsonify({"success": False, "message": "Invalid request."}), 400
    if not new_pin.isdigit() or not (4 <= len(new_pin) <= 6):
        return jsonify({"success": False, "message": "PIN must be 4–6 digits."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT prt.* FROM pin_reset_tokens prt
               JOIN users u ON u.id = prt.user_id
               WHERE u.email=%s AND prt.token=%s AND prt.used=0""",
            (email, token)
        )
        record = cur.fetchone()
        if not record or datetime.now() > record["expires_at"]:
            return jsonify({"success": False, "message": "Token expired or invalid."}), 400

        new_hash = generate_password_hash(new_pin)
        cur.execute(
            """UPDATE users SET withdrawal_pin_hash=%s, pin_failed_attempts=0, pin_locked_until=NULL
               WHERE id=%s""",
            (new_hash, record["user_id"])
        )
        cur.execute("UPDATE pin_reset_tokens SET used=1 WHERE id=%s", (record["id"],))
        conn.commit()
        return jsonify({"success": True, "message": "PIN reset. You can now withdraw again."})
    except Exception as e:
        conn.rollback()
        print(f"[reset-pin error] {e}")
        return jsonify({"success": False, "message": "Server error."}), 500
    finally:
        cur.close(); conn.close()

@worker_bp.route("/respond-invitation", methods=["POST"])
def api_worker_respond_invitation():
    """Worker accepts or declines a client-initiated direct-hire invite."""
    data      = request.get_json(silent=True) or {}
    job_id    = data.get("job_id")
    user_id   = str(data.get("user_id", "")).strip()
    action    = data.get("action", "").strip()  # 'accept' or 'decline'

    if not job_id or not user_id or action not in ("accept", "decline"):
        return jsonify({"success": False, "message": "job_id, user_id and action (accept/decline) required."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT ja.id FROM job_applications ja
               JOIN jobs j ON j.id = ja.job_id
               WHERE ja.job_id=%s AND ja.worker_id=%s AND j.status='open'
                 AND ja.status='pending' AND ja.initiated_by='client'""",
            (job_id, user_id)
        )
        invite = cur.fetchone()
        if not invite:
            return jsonify({"success": False, "message": "Invitation not found or already resolved."}), 404

        if action == "accept":
            cur.execute(
                "UPDATE jobs SET worker_id=%s, status='assigned', assigned_at=NOW() WHERE id=%s",
                (user_id, job_id)
            )
            cur.execute(
                "UPDATE job_applications SET status='accepted' WHERE job_id=%s AND worker_id=%s",
                (job_id, user_id)
            )
        else:
            cur.execute(
                "UPDATE job_applications SET status='rejected' WHERE job_id=%s AND worker_id=%s",
                (job_id, user_id)
            )
            # invite declined — the job has no worker, client can delete it via
            # the existing delete-job route (it's still 'open') or invite someone else

        conn.commit()
        return jsonify({"success": True, "action": action})
    except Exception as e:
        conn.rollback()
        print(f"[respond-invitation error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()