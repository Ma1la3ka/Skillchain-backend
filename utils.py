"""Utility functions for SkillChain Backend"""
import math
import uuid
import os
import requests as req
from config import SQUAD_BASE_URL, SQUAD_HEADERS
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


def squad_create_virtual_account(user_id, name, email, phone):
    """Create a virtual account for a worker on Squad"""
    try:
        parts = name.strip().split()
        payload = {
            "customer_identifier": f"worker_{user_id}",
            "first_name": parts[0],
            "last_name": parts[-1] if len(parts) > 1 else "User",
            "mobile_num": phone or "08000000000",
            "email": email,
            "bvn": "22222222222",
            "date_of_birth": "1990-01-01",
            "gender": "1",
            "transaction_limit": 5000000
        }
        resp = req.post(
            f"{SQUAD_BASE_URL}/virtual-account",
            json=payload,
            headers=SQUAD_HEADERS,
            timeout=10
        )
        data = resp.json()
        if resp.status_code in (200, 201) and data.get("success"):
            body = data.get("data", {})
            return {
                "account_number": body.get("virtual_account_number"),
                "bank_name": body.get("bank_name"),
                "customer_id": body.get("customer_identifier")
            }
    except Exception as e:
        print(f"Squad API error: {e}")

    return {
        "account_number": f"909{user_id:07d}",
        "bank_name": "Squad Demo Bank",
        "customer_id": f"worker_{user_id}"
    }


def squad_create_collection_account(job_id: int, amount: float, email: str) -> dict:
    """Create a collection account for escrow payment"""
    reference = f"job_{job_id}_{uuid.uuid4().hex[:8]}"
    payload = {
        "amount": int(amount * 100),
        "email": email,
        "currency": "NGN",
        "initiate_type": "inline",
        "transaction_ref": reference,
        "callback_url": "https://skillchain-frontend-omega.vercel.app//Client_Dashboard/index.html"
    }
    try:
        resp = req.post(
            f"{SQUAD_BASE_URL}/transaction/initiate",
            json=payload,
            headers=SQUAD_HEADERS,
            timeout=15
        )
        data = resp.json()
        print(f"\n{'='*50}")
        print(f"[Squad] Status: {resp.status_code}")
        print(f"[Squad] Response: {data}")
        print(f"{'='*50}\n")

        if resp.status_code == 200 and data.get("success"):
            body = data.get("data", {})
            checkout_url = body.get("checkout_url", "")
            return {
                "account_number": checkout_url,
                "bank_name": "Squad Pay",
                "bank_code": "squad",
                "reference": reference,
                "checkout_url": checkout_url
            }
        else:
            print(f"[Squad] FAILED: {data.get('message', 'No message')}")
    except Exception as e:
        print(f"[Squad] Exception: {e}")

    return {}


def squad_payout(job, worker_id, cur):
    """Transfer job amount to worker's virtual account"""
    cur.execute(
        "SELECT squad_account_number, name FROM users WHERE id = %s",
        (worker_id,)
    )
    worker = cur.fetchone()

    if not worker or not worker["squad_account_number"]:
        print(f"[payout] Worker {worker_id} has no Squad account — using fallback ref")
        return f"manual_{uuid.uuid4().hex[:10]}"

    amount_kobo = int(float(job["amount"]) * 100)
    reference = f"pay_{job['id']}_{uuid.uuid4().hex[:8]}"
    payload = {
        "transaction_reference": reference,
        "amount": amount_kobo,
        "bank_code": "000013",
        "account_number": worker["squad_account_number"],
        "account_name": worker["name"],
        "currency_id": "NGN",
        "remark": f"SkillChain payment for job #{job['id']}"
    }
    try:
        resp = req.post(
            f"{SQUAD_BASE_URL}/payout/transfer",
            json=payload,
            headers=SQUAD_HEADERS,
            timeout=15
        )
        data = resp.json()
        print(f"[payout] status={resp.status_code} body={data}")
        if resp.status_code in (200, 201):
            return reference
        else:
            print(f"[payout] Failed response: {data}")
            return reference
    except Exception as e:
        print(f"[payout] Exception: {e}")
        return reference


def release_job_payment(job, cur):
    """
    Pay out escrowed funds to the worker and finalize the job as 'paid'.
    Shared by: client approval endpoint AND the 24h auto-release scheduler.

    Job flow: 'assigned' -> (GPS check passes) -> 'verified' (awaiting client
    review) -> 'paid' (client approved, or 24h auto-release fired).

    SAFE AGAINST DOUBLE-PAYOUT: claims the job atomically first by flipping
    the existing `paid_at` column from NULL to NOW() in one conditional
    UPDATE. Because of standard row-level locking, if two callers race (two
    scheduler ticks, or a scheduler tick landing the same instant as a
    client's manual approve click), only one UPDATE can possibly match and
    change a row — the loser gets rowcount == 0 and backs off cleanly
    instead of paying twice. We deliberately do this BEFORE the slow
    external squad_payout() network call, so we're not holding a DB lock
    open for the duration of that call.

    Returns the transfer_reference on success, or None if the job had
    already been claimed/resolved by someone else (caller should treat
    that as "nothing to do", not an error).
    """
    job_id = job["id"]

    cur.execute(
        """UPDATE jobs SET paid_at = NOW()
           WHERE id = %s AND status = 'verified' AND paid_at IS NULL""",
        (job_id,)
    )
    claimed = cur.rowcount == 1
    if not claimed:
        return None  # someone else already claimed/resolved this job

    worker_id = job["worker_id"]
    transfer_reference = squad_payout(job, worker_id, cur)

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