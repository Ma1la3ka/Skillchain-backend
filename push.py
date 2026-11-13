"""Push notifications through Firebase Cloud Messaging (FCM HTTP v1).
Everything here is best-effort and runs in background threads: a push failure never breaks a request."""
import json
import os
import threading
import requests
from database_helper import get_db
from utils import haversine_distance

_creds, _project, _lock, _warned = None, None, threading.Lock(), False
JOB_ALERT_RADIUS_KM = float(os.getenv("JOB_ALERT_RADIUS_KM", "30"))
JOB_ALERT_MAX_WORKERS = 100


def _ready():
    global _creds, _project, _warned
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if not raw:
        if not _warned:
            print("[push] FIREBASE_SERVICE_ACCOUNT_JSON not set - push disabled")
            _warned = True
        return False
    if _creds is None:
        from google.oauth2 import service_account
        info = json.loads(raw)
        _creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
        _project = info["project_id"]
    return True


def _access_token():
    from google.auth.transport.requests import Request
    with _lock:
        if not _creds.valid:
            _creds.refresh(Request())
        return _creds.token


def spawn(fn, *args):
    threading.Thread(target=_safe, args=(fn, args), daemon=True).start()


def _safe(fn, args):
    try:
        fn(*args)
    except Exception as e:
        print(f"[push] {getattr(fn, '__name__', 'task')} failed: {e}")


def _query(sql, params=()):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def job_info(job_id):
    rows = _query("""SELECT id, title, client_id, worker_id, amount, artisan_gets, trade, site_address, site_lat, site_lng
                     FROM jobs WHERE id = %s""", (job_id,))
    return rows[0] if rows else {}


def _naira(x):
    try:
        return "₦{:,.0f}".format(float(x or 0))
    except (TypeError, ValueError):
        return ""


def send_many(user_ids, title, body, data=None, skip=None):
    """Send to every registered device of these users (never to `skip`, the person who acted)."""
    ids = {int(u) for u in user_ids if str(u).isdigit() and (skip is None or int(u) != int(skip))}
    if not ids or not _ready():
        return
    marks = ",".join(["%s"] * len(ids))
    tokens = _query(f"SELECT token FROM device_tokens WHERE user_id IN ({marks})", tuple(ids))
    payload_data = {k: str(v) for k, v in (data or {}).items() if v is not None}
    dead = []
    for t in tokens:
        r = requests.post(
            f"https://fcm.googleapis.com/v1/projects/{_project}/messages:send",
            headers={"Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json"},
            json={"message": {"token": t["token"], "notification": {"title": title[:80], "body": body[:180]},
                              "data": payload_data,
                              "android": {"priority": "HIGH", "notification": {"channel_id": "default"}}}},
            timeout=15)
        if r.status_code != 200:
            err = (r.json().get("error") or {}) if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 404 or "UNREGISTERED" in json.dumps(err):
                dead.append(t["token"])
            else:
                print(f"[push] FCM error {r.status_code}: {str(err)[:200]}")
    if dead:
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute(f"DELETE FROM device_tokens WHERE token IN ({','.join(['%s'] * len(dead))})", tuple(dead))
            conn.commit()
        finally:
            cur.close()
            conn.close()


def role_in(job, user_id):
    return "worker" if job and str(job.get("worker_id")) == str(user_id) else "client"


# ── Event helpers ───────────────────────────────────────────────────────────
def pick_alert_recipients(job, candidates):
    """Workers of the job's trade whose saved shop location is within the radius.
    Workers with no saved location still get it (we cannot filter them)."""
    out = []
    for c in candidates:
        if str(c["id"]) == str(job.get("client_id")):
            continue
        if c.get("shop_lat") and c.get("shop_lng") and job.get("site_lat") and job.get("site_lng"):
            km = haversine_distance(float(job["site_lat"]), float(job["site_lng"]),
                                    float(c["shop_lat"]), float(c["shop_lng"])) / 1000
            if km > JOB_ALERT_RADIUS_KM:
                continue
        out.append(c["id"])
        if len(out) >= JOB_ALERT_MAX_WORKERS:
            break
    return out


def notify_new_job(job_id):
    job = job_info(job_id)
    if not job or not job.get("trade"):
        return
    cands = _query("""SELECT u.id, u.shop_lat, u.shop_lng FROM users u
                      WHERE u.trade = %s AND EXISTS (SELECT 1 FROM device_tokens d WHERE d.user_id = u.id)""",
                   (job["trade"],))
    place = ", ".join([p.strip() for p in str(job.get("site_address") or "").split(",")[:2] if p.strip()])
    send_many(pick_alert_recipients(job, cands), f"New {job['trade']} job near you",
              f"{job['title']} · {_naira(job['amount'])}" + (f" · {place}" if place else ""),
              {"role": "worker", "type": "new_job", "job_id": job_id})


def notify_escrow(job_id):
    job = job_info(job_id)
    if not job:
        return
    send_many([job["client_id"]], "Payment received", f"{_naira(job['amount'])} is held safely for “{job['title']}”.",
              {"role": "client", "type": "escrow", "job_id": job_id})
    if job.get("worker_id"):
        send_many([job["worker_id"]], "Escrow funded", f"The client has paid for “{job['title']}”. You can start now.",
                  {"role": "worker", "type": "escrow", "job_id": job_id})


def notify_released(job_id, auto=False):
    job = job_info(job_id)
    if job and job.get("worker_id"):
        send_many([job["worker_id"]], "Payment released",
                  f"{_naira(job['artisan_gets'] or job['amount'])} was added to your wallet for “{job['title']}”."
                  + (" (auto-released after 24 hours)" if auto else ""),
                  {"role": "worker", "type": "paid", "job_id": job_id})
