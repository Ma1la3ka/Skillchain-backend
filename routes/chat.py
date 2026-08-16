"""Chat routes — job-scoped messaging between client and worker"""
from flask import Blueprint, request, jsonify
from database_helper import get_db

chat_bp = Blueprint('chat', __name__, url_prefix='/api/chat')


@chat_bp.route("/conversations")
def api_chat_conversations():
    """
    Get all conversation threads for this user — one row per
    (job_id, other_person) pair, most recent first.
    Returns job_title, other_user_name, last message, unread count.
    """
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT
                m.job_id,
                j.title                                             AS job_title,
                CASE WHEN m.sender_id = %s
                     THEN m.recipient_id
                     ELSE m.sender_id END                           AS other_user_id,
                u.name                                              AS other_user_name,
                u.profile_photo_path                                AS other_user_photo,
                u.role                                              AS other_user_role,
                MAX(m.created_at)                                   AS last_at,
                (SELECT COALESCE(m2.content, m2.body)
                 FROM messages m2
                 WHERE m2.job_id = m.job_id
                   AND (
                     (m2.sender_id = %s AND m2.recipient_id = u.id)
                  OR (m2.sender_id = u.id AND m2.recipient_id = %s)
                   )
                 ORDER BY m2.created_at DESC LIMIT 1)               AS last_message,
                (SELECT m3.sender_id
                 FROM messages m3
                 WHERE m3.job_id = m.job_id
                   AND (
                     (m3.sender_id = %s AND m3.recipient_id = u.id)
                  OR (m3.sender_id = u.id AND m3.recipient_id = %s)
                   )
                 ORDER BY m3.created_at DESC LIMIT 1)               AS last_sender_id,
                SUM(CASE WHEN m.recipient_id = %s
                         AND m.read_at IS NULL THEN 1 ELSE 0 END)   AS unread_count
            FROM messages m
            JOIN jobs  j ON j.id  = m.job_id
            JOIN users u ON u.id  = CASE WHEN m.sender_id = %s
                                         THEN m.recipient_id
                                         ELSE m.sender_id END
            WHERE m.sender_id = %s OR m.recipient_id = %s
            GROUP BY m.job_id,
                     other_user_id,
                     j.title,
                     u.name,
                     u.profile_photo_path,
                     u.role
            ORDER BY last_at DESC
        """, (
            user_id,
            user_id, user_id,
            user_id, user_id,
            user_id,
            user_id,
            user_id, user_id
        ))
        rows = cur.fetchall()

        seen    = set()
        threads = []
        for r in rows:
            key = (r["job_id"], r["other_user_id"])
            if key in seen:
                continue
            seen.add(key)
            threads.append(r)

        result = []
        for t in threads:
            result.append({
                "job_id":          t["job_id"],
                "job_title":       t["job_title"]       or "Job",
                "other_id":        t["other_user_id"],
                "other_name":      t["other_user_name"] or "User",
                "other_role":      t["other_user_role"] or "",
                "other_photo":     t["other_user_photo"],
                "last_message":    t["last_message"]    or "",
                "last_at":         str(t["last_at"])    if t["last_at"] else "",
                "last_from_me":    int(t["last_sender_id"] or 0) == int(user_id),
                "unread_count":    int(t["unread_count"] or 0),
            })

        return jsonify({"conversations": result})

    except Exception as e:
        print(f"[conversations error] {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@chat_bp.route("/thread")
def api_chat_thread():
    """
    Get all messages between two users for a specific job.
    Marks incoming messages as read automatically.
    """
    job_id   = request.args.get("job_id",   "").strip()
    user_id  = request.args.get("user_id",  "").strip()
    other_id = request.args.get("other_id", "").strip()

    if not all([job_id, user_id, other_id]):
        return jsonify({"error": "job_id, user_id and other_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT id, job_id, sender_id, recipient_id,
                      COALESCE(content, body) AS body,
                      created_at, read_at
               FROM messages
               WHERE job_id = %s
                 AND (
                   (sender_id = %s AND recipient_id = %s)
                OR (sender_id = %s AND recipient_id = %s)
                 )
               ORDER BY created_at ASC""",
            (job_id, user_id, other_id, other_id, user_id)
        )
        messages = cur.fetchall()
        for m in messages:
            m["created_at"] = str(m["created_at"])
            m["read_at"]    = str(m["read_at"]) if m["read_at"] else None

        cur.execute(
            """UPDATE messages
               SET read_at = NOW()
               WHERE job_id      = %s
                 AND sender_id   = %s
                 AND recipient_id= %s
                 AND read_at IS NULL""",
            (job_id, other_id, user_id)
        )
        conn.commit()

        return jsonify({"messages": messages})

    except Exception as e:
        print(f"[chat-thread error] {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@chat_bp.route("/send", methods=["POST"])
def api_chat_send():
    """Send a message tied to a job"""
    data         = request.get_json(silent=True) or {}
    job_id       = data.get("job_id")
    sender_id    = str(data.get("sender_id",    "")).strip()
    recipient_id = str(data.get("recipient_id", "")).strip()
    content      = data.get("content", "").strip()

    if not content:
        content = data.get("body", "").strip()

    if not all([job_id, sender_id, recipient_id, content]):
        return jsonify({
            "success": False,
            "message": "job_id, sender_id, recipient_id and content required."
        }), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """INSERT INTO messages
               (job_id, sender_id, recipient_id, body, content, created_at)
               VALUES (%s, %s, %s, %s, %s, NOW())""",
            (job_id, sender_id, recipient_id, content, content)
        )
        conn.commit()
        return jsonify({"success": True, "id": cur.lastrowid})

    except Exception as e:
        conn.rollback()
        print(f"[chat-send error] {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@chat_bp.route("/unread-count")
def api_chat_unread_count():
    """Total unread messages for this user across all jobs — for sidebar badge"""
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT COUNT(*) AS c
               FROM messages
               WHERE recipient_id = %s AND read_at IS NULL""",
            (user_id,)
        )
        count = cur.fetchone()["c"]
        return jsonify({"unread": int(count or 0)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()