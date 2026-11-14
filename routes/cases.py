"""Fix requests, disputes, late complaints and evidence (client and worker side)."""
import uuid
import cloudinary.uploader
from flask import Blueprint, request, jsonify, g
from database_helper import get_db
import cases_logic as cl
import push

cases_bp = Blueprint('cases', __name__, url_prefix='/api/cases')


def _tx(work):
    """Run work(cur) in one transaction. Returns (result, error_response)."""
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        result = work(cur)
        conn.commit()
        return result, None
    except cl.CaseError as e:
        conn.rollback()
        return None, (jsonify({"success": False, "message": e.message}), e.status)
    except Exception as e:
        conn.rollback()
        print(f"[cases error] {e}")
        return None, (jsonify({"success": False, "message": "Something went wrong. Please try again."}), 500)
    finally:
        cur.close()
        conn.close()


def _body():
    return request.get_json(silent=True) or {}


def _tell(user_id, title, body, job_id, role):
    push.spawn(push.send_many, [user_id], title, body, {"role": role, "type": "case", "job_id": job_id}, g.uid)


@cases_bp.route("/request-fix", methods=["POST"])
def api_request_fix():
    d = _body()
    res, err = _tx(lambda cur: cl.request_fix(cur, d.get("job_id"), g.uid, d.get("reason")))
    if err:
        return err
    case_id, job, rnd = res
    _tell(job["worker_id"], "Fix requested", f"“{job['title']}”: the client asked for a fix (round {rnd} of {cl.MAX_FIXES}). Check in again within 48 hours.", job["id"], "worker")
    return jsonify({"success": True, "case_id": case_id, "message": "Fix requested. The worker has 48 hours to respond."})


@cases_bp.route("/dispute", methods=["POST"])
def api_dispute():
    d = _body()
    res, err = _tx(lambda cur: cl.open_dispute(cur, d.get("job_id"), g.uid, d.get("reason")))
    if err:
        return err
    case_id, job, role = res
    other, other_role = (job["worker_id"], "worker") if role == "client" else (job["client_id"], "client")
    _tell(other, "Dispute opened", f"A dispute was opened on “{job['title']}”. Add your side and evidence within 48 hours. Payment is on hold.", job["id"], other_role)
    return jsonify({"success": True, "case_id": case_id, "message": "Dispute opened. Support will review both sides."})


@cases_bp.route("/late", methods=["POST"])
def api_late():
    d = _body()
    res, err = _tx(lambda cur: cl.open_late(cur, d.get("job_id"), g.uid, d.get("reason")))
    if err:
        return err
    case_id, job = res
    _tell(job["worker_id"], "Problem reported", f"The client reported a problem on “{job['title']}” after payment. Add your side and evidence.", job["id"], "worker")
    return jsonify({"success": True, "case_id": case_id, "message": "Your report was sent. Support will review it."})


@cases_bp.route("/evidence", methods=["POST"])
def api_evidence():
    case_id = request.form.get("case_id", type=int)
    note = request.form.get("note", "")
    files = [f for f in request.files.getlist("files") if f and f.filename][:4]
    if not case_id or not (note.strip() or files):
        return jsonify({"success": False, "message": "Add a note, a photo or a video."}), 400

    def party_check(cur):   # before any upload, so strangers cannot use our storage
        cur.execute("SELECT c.status, j.client_id, j.worker_id FROM job_cases c JOIN jobs j ON j.id = c.job_id WHERE c.id = %s", (case_id,))
        c = cur.fetchone()
        if not c or g.uid not in (c["client_id"], c["worker_id"]):
            raise cl.CaseError("Case not found.", 404)
        if c["status"] != "open":
            raise cl.CaseError("This case is closed.", 409)
    _, err = _tx(party_check)
    if err:
        return err

    uploaded = []
    for f in files:
        ctype = f.content_type or ""
        if not (ctype.startswith("image/") or ctype.startswith("video/")):
            continue
        is_video = ctype.startswith("video/")
        try:
            r = cloudinary.uploader.upload(f, folder=f"skillchain/cases/{case_id}", public_id=f"ev_{case_id}_{uuid.uuid4().hex[:8]}",
                                           resource_type="video" if is_video else "image")
            uploaded.append((r["secure_url"], "video" if is_video else "image"))
        except Exception as e:
            print(f"[evidence upload error] {e}")
    if files and not uploaded and not note.strip():
        return jsonify({"success": False, "message": "Upload failed. Check your connection and try again."}), 502

    def save(cur):
        role = case = None
        for i, (url, typ) in enumerate(uploaded or [(None, None)]):
            role, case = cl.add_evidence(cur, case_id, g.uid, note if i == 0 else None, url, typ)
        return role, case
    res, err = _tx(save)
    if err:
        return err
    role, case = res
    other, other_role = (case["worker_id"], "worker") if role == "client" else (case["client_id"], "client")
    _tell(other, "New evidence added", "The other side added evidence to your open case.", case["job_id"], other_role)
    return jsonify({"success": True, "added": max(len(uploaded), 1)})


@cases_bp.route("/job", methods=["GET"])
def api_case_state():
    job_id = request.args.get("job_id", type=int)

    def work(cur):
        cur.execute("""SELECT id, client_id, worker_id, status, escrow_paid, paid_at,
                              GREATEST(0, %s - TIMESTAMPDIFF(DAY, paid_at, NOW())) AS late_days_left
                       FROM jobs WHERE id = %s""", (cl.LATE_WINDOW_DAYS, job_id))
        job = cur.fetchone()
        if not job or g.uid not in (job["client_id"], job["worker_id"]):
            raise cl.CaseError("Job not found.", 404)
        role = "client" if g.uid == job["client_id"] else "worker"
        cur.execute("""SELECT id, kind, status, reason, round_no, opener_role, outcome, worker_amount, client_refund, admin_note,
                              GREATEST(0, TIMESTAMPDIFF(HOUR, NOW(), respond_by)) AS hours_left
                       FROM job_cases WHERE job_id = %s ORDER BY (status = 'open') DESC, id DESC LIMIT 1""", (job_id,))
        case = cur.fetchone()
        evidence = []
        if case:
            cur.execute("""SELECT id, role, note, media_url, media_type, TIMESTAMPDIFF(SECOND, created_at, NOW()) AS age
                           FROM case_evidence WHERE case_id = %s ORDER BY id""", (case["id"],))
            evidence = cur.fetchall()
            for k in ("worker_amount", "client_refund"):
                case[k] = float(case[k]) if case[k] is not None else None
        used = cl.fixes_used(cur, job_id)
        is_open = bool(case and case["status"] == "open")
        cur.execute("SELECT COUNT(*) AS c FROM job_cases WHERE job_id = %s AND kind IN ('late','dispute')", (job_id,))
        has_claim = int(cur.fetchone()["c"]) > 0
        in_review = job["status"] in cl.REVIEW_STATES and bool(job["escrow_paid"])
        return {
            "role": role, "case": case, "evidence": evidence, "fixes_used": used, "fixes_left": max(cl.MAX_FIXES - used, 0),
            "can_request_fix": role == "client" and in_review and not is_open and used < cl.MAX_FIXES,
            "can_dispute": (role == "client" and in_review and not is_open) or
                           (role == "worker" and is_open and case["kind"] == "fix" and job["status"] == "assigned"),
            "can_late_complaint": role == "client" and job["status"] == "paid" and bool(job["paid_at"]) and not has_claim and int(job["late_days_left"] or 0) > 0,
            "late_days_left": int(job["late_days_left"] or 0) if job["status"] == "paid" else 0,
            "can_add_evidence": is_open,
        }
    res, err = _tx(work)
    return err if err else jsonify(res)


@cases_bp.after_app_request
def _after(resp):
    """Keep fix cases in step with the existing endpoints, without editing them."""
    try:
        path = request.path.rstrip("/")
        if resp.status_code != 200 or not resp.is_json or request.method != "POST":
            return resp
        out = resp.get_json(silent=True) or {}
        if path == "/api/verify-job" and out.get("status") in cl.REVIEW_STATES and request.form.get("job_id"):
            jid = request.form.get("job_id")
            _tx(lambda cur: cl.close_fix_on_resubmit(cur, jid))
        elif path in ("/api/client/review-job", "/api/client/dispute-job") and out.get("success") is not False:
            b = request.get_json(silent=True) or {}
            if path.endswith("dispute-job") or b.get("action") == "dispute":
                _tx(lambda cur: cl.ensure_legacy_dispute_case(cur, b.get("job_id"), g.get("uid"), b.get("reason")))
    except Exception as e:
        print(f"[cases hook] {e}")
    return resp
