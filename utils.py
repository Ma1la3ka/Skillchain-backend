"""Utility functions for SkillChain Backend: geo, wallet/escrow money logic, email."""
import math
import uuid
from decimal import Decimal, ROUND_HALF_UP
from threading import Thread
import requests as req
from config import FRONTEND_URL, RESEND_API_KEY as CONFIG_RESEND_API_KEY
import squad_service


def haversine_distance(lat1, lng1, lat2, lng2):
    """Calculate distance between two GPS coordinates in meters"""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def money(x):
    """Exact 2-decimal Decimal from any number/string."""
    return Decimal(str(x or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ── Collection (client funds escrow via Squad checkout) ─────────────────────
def create_collection_link(ref_id, amount, email, name=None):
    """Returns {} on failure. Keys kept compatible with the columns client.py already writes."""
    reference = f"{ref_id}_{uuid.uuid4().hex[:10]}"
    result = squad_service.initialize_payment(
        email=email, amount_naira=amount, reference=reference,
        callback_url=f"{FRONTEND_URL}/client_dashboard/index.html",
        customer_name=name, metadata={"ref": str(ref_id)},
    )
    if not result["ok"]:
        print(f"[squad] initialize_payment failed: {result['message']}")
        return {}
    return {"account_number": result["checkout_url"], "bank_name": "Squad", "bank_code": "squad",
            "reference": reference, "checkout_url": result["checkout_url"]}


# ── Wallet ledger (caller owns the transaction: commit/rollback) ────────────
def credit_wallet(cur, user_id, amount, reference, note):
    amount = money(amount)
    cur.execute("UPDATE users SET escrow_balance = ROUND(COALESCE(escrow_balance,0) + %s, 2) WHERE id = %s",
                (str(amount), user_id))
    cur.execute("""INSERT INTO wallet_ledger (user_id, direction, amount, reference, note)
                   VALUES (%s, 'credit', %s, %s, %s)""", (user_id, str(amount), reference, note))


def _release(table, row, cur, extra_user_sql):
    """Atomically mark a funded, verified job/slot paid and credit the worker's wallet.
    Returns a reference string, or None if unfunded / already released."""
    rid = row["id"]
    if not row.get("escrow_paid"):
        print(f"[release] {table} {rid} has no funded escrow - refusing.")
        return None
    ref = f"wallet_{table}_{rid}"
    cur.execute(
        f"""UPDATE {table} SET status='paid', paid_at=NOW(), transfer_reference=%s
            WHERE id=%s AND status='verified' AND paid_at IS NULL AND escrow_paid=1""",
        (ref, rid))
    if cur.rowcount != 1:
        return None
    cur.execute(f"SELECT worker_id, artisan_gets, amount FROM {table} WHERE id=%s", (rid,))
    r = cur.fetchone()
    gets = money(r["artisan_gets"] or r["amount"])
    credit_wallet(cur, r["worker_id"], gets, ref, f"Payment for {table[:-1] if table == 'jobs' else 'slot'} #{rid}")
    cur.execute(f"UPDATE users SET jobs_completed = jobs_completed + 1{extra_user_sql} WHERE id = %s", (r["worker_id"],))
    return ref


def release_job_payment(job, cur):
    """Client approval or 24h auto-release: credit the artisan's wallet. The artisan then
    withdraws to their bank with a PIN. Idempotent via the conditional UPDATE."""
    return _release("jobs", job, cur, "")


def release_gig_slot_payment(slot, cur):
    # Quick gigs are removed from the apps; kept only until those backend routes are deleted.
    return _release("job_workers", slot, cur, ", quick_gigs_completed = quick_gigs_completed + 1")


# ── Payout state changes (caller commits) ───────────────────────────────────
def refund_payout(cur, payout_id, message=""):
    """Failed payout -> money goes back to the wallet. Safe to call twice (status guard)."""
    cur.execute("UPDATE payouts SET status='failed', provider_message=%s, updated_at=NOW() "
                "WHERE id=%s AND status IN ('processing','pending')", (message[:250], payout_id))
    if cur.rowcount != 1:
        return False
    cur.execute("SELECT user_id, amount FROM payouts WHERE id=%s", (payout_id,))
    p = cur.fetchone()
    credit_wallet(cur, p["user_id"], p["amount"], f"payout_{payout_id}_refund", "Withdrawal failed - refunded")
    cur.execute("UPDATE users SET total_withdrawn = GREATEST(0, ROUND(COALESCE(total_withdrawn,0) - %s, 2)) WHERE id=%s",
                (str(money(p["amount"])), p["user_id"]))
    return True


def mark_payout_success(cur, payout_id, message=""):
    cur.execute("UPDATE payouts SET status='success', provider_message=%s, updated_at=NOW() "
                "WHERE id=%s AND status IN ('processing','pending')", (message[:250], payout_id))
    return cur.rowcount == 1


# ─────────────────────────────────────────────────────────────────────────────
# RESEND EMAIL (verified domain: hamzlabs.com)
# ─────────────────────────────────────────────────────────────────────────────

RESEND_API_KEY = CONFIG_RESEND_API_KEY
SENDER_EMAIL = "hello@hamzlabs.com"
SENDER_NAME = "SkillChain"


def _send_email_blocking(email, token, user_name="User", email_type="reset"):
    """Send email via Resend API using verified hamzlabs.com domain"""
    try:
        if not RESEND_API_KEY:
            print("✗ RESEND_API_KEY not set in environment variables")
            return False

        if email_type == "reset":
            subject = "SkillChain - Password Reset Code"
            heading = "Password Reset Request"
            body_text = "We received a request to reset your SkillChain password. Use the code below to proceed:"
            code_label = token

        elif email_type == "pin_reset":
            subject = "SkillChain - Withdrawal PIN Reset Code"
            heading = "Withdrawal PIN Reset"
            body_text = "We received a request to reset your SkillChain withdrawal PIN. Use the code below to proceed:"
            code_label = token

        else:
            subject = "Verify your SkillChain account"
            heading = "Verify Your Email"
            body_text = "Welcome to SkillChain! Use the code below to verify your email and activate your account:"
            code_label = token


        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 20px;">
                <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <h2 style="color: #333; margin-bottom: 20px;">{heading}</h2>
                    <p style="color: #555; font-size: 16px; line-height: 1.6;">Hi {user_name},</p>
                    <p style="color: #555; font-size: 16px; line-height: 1.6;">{body_text}</p>
                    <div style="background-color: #FF4D2E; color: white; padding: 15px; border-radius: 5px; text-align: center; margin: 30px 0; font-size: 28px; font-weight: bold; letter-spacing: 3px;">
                        {code_label}
                    </div>
                    <p style="color: #555; font-size: 14px;"><strong>This code expires in 10 minutes.</strong></p>
                    <p style="color: #555; font-size: 14px;">If you didn't request this, please ignore this email.</p>
                    <hr style="border: none; border-top: 1px solid #ddd; margin: 30px 0;">
                    <p style="color: #999; font-size: 12px; text-align: center;">
                        SkillChain by Hamz Labs<br>
                        <a href="{FRONTEND_URL}" style="color: #FF4D2E;">Visit SkillChain</a>
                    </p>
                </div>
            </body>
        </html>
        """

        response = req.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "from": f"{SENDER_NAME} <{SENDER_EMAIL}>",
                "to": email,
                "subject": subject,
                "html": html_body
            },
            timeout=15
        )

        if response.status_code in (200, 202):
            print(f"✓ Email sent to {email} (type: {email_type})")
            return True
        else:
            print(f"✗ Resend API error: {response.status_code} | {response.text}")
            return False

    except Exception as e:
        print(f"✗ Failed to send email to {email}: {type(e).__name__}: {e}")
        return False


def send_reset_email(email, token, user_name="User"):
    """Send password reset email in background thread"""
    thread = Thread(
        target=_send_email_blocking,
        args=(email, token, user_name, "reset"),
        daemon=False
    )
    thread.start()
    thread.join(timeout=15)
    print(f"[EMAIL] Reset thread completed for {email}")
    return True

def send_pin_reset_email(email, token, user_name="User"):
    """Send PIN reset email in background thread"""  
    thread = Thread(
        target=_send_email_blocking,
        args=(email, token, user_name, "pin_reset"),
        daemon=False
    )
    thread.start()
    thread.join(timeout=15)
    print(f"[EMAIL] PIN reset thread completed for {email}") 
    return True




def send_verification_email(email, code, user_name="User"):
    """Send account verification email in background thread"""
    thread = Thread(
        target=_send_email_blocking,
        args=(email, code, user_name, "verify"),
        daemon=False
    )
    thread.start()
    thread.join(timeout=15)
    print(f"[EMAIL] Verify thread completed for {email}")
    return True