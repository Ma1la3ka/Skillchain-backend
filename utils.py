"""Utility functions for SkillChain Backend"""
import math
import uuid
import json
import requests as req
from config import SQUAD_BASE_URL, SQUAD_HEADERS


def haversine_distance(lat1, lng1, lat2, lng2):
    """Calculate distance between two GPS coordinates in meters"""
    R = 6_371_000  # Earth radius in meters
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
            "bvn": "22222222222",  # Note: Live accounts require real BVNs
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

    # Fallback for demo if API fails
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
        "bank_code": "000013",  # Squad sandbox payout bank code
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
