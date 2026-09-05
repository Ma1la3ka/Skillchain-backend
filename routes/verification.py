"""Job verification and payment routes"""
from flask import Blueprint, request, jsonify
from database_helper import get_db
from utils import haversine_distance
import os
import uuid

verification_bp = Blueprint('verification', __name__, url_prefix='/api')


@verification_bp.route("/verify-job", methods=["POST"])
def api_verify_job():
    """Verify job completion with GPS check"""
    job_id        = request.form.get("job_id")
    job_worker_id = request.form.get("job_worker_id")
    worker_lat    = request.form.get("worker_lat")
    worker_lng    = request.form.get("worker_lng")
    user_id       = request.form.get("user_id", "").strip()
    video         = request.files.get("video")

    if not worker_lat or not worker_lng or not user_id or not (job_id or job_worker_id):
        return jsonify({"success": False, "message": "Missing required fields."}), 400

    try:
        worker_lat = float(worker_lat)
        worker_lng = float(worker_lng)
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid GPS coordinates."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    try:
        if job_worker_id:
            cur.execute(
                """SELECT jw.*, j.site_lat, j.site_lng
                   FROM job_workers jw JOIN jobs j ON j.id = jw.job_id
                   WHERE jw.id = %s AND jw.worker_id = %s""",
                (job_worker_id, user_id)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"success": False, "message": "Slot not found or not assigned to you."}), 404
            if row["status"] not in ("assigned", "pending_verification"):
                return jsonify({"success": False,
                                "message": f"Slot cannot be verified (status: {row['status']})."}), 400

            if row.get("work_location_type") == "worker_shop":
                cur.execute("SELECT shop_lat, shop_lng FROM users WHERE id = %s", (user_id,))
                wu = cur.fetchone()
                ref_lat = float(wu["shop_lat"]) if wu and wu["shop_lat"] else float(row["site_lat"])
                ref_lng = float(wu["shop_lng"]) if wu and wu["shop_lng"] else float(row["site_lng"])
            else:
                ref_lat = float(row["site_lat"])
                ref_lng = float(row["site_lng"])

            distance = haversine_distance(ref_lat, ref_lng, float(worker_lat), float(worker_lng))
            radius = 150
            in_range = distance <= radius
            result = "pass" if in_range else "fail"

            video_path = None
            if video:
                os.makedirs("static/videos", exist_ok=True)
                filename = f"proof_jw{job_worker_id}_{uuid.uuid4().hex[:8]}.webm"
                video_path = os.path.join("static/videos", filename)
                video.save(video_path)

            new_status = "verified" if in_range else "pending_verification"
            if in_range:
                cur.execute(
                    """UPDATE job_workers SET
                       distance_meters=%s, status=%s, verified_at=NOW()
                       WHERE id=%s""",
                    (distance, new_status, job_worker_id)
                )
            else:
                cur.execute(
                    """UPDATE job_workers SET
                       distance_meters=%s, status=%s, verified_at=NOW()
                       WHERE id=%s""",
                    (distance, new_status, job_worker_id)
                )

            cur.execute(
                """INSERT INTO verification_logs
                   (job_id, worker_id, passed, result, distance_meters,
                    worker_lat, worker_lng, site_lat, site_lng)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (row["job_id"], user_id, 1 if in_range else 0, result, distance,
                 worker_lat, worker_lng, ref_lat, ref_lng)
            )

            conn.commit()
            return jsonify({
                "in_range": in_range, "distance_meters": distance, "radius_meters": radius,
                "status": new_status,
                "message": (
                    "Submitted! Waiting for client to review and release your payment."
                    if in_range else "You are too far from the reference location."
                )
            })

        # ── Single-worker path (skilled jobs / single-slot gigs) — unchanged ──
        cur.execute("SELECT * FROM jobs WHERE id = %s AND worker_id = %s", (job_id, user_id))
        job = cur.fetchone()
        if not job:
            return jsonify({"success": False, "message": "Job not found or not assigned to you."}), 404
        if job["status"] not in ("assigned", "pending_verification"):
            return jsonify({"success": False,
                            "message": f"Job cannot be verified (status: {job['status']})."}), 400

        distance = haversine_distance(
            float(job["site_lat"]), float(job["site_lng"]),
            float(worker_lat), float(worker_lng)
        )
        radius = 150
        in_range = distance <= radius
        result = "pass" if in_range else "fail"

        video_path = None
        if video:
            os.makedirs("static/videos", exist_ok=True)
            filename = f"proof_{job_id}_{uuid.uuid4().hex[:8]}.webm"
            video_path = os.path.join("static/videos", filename)
            video.save(video_path)

        new_status = "verified" if in_range else "pending_verification"
        if in_range:
            cur.execute(
                """UPDATE jobs SET
                   worker_lat=%s, worker_lng=%s, distance_meters=%s, status=%s,
                   verified_at=NOW(), review_deadline=NOW() + INTERVAL 24 HOUR,
                   video_proof_path=%s WHERE id=%s""",
                (worker_lat, worker_lng, distance, new_status, video_path, job_id)
            )
        else:
            cur.execute(
                """UPDATE jobs SET
                   worker_lat=%s, worker_lng=%s, distance_meters=%s, status=%s,
                   verified_at=NOW(), video_proof_path=%s WHERE id=%s""",
                (worker_lat, worker_lng, distance, new_status, video_path, job_id)
            )

        cur.execute(
            """INSERT INTO verification_logs
               (job_id, worker_id, passed, result, distance_meters,
                worker_lat, worker_lng, site_lat, site_lng)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (job_id, user_id, 1 if in_range else 0, result, distance,
             worker_lat, worker_lng, float(job["site_lat"]), float(job["site_lng"]))
        )

        conn.commit()
        return jsonify({
            "in_range": in_range, "distance_meters": distance, "radius_meters": radius,
            "status": new_status,
            "message": (
                "Submitted! Waiting for client to review and release payment "
                "(auto-released after 24h if no response)."
                if in_range else "You are too far from the job site."
            )
        })

    except Exception as e:
        conn.rollback()
        print(f"[verify-job error] {e}")
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500
    finally:
        cur.close()
        conn.close()