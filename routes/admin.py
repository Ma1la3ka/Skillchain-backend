"""Admin API: disputes, refunds, users, jobs, payments. Every route requires a login token AND a row in `admins`.
Money decisions also require the admin's password again and are written to admin_audit."""
import os
from datetime import datetime
from decimal import Decimal
from functools import wraps
from flask import Blueprint, request, jsonify, g
from werkzeug.security import check_password_hash
from database_helper import get_db
import cases_logic as cl
import push
import security
from utils import money

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')


def _ser(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, datetime):
        return o.strftime("%Y-%m-%d %H:%M")
    if isinstance(o, dict):
        return {k: _ser(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_ser(v) for v in o]
    return o


def _db(work, write=False):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        out = work(cur)
        if write:
            conn.commit()
        return jsonify(_ser(out))
    except cl.CaseError as e:
        conn.rollback()
        return jsonify({"success": False, "message": e.message}), e.status
    except Exception as e:
        conn.rollback()
        print(f"[admin error] {e}")
        return jsonify({"success": False, "message": "Server error."}), 500
    finally:
        cur.close()
        conn.close()


def _owner_emails():
    """Owners are set in Render (ADMIN_EMAILS=a@x.com,b@y.com). Owners can add and remove other admins."""
    return {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}


def _level(cur, uid):
    """None, 'admin' or 'owner'. Anyone whose VERIFIED login email is listed counts as admin."""
    cur.execute("""SELECT LOWER(u.email) AS email,
                          (EXISTS(SELECT 1 FROM admins a WHERE a.user_id = u.id)
                           OR EXISTS(SELECT 1 FROM admin_emails e WHERE e.email = LOWER(u.email))) AS listed
                   FROM users u WHERE u.id = %s""", (uid,))
    row = cur.fetchone()
    if not row:
        return None
    if row["email"] in _owner_emails():
        return "owner"
    return "admin" if row["listed"] else None


def _guard(owner):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            conn = get_db()
            cur = conn.cursor(dictionary=True)
            try:
                lvl = _level(cur, g.uid)
            finally:
                cur.close()
                conn.close()
            if not lvl or (owner and lvl != "owner"):
                return jsonify({"success": False, "error": "Owners only." if lvl else "Admins only."}), 403
            g.admin_level = lvl
            return fn(*a, **k)
        return wrapper
    return deco


admin_only = _guard(False)
owner_only = _guard(True)


def _check_password(cur):
    pw = str((request.get_json(silent=True) or {}).get("password", ""))
    cur.execute("SELECT password_hash FROM users WHERE id = %s", (g.uid,))
    row = cur.fetchone()
    if not pw or not row or not check_password_hash(row["password_hash"], pw):
        raise cl.CaseError("Wrong admin password.", 403)


def _audit(cur, action, target, detail):
    cur.execute("INSERT INTO admin_audit (admin_id, action, target, detail) VALUES (%s,%s,%s,%s)", (g.uid, action, str(target)[:60], str(detail)[:1000]))


def _naira(x):
    return "₦{:,.0f}".format(float(x or 0))


@admin_bp.route("/me")
@admin_only
def me():
    return jsonify({"success": True, "admin": True, "owner": g.admin_level == "owner"})


@admin_bp.route("/overview")
@admin_only
def overview():
    def work(cur):
        cur.execute("""SELECT
          (SELECT COUNT(*) FROM job_cases WHERE status='open' AND kind IN ('dispute','late')) AS open_disputes,
          (SELECT COUNT(*) FROM job_cases WHERE status='open' AND kind='fix') AS open_fixes,
          (SELECT COUNT(*) FROM refunds WHERE status='pending') AS pending_refunds,
          (SELECT COALESCE(SUM(amount),0) FROM refunds WHERE status='pending') AS pending_refund_amount,
          (SELECT COUNT(*) FROM payouts WHERE status IN ('processing','needs_review')) AS payouts_attention,
          (SELECT COUNT(*) FROM users) AS users,
          (SELECT COUNT(*) FROM jobs) AS jobs,
          (SELECT COALESCE(SUM(client_pays),0) FROM jobs WHERE escrow_paid=1 AND status IN ('assigned','pending_verification','verified','disputed')) AS escrow_held,
          (SELECT COALESCE(SUM(escrow_balance),0) FROM users) AS wallets_total""")
        return cur.fetchone()
    return _db(work)


@admin_bp.route("/cases")
@admin_only
def cases():
    status = request.args.get("status", "open")
    kinds = request.args.get("kind", "dispute,late").split(",")
    kinds = [k for k in kinds if k in ("dispute", "late", "fix")] or ["dispute", "late"]

    def work(cur):
        sql = """SELECT c.id, c.kind, c.status, c.outcome, c.reason, c.opener_role, c.created_at,
                        TIMESTAMPDIFF(HOUR, c.created_at, NOW()) AS age_h, j.id AS job_id, j.title, j.amount, j.client_pays,
                        cu.name AS client_name, wu.name AS worker_name
                 FROM job_cases c JOIN jobs j ON j.id = c.job_id
                 LEFT JOIN users cu ON cu.id = j.client_id LEFT JOIN users wu ON wu.id = j.worker_id
                 WHERE c.kind IN (%s)""" % ",".join(["%s"] * len(kinds))
        params = list(kinds)
        if status != "all":
            sql += " AND c.status = %s"
            params.append(status)
        cur.execute(sql + " ORDER BY c.id DESC LIMIT 100", params)
        return {"cases": cur.fetchall()}
    return _db(work)


@admin_bp.route("/case")
@admin_only
def case_detail():
    cid = request.args.get("id", type=int)

    def work(cur):
        cur.execute("SELECT * FROM job_cases WHERE id = %s", (cid,))
        case = cur.fetchone()
        if not case:
            raise cl.CaseError("Case not found.", 404)
        jid = case["job_id"]
        cur.execute("""SELECT id, title, description, status, amount, client_pays, artisan_gets, platform_fee, escrow_paid, paid_at,
                              distance_meters, site_address, review_deadline, created_at, verified_at, client_id, worker_id
                       FROM jobs WHERE id = %s""", (jid,))
        job = cur.fetchone()
        cur.execute("SELECT id, name, email, phone, trust_score, jobs_completed, escrow_balance FROM users WHERE id IN (%s, %s)",
                    (job["client_id"], job["worker_id"] or 0))
        people = {u["id"]: u for u in cur.fetchall()}
        cur.execute("SELECT id, role, note, media_url, media_type, created_at FROM case_evidence WHERE case_id = %s ORDER BY id", (cid,))
        evidence = cur.fetchall()
        cur.execute("SELECT media_type, file_path, proof_lat, proof_lng, uploader_id, created_at FROM job_media WHERE job_id = %s ORDER BY id", (jid,))
        media = cur.fetchall()
        cur.execute("SELECT passed, result, distance_meters, created_at FROM verification_logs WHERE job_id = %s ORDER BY id", (jid,))
        logs = cur.fetchall()
        cur.execute("SELECT sender_id, COALESCE(content, body) AS text, created_at FROM messages WHERE job_id = %s ORDER BY created_at LIMIT 200", (jid,))
        chat = cur.fetchall()
        cur.execute("SELECT id, kind, status, round_no, reason, outcome, created_at FROM job_cases WHERE job_id = %s ORDER BY id", (jid,))
        history = cur.fetchall()

        def prior(col):
            cur.execute(f"""SELECT COUNT(*) AS total FROM job_cases c JOIN jobs j ON j.id = c.job_id
                            WHERE j.{col} = %s AND c.kind IN ('dispute','late') AND c.id <> %s""", (job[col], cid))
            return int(cur.fetchone()["total"])
        return {"case": case, "job": job, "client": people.get(job["client_id"]), "worker": people.get(job["worker_id"]),
                "evidence": evidence, "media": media, "gps_logs": logs, "chat": chat, "job_cases": history,
                "client_prior_cases": prior("client_id"), "worker_prior_cases": prior("worker_id") if job["worker_id"] else 0}
    return _db(work)


@admin_bp.route("/resolve", methods=["POST"])
@admin_only
def resolve():
    d = request.get_json(silent=True) or {}
    holder = {}

    def work(cur):
        _check_password(cur)
        r = cl.resolve_case(cur, d.get("case_id"), g.uid, d.get("worker_amount") or 0, d.get("client_refund") or 0, d.get("note"))
        _audit(cur, "resolve_case", d.get("case_id"), f"{r['outcome']} worker={r['worker_amount']} refund={r['client_refund']} note={d.get('note')}")
        holder.update(r)
        return {"success": True, "outcome": r["outcome"], "worker_amount": r["worker_amount"], "client_refund": r["client_refund"]}
    resp = _db(work, write=True)
    if holder:
        job, W, R = holder["job"], holder["worker_amount"], holder["client_refund"]
        wmsg = f"{_naira(W)} was added to your wallet." if W > 0 else ("A refund of %s was taken from your wallet." % _naira(R) if holder["case"]["kind"] == "late" and R > 0 else "No payment was made for this job.")
        cmsg = f"A refund of {_naira(R)} is being processed." if R > 0 else "The payment stays with the worker."
        note = str(d.get("note") or "")[:120]
        for uid, role, msg in ((job["worker_id"], "worker", wmsg), (job["client_id"], "client", cmsg)):
            if uid:
                push.spawn(push.send_many, [uid], "Dispute settled", f"“{job['title']}”: {msg} Reason: {note}", {"role": role, "type": "case", "job_id": job["id"]}, None)
    return resp


@admin_bp.route("/refunds")
@admin_only
def refunds():
    status = request.args.get("status", "pending")

    def work(cur):
        sql = """SELECT r.id, r.case_id, r.job_id, r.amount, r.status, r.squad_reference, r.created_at, r.done_at, u.name AS client_name, u.email AS client_email, j.title
                 FROM refunds r JOIN users u ON u.id = r.client_id JOIN jobs j ON j.id = r.job_id"""
        params = []
        if status != "all":
            sql += " WHERE r.status = %s"
            params.append(status)
        cur.execute(sql + " ORDER BY r.id DESC LIMIT 100", params)
        return {"refunds": cur.fetchall()}
    return _db(work)


@admin_bp.route("/refund/done", methods=["POST"])
@admin_only
def refund_done():
    d = request.get_json(silent=True) or {}
    holder = {}

    def work(cur):
        _check_password(cur)
        ref = str(d.get("reference", "")).strip()[:100]
        if len(ref) < 3:
            raise cl.CaseError("Enter the Squad refund reference.")
        cur.execute("UPDATE refunds SET status='done', squad_reference=%s, done_at=NOW(), done_by=%s WHERE id=%s AND status='pending'", (ref, g.uid, d.get("refund_id")))
        if cur.rowcount != 1:
            raise cl.CaseError("This refund is not pending.", 409)
        cur.execute("SELECT client_id, amount, job_id FROM refunds WHERE id = %s", (d.get("refund_id"),))
        holder.update(cur.fetchone())
        _audit(cur, "refund_done", d.get("refund_id"), ref)
        return {"success": True}
    resp = _db(work, write=True)
    if holder:
        push.spawn(push.send_many, [holder["client_id"]], "Refund sent", f"{_naira(holder['amount'])} has been refunded. It may take a few days to show.",
                   {"role": "client", "type": "case", "job_id": holder["job_id"]}, None)
    return resp


@admin_bp.route("/users")
@admin_only
def users():
    q = request.args.get("q", "").strip()
    role = request.args.get("role", "")
    limit = min(request.args.get("limit", 50, type=int), 100)
    offset = max(request.args.get("offset", 0, type=int), 0)

    def work(cur):
        sql = """SELECT u.id, u.name, u.email, u.phone, u.role, u.trade, u.trust_score, u.jobs_completed, u.escrow_balance, u.created_at,
                        COALESCE(f.suspended, 0) AS suspended,
                        (SELECT COUNT(*) FROM job_cases c JOIN jobs j ON j.id = c.job_id
                          WHERE c.kind IN ('dispute','late') AND (j.client_id = u.id OR j.worker_id = u.id)) AS cases
                 FROM users u LEFT JOIN user_flags f ON f.user_id = u.id WHERE 1=1"""
        params = []
        if q:
            sql += " AND (u.name LIKE %s OR u.email LIKE %s OR u.phone LIKE %s)"
            params += [f"%{q}%"] * 3
        if role in ("worker", "client"):
            sql += " AND u.role = %s"
            params.append(role)
        cur.execute(sql + " ORDER BY u.id DESC LIMIT %s OFFSET %s", params + [limit, offset])
        return {"users": cur.fetchall()}
    return _db(work)


@admin_bp.route("/user")
@admin_only
def user_detail():
    uid = request.args.get("id", type=int)

    def work(cur):
        cur.execute("""SELECT u.id, u.name, u.email, u.phone, u.role, u.trade, u.trust_score, u.jobs_completed, u.escrow_balance, u.created_at,
                              COALESCE(f.suspended,0) AS suspended, f.reason AS suspended_reason
                       FROM users u LEFT JOIN user_flags f ON f.user_id = u.id WHERE u.id = %s""", (uid,))
        user = cur.fetchone()
        if not user:
            raise cl.CaseError("User not found.", 404)
        cols = "id, title, status, amount, escrow_paid, created_at"
        cur.execute(f"SELECT {cols} FROM jobs WHERE client_id = %s ORDER BY id DESC LIMIT 30", (uid,))
        as_client = cur.fetchall()
        cur.execute(f"SELECT {cols} FROM jobs WHERE worker_id = %s ORDER BY id DESC LIMIT 30", (uid,))
        as_worker = cur.fetchall()
        cur.execute("""SELECT c.id, c.kind, c.status, c.outcome, c.created_at, j.title FROM job_cases c JOIN jobs j ON j.id = c.job_id
                       WHERE j.client_id = %s OR j.worker_id = %s ORDER BY c.id DESC LIMIT 30""", (uid, uid))
        cases_ = cur.fetchall()
        cur.execute("SELECT id, amount, status, bank_name, created_at FROM payouts WHERE user_id = %s ORDER BY id DESC LIMIT 20", (uid,))
        payouts = cur.fetchall()
        return {"user": user, "as_client": as_client, "as_worker": as_worker, "cases": cases_, "payouts": payouts}
    return _db(work)


@admin_bp.route("/suspend", methods=["POST"])
@admin_only
def suspend():
    d = request.get_json(silent=True) or {}

    def work(cur):
        _check_password(cur)
        uid = int(d.get("user_id") or 0)
        if _level(cur, uid):
            raise cl.CaseError("Admins cannot be suspended here.", 409)
        flag = 1 if d.get("suspend") else 0
        reason = str(d.get("reason", ""))[:300]
        cur.execute("""INSERT INTO user_flags (user_id, suspended, reason) VALUES (%s,%s,%s)
                       ON DUPLICATE KEY UPDATE suspended = VALUES(suspended), reason = VALUES(reason)""", (uid, flag, reason))
        _audit(cur, "suspend" if flag else "unsuspend", uid, reason)
        return {"success": True}
    resp = _db(work, write=True)
    security.refresh_suspended()
    return resp


@admin_bp.route("/jobs")
@admin_only
def jobs():
    status = request.args.get("status", "")
    q = request.args.get("q", "").strip()
    limit = min(request.args.get("limit", 50, type=int), 100)
    offset = max(request.args.get("offset", 0, type=int), 0)

    def work(cur):
        sql = """SELECT j.id, j.title, j.status, j.amount, j.client_pays, j.escrow_paid, j.created_at, j.distance_meters,
                        cu.name AS client_name, wu.name AS worker_name,
                        (SELECT kind FROM job_cases c WHERE c.job_id = j.id AND c.status = 'open' ORDER BY c.id DESC LIMIT 1) AS open_case
                 FROM jobs j LEFT JOIN users cu ON cu.id = j.client_id LEFT JOIN users wu ON wu.id = j.worker_id WHERE 1=1"""
        params = []
        if status:
            sql += " AND j.status = %s"
            params.append(status)
        if q:
            sql += " AND (j.title LIKE %s OR cu.name LIKE %s OR wu.name LIKE %s)"
            params += [f"%{q}%"] * 3
        cur.execute(sql + " ORDER BY j.id DESC LIMIT %s OFFSET %s", params + [limit, offset])
        return {"jobs": cur.fetchall()}
    return _db(work)


@admin_bp.route("/payments")
@admin_only
def payments():
    def work(cur):
        cur.execute("""SELECT p.id, p.amount, p.status, p.bank_name, p.account_name, p.reference, p.provider_message, p.created_at, u.name
                       FROM payouts p JOIN users u ON u.id = p.user_id ORDER BY (p.status IN ('processing','needs_review')) DESC, p.id DESC LIMIT 60""")
        payouts = cur.fetchall()
        cur.execute("""SELECT l.id, l.direction, l.amount, l.reference, l.note, l.created_at, u.name
                       FROM wallet_ledger l JOIN users u ON u.id = l.user_id ORDER BY l.id DESC LIMIT 60""")
        return {"payouts": payouts, "ledger": cur.fetchall()}
    return _db(work)


@admin_bp.route("/admins")
@admin_only
def admins_list():
    def work(cur):
        cur.execute("""SELECT e.email, e.created_at, u.id AS user_id, u.name FROM admin_emails e
                       LEFT JOIN users u ON LOWER(u.email) = e.email ORDER BY e.created_at""")
        return {"owners": sorted(_owner_emails()), "admins": cur.fetchall(), "you_are_owner": g.admin_level == "owner"}
    return _db(work)


def _clean_email(raw):
    email = str(raw or "").strip().lower()
    if "@" not in email or len(email) > 190 or " " in email:
        raise cl.CaseError("Enter a valid email address.")
    return email


@admin_bp.route("/admins/add", methods=["POST"])
@owner_only
def admins_add():
    d = request.get_json(silent=True) or {}

    def work(cur):
        _check_password(cur)
        email = _clean_email(d.get("email"))
        cur.execute("INSERT INTO admin_emails (email, added_by) VALUES (%s, %s) ON DUPLICATE KEY UPDATE added_by = VALUES(added_by)", (email, g.uid))
        _audit(cur, "admin_add", email, "")
        return {"success": True}
    return _db(work, write=True)


@admin_bp.route("/admins/remove", methods=["POST"])
@owner_only
def admins_remove():
    d = request.get_json(silent=True) or {}

    def work(cur):
        _check_password(cur)
        email = _clean_email(d.get("email"))
        if email in _owner_emails():
            raise cl.CaseError("Owners are set in Render (ADMIN_EMAILS). Remove them there.", 409)
        cur.execute("DELETE FROM admin_emails WHERE email = %s", (email,))
        cur.execute("DELETE FROM admins WHERE user_id IN (SELECT id FROM users WHERE LOWER(email) = %s)", (email,))
        _audit(cur, "admin_remove", email, "")
        return {"success": True}
    return _db(work, write=True)
