"""Turns successful API calls into push notifications, without touching the route code.
Runs after each response; the actual work happens in a background thread."""
from flask import Blueprint, request, g
import push

notify_bp = Blueprint("notify", __name__)


def _to(user_id, title, body, job, role=None, kind="update", skip=None):
    push.send_many([user_id], title, body,
                   {"role": role or push.role_in(job, user_id), "type": kind, "job_id": (job or {}).get("id")}, skip=skip)


def _assign(v, out, uid):
    j = push.job_info(v("job_id"))
    _to(v("worker_id"), "You got the job", f"“{j.get('title')}” was assigned to you.", j, "worker", "assigned", uid)


def _hire(v, out, uid):
    push.send_many([v("worker_id")], "New job invitation", f"You were invited to “{v('title')}”.", {"role": "worker", "type": "invite"}, skip=uid)


def _send_offer(v, out, uid):
    j = push.job_info(v("job_id"))
    _to(v("worker_id"), "New price offer", f"{push._naira(v('amount'))} offered for “{j.get('title')}”.", j, "worker", "offer", uid)


def _bargain_row(bid):
    rows = push._query("""SELECT b.worker_id, b.job_id, b.proposed_price FROM bargains b WHERE b.id = %s""", (bid,))
    return rows[0] if rows else None


def _client_respond_bargain(v, out, uid):
    b = _bargain_row(v("bargain_id"))
    if not b:
        return
    j = push.job_info(b["job_id"])
    ok = v("action") == "accept"
    _to(b["worker_id"], "Offer accepted" if ok else "Offer declined",
        f"“{j.get('title')}” " + ("is yours. Open the app to start." if ok else "was declined. The client may suggest another price."),
        j, "worker", "offer", uid)


def _worker_bargain(v, out, uid):
    j = push.job_info(v("job_id"))
    _to(j.get("client_id"), "New price offer", f"A worker offered {push._naira(v('proposed_price'))} for “{j.get('title')}”.", j, "client", "offer", uid)


def _worker_respond_bargain(v, out, uid):
    b = _bargain_row(v("bargain_id"))
    if not b:
        return
    j = push.job_info(b["job_id"])
    ok = v("action") == "accept"
    _to(j.get("client_id"), "Offer accepted" if ok else "Offer declined",
        f"The worker {'accepted' if ok else 'declined'} your offer for “{j.get('title')}”.", j, "client", "offer", uid)


def _accept_job(v, out, uid):
    j = push.job_info(v("job_id"))
    _to(j.get("client_id"), "New worker request", f"A worker wants to take “{j.get('title')}”.", j, "client", "applicant", uid)


def _invitation(v, out, uid):
    j = push.job_info(v("job_id"))
    ok = v("action") == "accept"
    _to(j.get("client_id"), "Invitation accepted" if ok else "Invitation declined",
        f"The worker {'accepted' if ok else 'declined'} “{j.get('title')}”.", j, "client", "applicant", uid)


def _submitted(v, out, uid):
    if not v("job_id"):
        return
    j = push.job_info(v("job_id"))
    flag = " Location was flagged, check the proof." if out.get("in_range") is False else ""
    _to(j.get("client_id"), "Work submitted", f"Review the proof for “{j.get('title')}”.{flag}", j, "client", "submitted", uid)


def _review(v, out, uid):
    if v("action") == "approve":
        push.notify_released(v("job_id"))
    elif v("action") == "dispute":
        _dispute(v, out, uid)


def _dispute(v, out, uid):
    j = push.job_info(v("job_id"))
    if j.get("worker_id"):
        _to(j["worker_id"], "Dispute raised", f"The client raised a dispute on “{j.get('title')}”.", j, "worker", "dispute", uid)


def _chat(v, out, uid):
    rows = push._query("SELECT name FROM users WHERE id = %s", (uid,))
    j = push.job_info(v("job_id"))
    text = str(v("content") or v("body") or "")
    push.send_many([v("recipient_id")], rows[0]["name"] if rows else "New message", text,
                   {"role": push.role_in(j, v("recipient_id")), "type": "chat", "job_id": v("job_id")}, skip=uid)


RULES = {
    "/api/client/assign-worker": _assign, "/api/client/direct-hire": _hire, "/api/client/send-offer": _send_offer,
    "/api/client/respond-bargain": _client_respond_bargain, "/api/client/review-job": _review,
    "/api/client/approve-job": lambda v, o, u: push.notify_released(v("job_id")), "/api/client/dispute-job": _dispute,
    "/api/worker/bargain": _worker_bargain, "/api/worker/respond-bargain": _worker_respond_bargain,
    "/api/worker/accept-job": _accept_job, "/api/worker/respond-invitation": _invitation,
    "/api/verify-job": _submitted, "/api/chat/send": _chat,
    "/api/client/post-job": lambda v, o, u: o.get("job_id") and push.notify_new_job(int(o["job_id"])),
}


@notify_bp.after_app_request
def _after(resp):
    try:
        rule = RULES.get(request.path.rstrip("/"))
        if rule and request.method == "POST" and resp.status_code == 200 and resp.is_json:
            out = resp.get_json(silent=True) or {}
            if out.get("success") is not False:
                vals = dict(request.form.to_dict())
                vals.update((request.get_json(silent=True) or {}) if request.is_json else {})
                push.spawn(rule, vals.get, out, getattr(g, "uid", None))
    except Exception as e:
        print(f"[notify] hook error: {e}")
    return resp
