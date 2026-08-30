"""Worker and job profile routes"""
from flask import Blueprint, request, jsonify
from database_helper import get_db
from utils import haversine_distance

profile_bp = Blueprint('profile', __name__, url_prefix='/api')



@profile_bp.route("/client/public-profile")
def api_client_public_profile():
    """Get public profile of a client — shown when a worker clicks their name in chat"""
    client_id = request.args.get("client_id", "").strip()
    if not client_id:
        return jsonify({"error": "client_id required"}), 400

    conn = get_db()
    cur = conn.cursor(dictionary=True)

    cur.execute(
        """SELECT id, name, profile_photo_path, created_at
           FROM users WHERE id = %s""",
        (client_id,)
    )
    client = cur.fetchone()
    if not client:
        cur.close()
        conn.close()
        return jsonify({"error": "Client not found"}), 404
    client["created_at"] = str(client["created_at"]) if client["created_at"] else None

    cur.execute(
        """SELECT
             COUNT(*) AS total_jobs,
             SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS completed_jobs
           FROM jobs WHERE client_id = %s""",
        (client_id,)
    )
    stats = cur.fetchone()
    client["total_jobs"]     = int(stats["total_jobs"] or 0)
    client["completed_jobs"] = int(stats["completed_jobs"] or 0)

    cur.close()
    conn.close()

    return jsonify({"client": client})



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
    SELECT u.id, u.name, u.trade, u.trust_score, 
            COALESCE(jc.cnt, 0) AS jobs_completed,
            u.phone,
            AVG(j.site_lat) AS avg_lat,
            AVG(j.site_lng) AS avg_lng,
            COUNT(j.id)     AS job_count_in_area
    FROM users u
    LEFT JOIN (
        SELECT worker_id, COUNT(*) AS cnt 
        FROM jobs 
        WHERE status IN ('verified','paid') 
        GROUP BY worker_id
    ) jc ON jc.worker_id = u.id
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
