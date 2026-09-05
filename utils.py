"""Utility functions for SkillChain Backend"""
import math
import uuid
import os
import requests as req
from paystack_service import initialize_transaction, create_transfer_recipient, initiate_transfer
from threading import Thread


def haversine_distance(lat1, lng1, lat2, lng2):
    """Calculate distance between two GPS coordinates in meters"""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def create_virtual_account(user_id, name, email, phone):
    """
    NOTE: Paystack doesn't need a per-worker virtual account for this app's
    flow — payouts go straight to the worker's bank account via Transfer,
    and escrow collection uses a single Checkout link per job, not a
    dedicated account per worker. This function is kept ONLY so existing
    call sites don't break on a missing import. It's a no-op stub.
    """
    return {
        "account_number": None,
        "bank_name": None,
        "customer_id": f"worker_{user_id}"
    }


def paystack_create_collection_account(job_id: int, amount: float, email: str) -> dict:
    """Create a Paystack Checkout link for a client to fund a job's escrow."""
    reference = f"job_{job_id}_{uuid.uuid4().hex[:8]}"
    callback_url = "https://skillchain-frontend-omega.vercel.app//Client_dashboard/index.html"

    result = initialize_transaction(
        email=email,
        amount_naira=amount,
        reference=reference,
        callback_url=callback_url,
        metadata={"job_id": job_id}
    )

    print(f"\n{'='*50}")
    print(f"[Paystack] initialize_transaction response: {result}")
    print(f"{'='*50}\n")

    if result.get("status") and result.get("data"):
        checkout_url = result["data"].get("authorization_url", "")
        return {
            "account_number": checkout_url,
            "bank_name": "Paystack",
            "bank_code": "paystack",
            "reference": reference,
            "checkout_url": checkout_url
        }
    else:
        print(f"[Paystack] initialize_transaction FAILED: {result.get('message', 'No message')}")

    return {}


def paystack_payout(job, worker_id, payout_amount, cur):
    """Transfer the given amount to the worker's bank account via Paystack.

    `job` may be a row from either `jobs` or `job_workers` — only `job['id']`
    is used here (for the transfer reference/reason text), so both shapes work.
    `payout_amount` must already be the fee-adjusted amount (artisan_gets),
    resolved by the caller — this function never recomputes it from job['amount'].
    """
    cur.execute(
        """SELECT bank_account_no, bank_code, bank_account_name, name,
                  paystack_recipient_code
           FROM users WHERE id = %s""",
        (worker_id,)
    )
    worker = cur.fetchone()

    if not worker or not worker["bank_account_no"] or not worker["bank_code"]:
        print(f"[payout] Worker {worker_id} has no bank details on file — using fallback ref")
        return f"manual_{uuid.uuid4().hex[:10]}"

    recipient_code = worker.get("paystack_recipient_code")

    if not recipient_code:
        rec_result = create_transfer_recipient(
            name=worker["bank_account_name"] or worker["name"],
            account_number=worker["bank_account_no"],
            bank_code=worker["bank_code"]
        )
        print(f"[payout] create_transfer_recipient response: {rec_result}")

        if rec_result.get("status") and rec_result.get("data"):
            recipient_code = rec_result["data"]["recipient_code"]
            cur.execute(
                "UPDATE users SET paystack_recipient_code = %s WHERE id = %s",
                (recipient_code, worker_id)
            )
        else:
            print(f"[payout] Could not create recipient: {rec_result.get('message')}")
            return f"manual_{uuid.uuid4().hex[:10]}"

    reference = f"pay_{job['id']}_{uuid.uuid4().hex[:8]}"

    transfer_result = initiate_transfer(
        amount_naira=payout_amount,
        recipient_code=recipient_code,
        reference=reference,
        reason=f"SkillChain payment for job #{job['id']}"
    )
    print(f"[payout] initiate_transfer response: {transfer_result}")

    return reference


def release_job_payment(job, cur):
    """
    Pay out escrowed funds to the worker and finalize a *regular job* (or
    single-slot gig) as 'paid'. Single source of truth for this table —
    called by: client approval endpoint (review-job) AND the 24h
    auto-release scheduler. Do not duplicate this logic elsewhere.

    SAFE AGAINST DOUBLE-PAYOUT: claims the job atomically first by flipping
    the existing `paid_at` column from NULL to NOW() in one conditional
    UPDATE. Only one caller can win that race; the loser backs off cleanly.

    SAFE AGAINST UNFUNDED PAYOUT: refuses to release if escrow_paid is not
    set — this can happen if a worker submits proof before the client ever
    funds escrow, and 24h passes with no client action.

    Trust score is NOT bumped here — it's derived solely from client_rating
    averages in api_rate_worker, so a job paid without a rating (e.g. via
    24h auto-release) doesn't inflate the score with no real signal behind it.

    Returns the transfer_reference on success, or None if already claimed
    or escrow was never funded.
    """
    job_id = job["id"]

    if not job.get("escrow_paid"):
        print(f"[release_job_payment] Job {job_id} has no funded escrow — refusing to release.")
        return None

    cur.execute(
        """UPDATE jobs SET paid_at = NOW()
           WHERE id = %s AND status = 'verified' AND paid_at IS NULL""",
        (job_id,)
    )
    claimed = cur.rowcount == 1
    if not claimed:
        return None

    worker_id = job["worker_id"]
    artisan_gets = float(job.get("artisan_gets") or job["amount"] or 0)
    transfer_reference = paystack_payout(job, worker_id, artisan_gets, cur)

    cur.execute(
        """UPDATE jobs SET
           status             = 'paid',
           transfer_reference = %s
           WHERE id = %s""",
        (transfer_reference, job_id)
    )

    cur.execute(
        "UPDATE users SET jobs_completed = jobs_completed + 1 WHERE id = %s",
        (worker_id,)
    )

    return transfer_reference


def release_gig_slot_payment(slot, cur):
    """
    Same as release_job_payment, but for a single worker's slot on a
    multi-slot quick gig (job_workers table instead of jobs). Single
    source of truth for this table — called by: client's
    review-gig-submission endpoint AND the 24h auto-release scheduler.

    Same safety guarantees as release_job_payment: atomic claim via
    paid_at, and refuses to release unfunded escrow.

    Returns the transfer_reference on success, or None if already claimed
    or escrow was never funded.
    """
    slot_id = slot["id"]

    if not slot.get("escrow_paid"):
        print(f"[release_gig_slot_payment] Slot {slot_id} has no funded escrow — refusing to release.")
        return None

    cur.execute(
        """UPDATE job_workers SET paid_at = NOW()
           WHERE id = %s AND status = 'verified' AND paid_at IS NULL""",
        (slot_id,)
    )
    claimed = cur.rowcount == 1
    if not claimed:
        return None

    worker_id = slot["worker_id"]
    artisan_gets = float(slot.get("artisan_gets") or slot["amount"] or 0)
    transfer_reference = paystack_payout(slot, worker_id, artisan_gets, cur)

    cur.execute(
        """UPDATE job_workers SET
           status             = 'paid',
           transfer_reference = %s
           WHERE id = %s""",
        (transfer_reference, slot_id)
    )

    cur.execute(
        """UPDATE users SET
           jobs_completed       = jobs_completed + 1,
           quick_gigs_completed = quick_gigs_completed + 1
           WHERE id = %s""",
        (worker_id,)
    )

    return transfer_reference


# ─────────────────────────────────────────────────────────────────────────────
# RESEND EMAIL (verified domain: hamzlabs.com)
# ─────────────────────────────────────────────────────────────────────────────

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
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
                        <a href="https://skillchain-frontend-omega.vercel.app" style="color: #FF4D2E;">Visit SkillChain</a>
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