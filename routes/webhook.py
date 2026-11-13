"""Squad webhook: confirms that a client really paid into escrow."""
from decimal import Decimal
from flask import Blueprint, request, jsonify
from database_helper import get_db
import squad_service
import push
from utils import money

webhook_bp = Blueprint('webhook', __name__, url_prefix='/api')


def mark_escrow_paid(conn, cur, reference, paid_kobo):
    """Shared by the webhook and the reconciler. Returns a status string; commits itself."""
    table, row = None, None
    for t in ("jobs", "job_workers"):
        cur.execute(f"SELECT id, amount, client_pays, escrow_paid FROM {t} WHERE escrow_reference = %s", (reference,))
        row = cur.fetchone()
        if row:
            table = t
            break
    if not row:
        return "not_found"
    if row["escrow_paid"]:
        return "already_paid"

    expected = money(row["client_pays"] or row["amount"])      # what the client was charged (amount + fee)
    received = (Decimal(int(paid_kobo)) / 100).quantize(Decimal("0.01"))
    if received + Decimal("1") < expected:                      # short payment: record, do not release
        cur.execute(f"UPDATE {table} SET escrow_amount_received = %s WHERE id = %s", (str(received), row["id"]))
        conn.commit()
        print(f"[squad] amount mismatch on {table} {row['id']}: expected {expected}, got {received}")
        return "amount_mismatch"

    cur.execute(
        f"""UPDATE {table} SET escrow_paid=1, escrow_paid_at=NOW(), escrow_amount_received=%s
            WHERE id=%s AND escrow_paid=0""", (str(received), row["id"]))
    conn.commit()
    if cur.rowcount == 1:
        if table == "jobs":
            push.spawn(push.notify_escrow, row["id"])
        return "ok"
    return "already_paid"


@webhook_bp.route("/squad/webhook", methods=["POST"])
def api_squad_webhook():
    raw = request.get_data()
    sig = request.headers.get("x-squad-encrypted-body") or request.headers.get("x-squad-signature") or ""
    if not squad_service.verify_webhook_signature(raw, sig):
        print("[squad] webhook signature mismatch - rejected")
        return jsonify({"status": "signature_invalid"}), 401

    payload = request.get_json(silent=True) or {}
    if str(payload.get("Event", "")).lower() != "charge_successful":
        return jsonify({"status": "ignored"}), 200

    body = payload.get("Body") or {}
    reference = body.get("transaction_ref") or payload.get("TransactionRef") or ""
    if not reference:
        return jsonify({"status": "no_reference"}), 200

    # Never trust the webhook amount: ask Squad directly what was paid.
    check = squad_service.verify_payment(reference)
    if not check["ok"]:
        return jsonify({"status": "verify_unavailable"}), 503      # non-200 so Squad retries
    if not check["paid"]:
        return jsonify({"status": "not_paid"}), 200

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    try:
        status = mark_escrow_paid(conn, cur, reference, check["amount_kobo"] or 0)
        return jsonify({"status": status}), 200
    except Exception as e:
        conn.rollback()
        print(f"[squad] webhook DB error: {e}")
        return jsonify({"status": "db_error"}), 500
    finally:
        cur.close()
        conn.close()
