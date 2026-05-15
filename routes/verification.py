"""Job verification and payment routes"""
from flask import Blueprint, request, jsonify
from database_helper import get_db
from utils import haversine_distance, squad_payout
import os
import uuid

verification_bp = Blueprint('verification', __name__, url_prefix='/api')


@verification_bp.route("/verify-job", methods=["POST"])
def api_verify_job():
    """Verify job completion with GPS check"""
    job_id = request.form.get("job_id")
    worker_lat = request.form.get("worker_lat")
    worker_lng = request.form.get("worker_lng")
    user_id = request.form.get("user_id", "").strip()
    video = request.files.get("video")

    if not all([job_id, worker_lat, worker_lng, user_id]):
        return jsonify({"success": False, "message": "Missing required fields."}), 400

    try:
        worker_lat = float(worker_lat)
        worker_lng = float(worker_lng)
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid GPS coordinates."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    try:
        cur.execute(
            "SELECT * FROM jobs WHERE id = %s AND worker_id = %s",
            (job_id, user_id)
        )
        job = cur.fetchone()

        if not job:
            return jsonify({"success": False, "message": "Job not found or not assigned to you."}), 404

        if job["status"] not in ("assigned", "pending_verification"):
            return jsonify({"success": False,
                            "message": f"Job cannot be verified (status: {job['status']})."}), 400

        # Haversine check
        distance = haversine_distance(
            float(job["site_lat"]), float(job["site_lng"]),
            worker_lat, worker_lng
        )
        radius = 100  # metres
        verified = distance <= radius
        result = "pass" if verified else "fail"

        # Save video proof (optional)
        video_path = None
        if video:
            os.makedirs("static/videos", exist_ok=True)
            filename = f"proof_{job_id}_{uuid.uuid4().hex[:8]}.webm"
            video_path = os.path.join("static/videos", filename)
            video.save(video_path)

        # Mark job status
        cur.execute(
            """UPDATE jobs SET
               worker_lat       = %s,
               worker_lng       = %s,
               distance_meters  = %s,
               status           = %s,
               verified_at      = NOW(),
               video_proof_path = %s
               WHERE id = %s""",
            (worker_lat, worker_lng, distance,
             "verified" if verified else "pending_verification",
             video_path, job_id)
        )

        # Log verification attempt
        cur.execute(
            """INSERT INTO verification_logs
            (job_id, worker_id, passed, result, distance_meters,
            worker_lat, worker_lng, site_lat, site_lng)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (job_id, user_id, 1 if verified else 0, result, distance,
            worker_lat, worker_lng,
            float(job["site_lat"]), float(job["site_lng"]))
        )
        
        transfer_reference = None

        if verified:
            # Squad payout
            transfer_reference = squad_payout(job, user_id, cur)
            if transfer_reference:
                cur.execute(
                    "UPDATE jobs SET status = 'paid', paid_at = NOW(), transfer_reference = %s WHERE id = %s",
                    (transfer_reference, job_id)
                )
                # Increment worker trust score and jobs_completed
                cur.execute(
                    """UPDATE users
                       SET jobs_completed = jobs_completed + 1,
                           trust_score    = LEAST(5.0, trust_score + 0.1)
                       WHERE id = %s""",
                    (user_id,)
                )

        conn.commit()
        return jsonify({
            "verified": verified,
            "distance_meters": distance,
            "radius_meters": radius,
            "message": "Job verified and payment released!" if verified else "You are too far from the job site.",
            "transfer_reference": transfer_reference
        })

    except Exception as e:
        conn.rollback()
        print(f"[verify-job error] {e}")
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500
    finally:
        cur.close()
        conn.close()
