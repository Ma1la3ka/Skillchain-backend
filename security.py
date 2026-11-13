"""Token auth, identity enforcement and rate limiting.

Every request outside PUBLIC must carry `Authorization: Bearer <token>` (issued by /login).
Any user_id / sender_id / viewer_id sent by the client MUST equal the token's user id, so a
caller can no longer act as someone else by editing a parameter.
"""
import threading
import time
from flask import request, jsonify, g
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from config import SECRET_KEY, TOKEN_TTL_DAYS

_ser = URLSafeTimedSerializer(SECRET_KEY, salt="skillchain-auth-v1")


def issue_token(user_id):
    return _ser.dumps({"uid": int(user_id)})


def read_token(token):
    try:
        data = _ser.loads(token, max_age=TOKEN_TTL_DAYS * 86400)
        return int(data["uid"])
    except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
        return None


# No token needed
PUBLIC = {
    "/", "/healthz", "/login", "/register", "/verify-email", "/resend-verification",
    "/forgot-password", "/reset-password", "/reset-password-final", "/logout-api",
    "/api/banks", "/api/squad/webhook",
}
# Works with or without a token (the QR verify page is public; handlers strip private fields)
OPTIONAL_AUTH = {"/api/worker/public-profile"}
# Endpoints where user_id means "the person being looked up", not the caller
TARGET_USER_ENDPOINTS = {"/api/user-status"}
ACTOR_KEYS = ("user_id", "sender_id", "viewer_id")

# path -> (max hits, window seconds)
LIMITS = {
    "/login": (10, 600), "/register": (10, 3600), "/verify-email": (10, 600),
    "/resend-verification": (5, 3600), "/forgot-password": (5, 3600),
    "/reset-password": (10, 600), "/reset-password-final": (10, 600),
    "/api/worker/reset-pin": (10, 600), "/api/worker/forgot-pin": (5, 3600),
    "/api/worker/withdraw": (10, 600), "/api/account/delete": (5, 3600), "/api/verify-account": (30, 600),
}
_hits, _lock = {}, threading.Lock()


def _allow(key, limit, window):
    now = time.time()
    with _lock:
        q = [t for t in _hits.get(key, []) if now - t < window]
        if len(q) >= limit:
            _hits[key] = q
            return False
        q.append(now)
        _hits[key] = q
        if len(_hits) > 20000:          # crude memory guard
            for k in list(_hits)[:5000]:
                _hits.pop(k, None)
        return True


def _json_body():
    return (request.get_json(silent=True) or {}) if request.is_json else {}


def _claimed_ids(body):
    for k in ACTOR_KEYS:
        for src in (request.args, request.form, body):
            v = src.get(k) if hasattr(src, "get") else None
            if v not in (None, ""):
                yield str(v).strip()


def _gate():
    if request.method == "OPTIONS":
        return None
    path = request.path.rstrip("/") or "/"
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    g.uid = read_token(token) if token else None
    body = _json_body()

    if path in LIMITS:
        limit, window = LIMITS[path]
        ident = str(g.uid or request.remote_addr)
        keys = [f"{path}:{ident}"]
        email = str(body.get("email", "") or request.form.get("email", "")).strip().lower()
        if email:
            keys.append(f"{path}:email:{email}")
        if not all(_allow(k, limit, window) for k in keys):
            return jsonify({"success": False, "message": "Too many attempts. Please wait and try again."}), 429

    if path in PUBLIC or (path == "/api/heartbeat" and request.method == "GET"):
        return None
    if path in OPTIONAL_AUTH and g.uid is None:
        return None

    if g.uid is None:
        return jsonify({"success": False, "error": "Authentication required.", "auth_required": True}), 401

    if path not in TARGET_USER_ENDPOINTS:
        for cid in _claimed_ids(body):
            if cid != str(g.uid):
                return jsonify({"success": False, "error": "Forbidden."}), 403
    return None


def init_security(app):
    app.before_request(_gate)
