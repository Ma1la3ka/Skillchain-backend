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


def paystack_payout(job, worker_id, cur):
    """Transfer job amount to the worker's bank account via Paystack."""
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
        amount_naira=float(job["amount"]),
        recipient_code=recipient_code,
        reference=reference,
        reason=f"SkillChain payment for job #{job['id']}"
    )
    print(f"[payout] initiate_transfer response: {transfer_result}")

    return reference


def release_job_payment(job, cur):
    """
    Pay out escrowed funds to the worker and finalize the job as 'paid'.
    Shared by: client approval endpoint AND the 24h auto-release scheduler.

    SAFE AGAINST DOUBLE-PAYOUT: claims the job atomically first by flipping
    the existing `paid_at` column from NULL to NOW() in one conditional
    UPDATE. Only one caller can win that race; the loser backs off cleanly.

    Returns the transfer_reference on success, or None if already claimed.
    """
    job_id = job["id"]

    cur.execute(
        """UPDATE jobs SET paid_at = NOW()
           WHERE id = %s AND status = 'verified' AND paid_at IS NULL""",
        (job_id,)
    )
    claimed = cur.rowcount == 1
    if not claimed:
        return None

    worker_id = job["worker_id"]
    transfer_reference = paystack_payout(job, worker_id, cur)

    cur.execute(
        """UPDATE jobs SET
           status             = 'paid',
           transfer_reference = %s
           WHERE id = %s""",
        (transfer_reference, job_id)
    )

    cur.execute(
        """UPDATE users
           SET jobs_completed = jobs_completed + 1,
               trust_score    = LEAST(5.0, trust_score + 0.1)
           WHERE id = %s""",
        (worker_id,)
    )

    return transfer_reference


def _send_email_blocking(email, token, user_name="User"):
    """Send password reset email via Resend API"""
    try:
        RESEND_API_KEY = os.getenv("RESEND_API_KEY")
        if not RESEND_API_KEY:
            print("✗ RESEND_API_KEY not set in environment variables")
            return

        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 20px;">
                <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <h2 style="color: #333; margin-bottom: 20px;">Password Reset Request</h2>
                    <p style="color: #555; font-size: 16px; line-height: 1.6;">Hi {user_name},</p>
                    <p style="color: #555; font-size: 16px; line-height: 1.6;">
                        We received a request to reset your SkillChain password. Use the code below to proceed:
                    </p>
                    <div style="background-color: #e85c00; color: white; padding: 15px; border-radius: 5px; text-align: center; margin: 30px 0; font-size: 28px; font-weight: bold; letter-spacing: 3px;">
                        {token}
                    </div>
                    <p style="color: #555; font-size: 14px;"><strong>This code expires in 10 minutes.</strong></p>
                    <p style="color: #555; font-size: 14px;">If you didn't request this, please ignore this email.</p>
                    <hr style="border: none; border-top: 1px solid #ddd; margin: 30px 0;">
                    <p style="color: #999; font-size: 12px; text-align: center;">
                        SkillChain Team<br>
                        <a href="https://skillchain-frontend-omega.vercel.app" style="color: #e85c00;">Visit SkillChain</a>
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
                "from": "SkillChain <onboarding@resend.dev>",
                "to": email,
                "subject": "SkillChain - Password Reset Code",
                "html": html_body
            },
            timeout=15
        )

        if response.status_code == 200:
            print(f"✓ Password reset email sent to {email}")
        else:
            print(f"✗ Resend API error: {response.status_code} | {response.text}")

    except Exception as e:
        print(f"✗ Failed to send email to {email}: {type(e).__name__}: {e}")


def send_reset_email(email, token, user_name="User"):
    """Send password reset email in background thread"""
    thread = Thread(target=_send_email_blocking, args=(email, token, user_name), daemon=False)
    thread.start()
    thread.join(timeout=15)
    print(f"[EMAIL] Thread completed for {email}")
    return True


def _send_verification_email_blocking(email, code, user_name="User"):
    """Send account-verification code via Resend API"""
    try:
        RESEND_API_KEY = os.getenv("RESEND_API_KEY")
        if not RESEND_API_KEY:
            print("✗ RESEND_API_KEY not set in environment variables")
            return

        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 20px;">
                <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <h2 style="color: #333; margin-bottom: 20px;">Verify Your Email</h2>
                    <p style="color: #555; font-size: 16px; line-height: 1.6;">Hi {user_name},</p>
                    <p style="color: #555; font-size: 16px; line-height: 1.6;">
                        Welcome to SkillChain! Use the code below to verify your email and activate your account:
                    </p>
                    <div style="background-color: #e85c00; color: white; padding: 15px; border-radius: 5px; text-align: center; margin: 30px 0; font-size: 28px; font-weight: bold; letter-spacing: 3px;">
                        {code}
                    </div>
                    <p style="color: #555; font-size: 14px;"><strong>This code expires in 10 minutes.</strong></p>
                    <p style="color: #555; font-size: 14px;">If you didn't create a SkillChain account, please ignore this email.</p>
                    <hr style="border: none; border-top: 1px solid #ddd; margin: 30px 0;">
                    <p style="color: #999; font-size: 12px; text-align: center;">
                        SkillChain Team<br>
                        <a href="https://skillchain-frontend-omega.vercel.app" style="color: #e85c00;">Visit SkillChain</a>
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
                "from": "SkillChain <onboarding@resend.dev>",
                "to": email,
                "subject": "Verify your SkillChain account",
                "html": html_body
            },
            timeout=15
        )

        if response.status_code == 200:
            print(f"✓ Verification email sent to {email}")
        else:
            print(f"✗ Resend API error: {response.status_code} | {response.text}")

    except Exception as e:
        print(f"✗ Failed to send verification email to {email}: {type(e).__name__}: {e}")


def send_verification_email(email, code, user_name="User"):
    """Send account verification email in background thread"""
    thread = Thread(target=_send_verification_email_blocking, args=(email, code, user_name), daemon=False)
    thread.start()
    thread.join(timeout=15)
    return True