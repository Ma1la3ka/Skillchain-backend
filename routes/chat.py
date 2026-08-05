"""Chat routes — job-scoped messaging between client and worker"""
from flask import Blueprint, request, jsonify
from database_helper import get_db

chat_bp = Blueprint('chat', __name__, url_prefix='/api/chat')



@chat_bp.route("/conversations")
def api_chat_conversations():
    """
    Get all conversation threads for this user, WhatsApp-style —
    one row per (job, other person) pair, with the last message,
    timestamp, and unread count. Ordered by most recent activity.
    """
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        # Every message this user sent or received, tagged with who the
        # "other person" is in that row — then we collapse to one row
        # per (job_id, other_person) by picking the latest message.
        cur.execute(
            """
            SELECT
                m.job_id,
                IF(m.sender_id = %s, m.recipient_id, m.sender_id) AS other_id,
                m.body            AS last_body,
                m.created_at      AS last_at,
                m.sender_id       AS last_sender_id
            FROM messages m
            WHERE m.sender_id = %s OR m.recipient_id = %s
            ORDER BY m.created_at DESC
            """,
            (user_id, user_id, user_id)
        )
        rows = cur.fetchall()

        # Collapse to latest message per (job_id, other_id) in Python —
        # simpler and more portable than a correlated subquery, and this
        # table will be small per-user for a long time.
        seen = set()
        threads = []
        for r in rows:
            key = (r["job_id"], r["other_id"])
            if key in seen:
                continue
            seen.add(key)
            threads.append(r)

        if not threads:
            return jsonify({"conversations": []})

        # Enrich each thread with job title, other person's name/photo/role,
        # and unread count for that specific thread.
        result = []
        for t in threads:
            cur.execute(
                "SELECT title FROM jobs WHERE id = %s",
                (t["job_id"],)
            )
            job = cur.fetchone()

            cur.execute(
                "SELECT id, name, role, profile_photo_path FROM users WHERE id = %s",
                (t["other_id"],)
            )
            other = cur.fetchone()
            if not other:
                continue  # other user deleted or bad data — skip

            cur.execute(
                """SELECT COUNT(*) AS c FROM messages
                   WHERE job_id=%s AND sender_id=%s AND recipient_id=%s AND read_at IS NULL""",
                (t["job_id"], t["other_id"], user_id)
            )
            unread = cur.fetchone()["c"]

            result.append({
                "job_id":             t["job_id"],
                "job_title":          job["title"] if job else "Job",
                "other_id":           other["id"],
                "other_name":         other["name"],
                "other_role":         other["role"],
                "other_photo":        other["profile_photo_path"],
                "last_message":       t["last_body"],
                "last_at":            str(t["last_at"]),
                "last_from_me":       t["last_sender_id"] == int(user_id),
                "unread_count":       unread,
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
    """Get all messages between two users for a specific job. Marks incoming messages as read."""
    job_id     = request.args.get("job_id", "").strip()
    user_id    = request.args.get("user_id", "").strip()
    other_id   = request.args.get("other_id", "").strip()

    if not all([job_id, user_id, other_id]):
        return jsonify({"error": "job_id, user_id and other_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT * FROM messages
               WHERE job_id=%s
                 AND ((sender_id=%s AND recipient_id=%s) OR (sender_id=%s AND recipient_id=%s))
               ORDER BY created_at ASC""",
            (job_id, user_id, other_id, other_id, user_id)
        )
        messages = cur.fetchall()
        for m in messages:
            m["created_at"] = str(m["created_at"])
            m["read_at"]    = str(m["read_at"]) if m["read_at"] else None

        # Mark messages sent TO this user as read
        cur.execute(
            "UPDATE messages SET read_at=NOW() WHERE job_id=%s AND sender_id=%s AND recipient_id=%s AND read_at IS NULL",
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
    sender_id    = str(data.get("sender_id", "")).strip()
    recipient_id = str(data.get("recipient_id", "")).strip()
    body         = data.get("body", "").strip()

    if not all([job_id, sender_id, recipient_id, body]):
        return jsonify({"success": False, "message": "job_id, sender_id, recipient_id and body required."}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "INSERT INTO messages (job_id, sender_id, recipient_id, body) VALUES (%s,%s,%s,%s)",
            (job_id, sender_id, recipient_id, body)
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
    """Total unread messages for this user, across all jobs — for a sidebar badge"""
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE recipient_id=%s AND read_at IS NULL",
            (user_id,)
        )
        count = cur.fetchone()["c"]
        return jsonify({"unread": count})
    finally:
        cur.close()
        conn.close()