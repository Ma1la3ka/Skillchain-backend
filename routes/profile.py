"""Worker and job profile routes"""
from flask import Blueprint, request, jsonify
from database_helper import get_db
from utils import haversine_distance

profile_bp = Blueprint('profile', __name__, url_prefix='/api')


@profile_bp.route("/worker/public-profile")
def api_worker_public_profile():
    """Get public profile of a worker"""
    worker_id = request.args.get("worker_id", "").strip()
    viewer_id = request.args.get("viewer_id", "0").strip()
    if not worker_id:
        return jsonify({"error": "worker_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    cur.execute(
        """SELECT id, name, trade, trust_score, jobs_completed,
                  squad_bank_name
           FROM users WHERE id = %s AND role = 'worker'""",
        (worker_id,)
    )
    worker = cur.fetchone()
    if not worker:
        cur.close()
        conn.close()
        return jsonify({"error": "Worker not found"}), 404
    worker["trust_score"] = float(worker["trust_score"] or 0)

    cur.execute(
        """SELECT j.id, j.title, j.trade, j.site_address, j.amount,
                  j.status, j.verified_at, j.distance_meters,
                  j.client_rating, j.client_rating_comment,
                  c.name AS client_name
           FROM jobs j
           LEFT JOIN users c ON c.id = j.client_id
           WHERE j.worker_id = %s AND j.status IN ('verified','paid')
           ORDER BY j.verified_at DESC LIMIT 30""",
        (worker_id,)
    )
    history = cur.fetchall()
    for h in history:
        h["amount"] = float(h["amount"] or 0)
        h["distance_meters"] = float(h["distance_meters"]) if h["distance_meters"] else None
        h["verified_at"] = str(h["verified_at"]) if h["verified_at"] else None

    cur.execute(
        """SELECT m.*,
                  j.title AS job_title,
                  (SELECT COUNT(*) FROM media_likes WHERE media_id = m.id) AS likes,
                  (SELECT COUNT(*) FROM media_comments WHERE media_id = m.id) AS comment_count,
                  (SELECT COUNT(*) FROM media_likes WHERE media_id = m.id AND user_id = %s) AS viewer_liked
           FROM job_media m
           JOIN jobs j ON j.id = m.job_id
           WHERE m.uploader_id = %s
           ORDER BY m.created_at DESC LIMIT 20""",
        (viewer_id, worker_id)
    )
    media = cur.fetchall()
    for m in media:
        m["created_at"] = str(m["created_at"])
        m["viewer_liked"] = bool(m["viewer_liked"])

    cur.execute(
        """SELECT COUNT(*) AS total_ratings, AVG(client_rating) AS avg_rating,
                  SUM(client_rating=5) AS five_star,
                  SUM(client_rating=4) AS four_star,
                  SUM(client_rating=3) AS three_star,
                  SUM(client_rating<=2) AS low_star
           FROM jobs WHERE worker_id = %s AND client_rating IS NOT NULL""",
        (worker_id,)
    )
    rating_summary = cur.fetchone()
    rating_summary["avg_rating"] = float(rating_summary["avg_rating"] or 0)

    cur.close()
    conn.close()

    return jsonify({
        "worker": worker,
        "job_history": history,
        "media": media,
        "rating_summary": rating_summary
    })


@profile_bp.route("/workers/search")
def api_workers_search():
    """Search for workers by name, trade, and location"""
    q = request.args.get("q", "").strip()
    trade = request.args.get("trade", "").strip()
    lat = request.args.get("lat", "").strip()
    lng = request.args.get("lng", "").strip()
    radius_km = float(request.args.get("radius_km", "10"))

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    sql = """
        SELECT u.id, u.name, u.trade, u.trust_score, u.jobs_completed, u.phone,
               AVG(j.site_lat) AS avg_lat,
               AVG(j.site_lng) AS avg_lng,
               COUNT(j.id)     AS job_count_in_area
        FROM users u
        LEFT JOIN jobs j ON j.worker_id = u.id
             AND j.status IN ('verified','paid')
             AND j.site_lat IS NOT NULL
        WHERE u.role = 'worker'
    """
    params = []

    if trade:
        sql += " AND u.trade = %s"
        params.append(trade)
    if q:
        sql += " AND (u.name LIKE %s OR u.trade LIKE %s)"
        params += [f"%{q}%", f"%{q}%"]

    sql += " GROUP BY u.id ORDER BY u.trust_score DESC, u.jobs_completed DESC LIMIT 100"

    cur.execute(sql, params)
    workers = cur.fetchall()
    cur.close()
    conn.close()

    result = []
    for w in workers:
        w["trust_score"] = float(w["trust_score"] or 0)
        w["avg_lat"] = float(w["avg_lat"]) if w["avg_lat"] else None
        w["avg_lng"] = float(w["avg_lng"]) if w["avg_lng"] else None
        w["distance_km"] = None

        if lat and lng and w["avg_lat"] and w["avg_lng"]:
            try:
                clat, clng = float(lat), float(lng)
                d = haversine_distance(clat, clng, w["avg_lat"], w["avg_lng"]) / 1000
                w["distance_km"] = round(d, 1)
                if d > radius_km:
                    continue
            except:
                pass

        result.append(w)

    if lat and lng:
        result.sort(key=lambda w: w["distance_km"] if w["distance_km"] is not None else 9999)

    return jsonify({"workers": result[:30]})
