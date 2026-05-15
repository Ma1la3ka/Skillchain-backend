"""Media and social features routes"""
from flask import Blueprint, request, jsonify
from database_helper import get_db
from utils import haversine_distance
import os
import uuid

media_bp = Blueprint('media', __name__, url_prefix='/api')


@media_bp.route("/job/upload-media", methods=["POST"])
def api_upload_media():
    """Upload proof media (photos/videos) for a job"""
    job_id = request.form.get("job_id")
    user_id = request.form.get("user_id", "").strip()
    proof_lat = request.form.get("proof_lat")
    proof_lng = request.form.get("proof_lng")
    files = request.files.getlist("files")

    if not all([job_id, user_id]):
        return jsonify({"success": False, "message": "job_id and user_id required."}), 400

    try:
        proof_lat = float(proof_lat) if proof_lat else None
        proof_lng = float(proof_lng) if proof_lng else None
    except (ValueError, TypeError):
        proof_lat = proof_lng = None

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

        # Geofence check
        within_fence = False
        distance_m = None
        if proof_lat and proof_lng and job["site_lat"] and job["site_lng"]:
            distance_m = haversine_distance(
                float(job["site_lat"]), float(job["site_lng"]),
                proof_lat, proof_lng
            )
            within_fence = distance_m <= 100

        saved = []
        os.makedirs("static/job_media", exist_ok=True)

        for f in files:
            if not f or not f.filename:
                continue

            ext = os.path.splitext(f.filename)[1].lower()

            if not ext:
                if f.content_type and "video" in f.content_type:
                    ext = ".webm"
                elif f.content_type and "image" in f.content_type:
                    ext = ".jpg"
                else:
                    ext = ".bin"

            fname = f"jm_{job_id}_{uuid.uuid4().hex[:8]}{ext}"
            path = os.path.join("static/job_media", fname)
            f.save(path)

            mtype = "video" if ext in (".mp4", ".webm", ".mov", ".avi") else "image"

            cur.execute(
                """INSERT INTO job_media
                   (job_id, uploader_id, media_type, file_path, proof_lat, proof_lng)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (job_id, user_id, mtype, path, proof_lat, proof_lng)
            )
            media_id = cur.lastrowid
            saved.append({"id": media_id, "path": path, "type": mtype})

        if proof_lat and proof_lng:
            cur.execute(
                "UPDATE jobs SET video_proof_lat = %s, video_proof_lng = %s WHERE id = %s",
                (proof_lat, proof_lng, job_id)
            )

        conn.commit()
        return jsonify({
            "success": True,
            "within_fence": within_fence,
            "distance_m": distance_m,
            "can_be_rated": within_fence,
            "media": saved
        })
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"[upload-media error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@media_bp.route("/job/media")
def api_job_media():
    """Get all media for a job"""
    job_id = request.args.get("job_id")
    user_id = request.args.get("user_id", "")
    if not job_id:
        return jsonify({"error": "job_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """SELECT m.*,
                  u.name AS uploader_name,
                  (SELECT COUNT(*) FROM media_likes  WHERE media_id = m.id) AS likes,
                  (SELECT COUNT(*) FROM media_comments WHERE media_id = m.id) AS comment_count,
                  (SELECT COUNT(*) FROM media_likes WHERE media_id = m.id AND user_id = %s) AS user_liked
           FROM job_media m
           JOIN users u ON u.id = m.uploader_id
           WHERE m.job_id = %s
           ORDER BY m.created_at ASC""",
        (user_id or 0, job_id)
    )
    media = cur.fetchall()
    cur.close()
    conn.close()
    for m in media:
        m["created_at"] = str(m["created_at"])
        m["proof_lat"] = float(m["proof_lat"]) if m["proof_lat"] else None
        m["proof_lng"] = float(m["proof_lng"]) if m["proof_lng"] else None
        m["user_liked"] = bool(m["user_liked"])
    return jsonify({"media": media})


@media_bp.route("/media/like", methods=["POST"])
def api_like_media():
    """Like/unlike media"""
    data = request.get_json(silent=True) or {}
    media_id = data.get("media_id")
    user_id = str(data.get("user_id", "")).strip()
    if not media_id or not user_id:
        return jsonify({"success": False}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM media_likes WHERE media_id=%s AND user_id=%s", (media_id, user_id))
        if cur.fetchone():
            cur.execute("DELETE FROM media_likes WHERE media_id=%s AND user_id=%s", (media_id, user_id))
            liked = False
        else:
            cur.execute("INSERT INTO media_likes (media_id, user_id) VALUES (%s, %s)", (media_id, user_id))
            liked = True
        conn.commit()
        cur.execute("SELECT COUNT(*) AS c FROM media_likes WHERE media_id=%s", (media_id,))
        count = cur.fetchone()["c"]
        return jsonify({"success": True, "liked": liked, "count": count})
    finally:
        cur.close()
        conn.close()


@media_bp.route("/job/like", methods=["POST"])
def api_like_job():
    """Like/unlike a job"""
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    user_id = str(data.get("user_id", "")).strip()
    if not job_id or not user_id:
        return jsonify({"success": False}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM job_likes WHERE job_id=%s AND user_id=%s", (job_id, user_id))
        if cur.fetchone():
            cur.execute("DELETE FROM job_likes WHERE job_id=%s AND user_id=%s", (job_id, user_id))
            liked = False
        else:
            cur.execute("INSERT INTO job_likes (job_id, user_id) VALUES (%s, %s)", (job_id, user_id))
            liked = True
        conn.commit()
        cur.execute("SELECT COUNT(*) AS c FROM job_likes WHERE job_id=%s", (job_id,))
        count = cur.fetchone()["c"]
        return jsonify({"success": True, "liked": liked, "count": count})
    finally:
        cur.close()
        conn.close()


@media_bp.route("/media/comments")
def api_media_comments():
    """Get comments on media"""
    media_id = request.args.get("media_id")
    if not media_id:
        return jsonify({"error": "media_id required"}), 400
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM media_comments WHERE media_id=%s ORDER BY created_at ASC",
        (media_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    for r in rows:
        r["created_at"] = str(r["created_at"])
    return jsonify({"comments": rows})


@media_bp.route("/media/comment", methods=["POST"])
def api_comment_media():
    """Add comment to media"""
    data = request.get_json(silent=True) or {}
    media_id = data.get("media_id")
    user_id = str(data.get("user_id", "")).strip()
    body = data.get("body", "").strip()
    name = data.get("user_name", "").strip()
    if not media_id or not user_id or not body:
        return jsonify({"success": False, "message": "media_id, user_id and body required."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "INSERT INTO media_comments (media_id, user_id, user_name, body) VALUES (%s,%s,%s,%s)",
            (media_id, user_id, name, body)
        )
        conn.commit()
        return jsonify({"success": True, "id": cur.lastrowid})
    finally:
        cur.close()
        conn.close()


@media_bp.route("/job/comments")
def api_job_comments():
    """Get comments on job"""
    job_id = request.args.get("job_id")
    if not job_id:
        return jsonify({"error": "job_id required"}), 400
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM job_comments WHERE job_id=%s ORDER BY created_at ASC",
        (job_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    for r in rows:
        r["created_at"] = str(r["created_at"])
    return jsonify({"comments": rows})


@media_bp.route("/job/comment", methods=["POST"])
def api_comment_job():
    """Add comment to job"""
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    user_id = str(data.get("user_id", "")).strip()
    body = data.get("body", "").strip()
    name = data.get("user_name", "").strip()
    if not job_id or not user_id or not body:
        return jsonify({"success": False, "message": "job_id, user_id and body required."}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "INSERT INTO job_comments (job_id, user_id, user_name, body) VALUES (%s,%s,%s,%s)",
            (job_id, user_id, name, body)
        )
        conn.commit()
        return jsonify({"success": True, "id": cur.lastrowid})
    finally:
        cur.close()
        conn.close()
