"""Fix requests, disputes, late complaints and their settlement.

Rules (kept in one place so the policy page and the code agree):
  * After work is submitted the client has the review window to approve, request a fix (max 2 rounds) or dispute.
  * A fix request puts the job back to 'assigned' so the worker can check in again. The worker has 48h; if they
    do not respond, the case escalates to an admin dispute.
  * A dispute freezes the escrow until an admin decides. Both sides can add evidence.
  * After payment, the client has 7 days to report a problem (a 'late' case). An admin can refund part of the
    money from the worker's wallet (typically 20 to 30 percent, up to the amount the worker received).
Every function here expects an open transaction on `cur` and leaves commit/rollback to the caller.
"""
from decimal import Decimal
from utils import money, credit_wallet

MAX_FIXES = 2
FIX_RESPONSE_HOURS = 48
LATE_WINDOW_DAYS = 7
REVIEW_STATES = ("verified", "pending_verification")


class CaseError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def lock_job(cur, job_id):
    cur.execute("SELECT * FROM jobs WHERE id = %s FOR UPDATE", (job_id,))
    job = cur.fetchone()
    if not job:
        raise CaseError("Job not found.", 404)
    return job


def open_case(cur, job_id):
    cur.execute("SELECT * FROM job_cases WHERE job_id = %s AND status = 'open' ORDER BY id DESC LIMIT 1", (job_id,))
    return cur.fetchone()


def fixes_used(cur, job_id):
    cur.execute("SELECT COUNT(*) AS c FROM job_cases WHERE job_id = %s AND kind = 'fix'", (job_id,))
    return int(cur.fetchone()["c"])


def _clean(reason, minimum):
    reason = " ".join(str(reason or "").split())
    if len(reason) < minimum:
        raise CaseError(f"Please explain in at least {minimum} characters.")
    return reason[:900]


def request_fix(cur, job_id, client_id, reason):
    job = lock_job(cur, job_id)
    if str(job["client_id"]) != str(client_id):
        raise CaseError("This is not your job.", 403)
    if job["status"] not in REVIEW_STATES or not job.get("escrow_paid"):
        raise CaseError("A fix can only be requested while submitted work is waiting for your review.", 409)
    if open_case(cur, job_id):
        raise CaseError("There is already an open request on this job.", 409)
    used = fixes_used(cur, job_id)
    if used >= MAX_FIXES:
        raise CaseError("You have used both fix requests. Approve the work or open a dispute.", 409)
    reason = _clean(reason, 10)
    cur.execute("""INSERT INTO job_cases (job_id, kind, round_no, opened_by, opener_role, reason, respond_by)
                   VALUES (%s, 'fix', %s, %s, 'client', %s, NOW() + INTERVAL %s HOUR)""",
                (job_id, used + 1, client_id, reason, FIX_RESPONSE_HOURS))
    case_id = cur.lastrowid
    cur.execute("UPDATE jobs SET status = 'assigned', review_deadline = NULL WHERE id = %s AND status IN ('verified','pending_verification')", (job_id,))
    if cur.rowcount != 1:
        raise CaseError("This job just changed. Refresh and try again.", 409)
    return case_id, job, used + 1


def open_dispute(cur, job_id, user_id, reason):
    job = lock_job(cur, job_id)
    reason = _clean(reason, 20)
    existing = open_case(cur, job_id)
    if str(user_id) == str(job["client_id"]):
        role = "client"
        if existing:
            raise CaseError("There is already an open request on this job.", 409)
        if job["status"] not in REVIEW_STATES or not job.get("escrow_paid"):
            raise CaseError("A dispute can only be opened while submitted work is waiting for your review.", 409)
    elif str(user_id) == str(job.get("worker_id")):
        role = "worker"
        if not (existing and existing["kind"] == "fix" and job["status"] == "assigned"):
            raise CaseError("You can only dispute while the client's fix request is open.", 409)
        cur.execute("UPDATE job_cases SET status = 'cancelled', resolved_at = NOW() WHERE id = %s AND status = 'open'", (existing["id"],))
    else:
        raise CaseError("This is not your job.", 403)
    cur.execute("UPDATE jobs SET status = 'disputed', dispute_reason = %s, disputed_at = NOW() WHERE id = %s AND status IN ('verified','pending_verification','assigned')",
                (reason, job_id))
    if cur.rowcount != 1:
        raise CaseError("This job just changed. Refresh and try again.", 409)
    cur.execute("""INSERT INTO job_cases (job_id, kind, opened_by, opener_role, reason, respond_by)
                   VALUES (%s, 'dispute', %s, %s, %s, NOW() + INTERVAL 48 HOUR)""", (job_id, user_id, role, reason))
    return cur.lastrowid, job, role


def open_late(cur, job_id, client_id, reason):
    job = lock_job(cur, job_id)
    if str(job["client_id"]) != str(client_id):
        raise CaseError("This is not your job.", 403)
    if job["status"] != "paid" or not job.get("paid_at"):
        raise CaseError("A problem can only be reported on a job that was paid.", 409)
    cur.execute("SELECT COUNT(*) AS c FROM job_cases WHERE job_id = %s AND kind IN ('late','dispute')", (job_id,))
    if int(cur.fetchone()["c"]):
        raise CaseError("A report already exists for this job.", 409)
    cur.execute("SELECT (paid_at >= NOW() - INTERVAL %s DAY) AS ok FROM jobs WHERE id = %s", (LATE_WINDOW_DAYS, job_id))
    if not cur.fetchone()["ok"]:
        raise CaseError(f"The {LATE_WINDOW_DAYS}-day window to report a problem has passed.", 409)
    reason = _clean(reason, 20)
    cur.execute("INSERT INTO job_cases (job_id, kind, opened_by, opener_role, reason, respond_by) VALUES (%s,'late',%s,'client',%s, NOW() + INTERVAL 48 HOUR)",
                (job_id, client_id, reason))
    return cur.lastrowid, job


def add_evidence(cur, case_id, user_id, note, media_url=None, media_type=None):
    cur.execute("SELECT c.*, j.client_id, j.worker_id FROM job_cases c JOIN jobs j ON j.id = c.job_id WHERE c.id = %s", (case_id,))
    case = cur.fetchone()
    if not case:
        raise CaseError("Case not found.", 404)
    if str(user_id) == str(case["client_id"]):
        role = "client"
    elif str(user_id) == str(case["worker_id"]):
        role = "worker"
    else:
        raise CaseError("This is not your job.", 403)
    if case["status"] != "open":
        raise CaseError("This case is closed.", 409)
    cur.execute("SELECT COUNT(*) AS c FROM case_evidence WHERE case_id = %s AND user_id = %s", (case_id, user_id))
    if int(cur.fetchone()["c"]) >= 12:
        raise CaseError("You have reached the limit of 12 evidence items.", 409)
    note = " ".join(str(note or "").split())[:900] or None
    cur.execute("INSERT INTO case_evidence (case_id, user_id, role, note, media_url, media_type) VALUES (%s,%s,%s,%s,%s,%s)",
                (case_id, user_id, role, note, media_url, media_type))
    return role, case


def close_fix_on_resubmit(cur, job_id):
    cur.execute("UPDATE job_cases SET status = 'resolved', outcome = 'fix_done', resolved_at = NOW() WHERE job_id = %s AND kind = 'fix' AND status = 'open'", (job_id,))
    return cur.rowcount


def escalate_overdue_fixes(cur):
    """Worker ignored a fix request for 48h: the case becomes an admin dispute. Returns [(case_id, job_id)]."""
    cur.execute("SELECT id, job_id, reason FROM job_cases WHERE kind = 'fix' AND status = 'open' AND respond_by IS NOT NULL AND respond_by <= NOW() LIMIT 50")
    out = []
    for c in cur.fetchall():
        note = "[Worker did not respond to the fix request in time] " + c["reason"]
        cur.execute("UPDATE jobs SET status = 'disputed', dispute_reason = %s, disputed_at = NOW() WHERE id = %s AND status = 'assigned'", (note[:1000], c["job_id"]))
        if cur.rowcount == 1:
            cur.execute("UPDATE job_cases SET kind = 'dispute', reason = %s, respond_by = NULL WHERE id = %s AND status = 'open'", (note[:1000], c["id"]))
            out.append((c["id"], c["job_id"]))
    return out


def ensure_legacy_dispute_case(cur, job_id, client_id, reason):
    """Old dispute buttons only set jobs.status='disputed'. Give those an admin case too."""
    cur.execute("SELECT status FROM jobs WHERE id = %s", (job_id,))
    j = cur.fetchone()
    if not j or j["status"] != "disputed" or open_case(cur, job_id):
        return None
    cur.execute("INSERT INTO job_cases (job_id, kind, opened_by, opener_role, reason, respond_by) VALUES (%s,'dispute',%s,'client',%s, NOW() + INTERVAL 48 HOUR)",
                (job_id, client_id, (" ".join(str(reason or '').split()) or "No reason given")[:900]))
    return cur.lastrowid


def resolve_case(cur, case_id, admin_id, worker_amount, client_refund, note):
    """Admin decision. Returns a dict describing what happened. Money rules:
       dispute: escrow (client_pays) is split into worker_amount (<= artisan_gets) and client_refund; the rest is platform fee.
       late:    worker already holds artisan_gets; client_refund (<= that) is clawed back from the worker's wallet."""
    cur.execute("SELECT * FROM job_cases WHERE id = %s FOR UPDATE", (case_id,))
    case = cur.fetchone()
    if not case or case["status"] != "open" or case["kind"] not in ("dispute", "late"):
        raise CaseError("This case is not open for a decision.", 409)
    job = lock_job(cur, case["job_id"])
    W, R = money(worker_amount), money(client_refund)
    if W < 0 or R < 0:
        raise CaseError("Amounts cannot be negative.")
    note = " ".join(str(note or "").split())[:900]
    if len(note) < 5:
        raise CaseError("Write a short note explaining your decision.")
    paid_to_worker = money(job["artisan_gets"] or job["amount"])
    ref = f"case_{case_id}"

    if case["kind"] == "dispute":
        if job["status"] != "disputed" or not job.get("escrow_paid"):
            raise CaseError("This job is not in a disputed, funded state.", 409)
        held = money(job["client_pays"] or job["amount"])
        if W > paid_to_worker:
            raise CaseError(f"The worker's share cannot exceed {paid_to_worker}.")
        if W + R > held:
            raise CaseError(f"Worker share plus refund cannot exceed the escrow held ({held}).")
        if W + R <= 0:
            raise CaseError("Choose a worker payment, a client refund, or both.")
        cur.execute("""UPDATE jobs SET status = 'paid', paid_at = NOW(), transfer_reference = %s, artisan_gets = %s
                       WHERE id = %s AND status = 'disputed' AND paid_at IS NULL""", (f"{ref}_award", str(W), job["id"]))
        if cur.rowcount != 1:
            raise CaseError("This job was already settled.", 409)
        if W > 0:
            credit_wallet(cur, job["worker_id"], W, f"{ref}_award", f"Dispute settled for job #{job['id']}")
            cur.execute("UPDATE users SET jobs_completed = jobs_completed + 1 WHERE id = %s", (job["worker_id"],))
    else:  # late complaint, job already paid
        if job["status"] != "paid":
            raise CaseError("This job is not in a paid state.", 409)
        if R > paid_to_worker:
            raise CaseError(f"A refund cannot exceed what the worker received ({paid_to_worker}).")
        W = Decimal("0.00")
        if R > 0:   # the worker's balance may go negative: it blocks withdrawals until earnings cover it
            cur.execute("UPDATE users SET escrow_balance = ROUND(COALESCE(escrow_balance,0) - %s, 2) WHERE id = %s", (str(R), job["worker_id"]))
            cur.execute("INSERT INTO wallet_ledger (user_id, direction, amount, reference, note) VALUES (%s,'debit',%s,%s,%s)",
                        (job["worker_id"], str(R), f"{ref}_clawback", f"Refund to client after complaint on job #{job['id']}"))
    if R > 0:
        cur.execute("INSERT INTO refunds (case_id, job_id, client_id, amount) VALUES (%s,%s,%s,%s)", (case_id, job["id"], job["client_id"], str(R)))
    outcome = "no_action" if (W == 0 and R == 0) else "split" if (W > 0 and R > 0) else "worker_paid" if W > 0 else "client_refunded"
    cur.execute("""UPDATE job_cases SET status = 'resolved', outcome = %s, worker_amount = %s, client_refund = %s,
                   admin_id = %s, admin_note = %s, resolved_at = NOW() WHERE id = %s AND status = 'open'""",
                (outcome, str(W), str(R), admin_id, note, case_id))
    return {"outcome": outcome, "worker_amount": W, "client_refund": R, "job": job, "case": case}
