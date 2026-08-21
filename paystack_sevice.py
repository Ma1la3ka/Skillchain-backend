"""Paystack integration — wraps the Paystack API for escrow collection and payouts.
Not a Flask blueprint; other route files import functions from here.
"""
import os
import requests

PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
PAYSTACK_BASE_URL    = "https://api.paystack.co"

def _headers():
    return {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


# ── Collection (client funds escrow) ────────────────────────────────────────

def initialize_transaction(email, amount_naira, reference, callback_url=None, metadata=None):
    """
    Starts a Paystack Checkout transaction. amount_naira is converted to kobo
    (Paystack's base unit) automatically. Returns the API response dict —
    on success, response['data']['authorization_url'] is what you redirect
    the client to (or show as a payment link/button).
    """
    payload = {
        "email": email,
        "amount": int(round(amount_naira * 100)),  # kobo
        "reference": reference,
    }
    if callback_url:
        payload["callback_url"] = callback_url
    if metadata:
        payload["metadata"] = metadata

    try:
        res = requests.post(
            f"{PAYSTACK_BASE_URL}/transaction/initialize",
            json=payload, headers=_headers(), timeout=15
        )
        return res.json()
    except requests.RequestException as e:
        return {"status": False, "message": str(e)}


def verify_transaction(reference):
    """
    Confirms what Paystack actually received for a given reference.
    Always call this from your webhook/verify route before trusting a payment —
    never trust amount/status from the frontend alone.
    Returns the API response dict; response['data']['amount'] is in kobo,
    response['data']['status'] is 'success' | 'failed' | 'abandoned'.
    """
    try:
        res = requests.get(
            f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}",
            headers=_headers(), timeout=15
        )
        return res.json()
    except requests.RequestException as e:
        return {"status": False, "message": str(e)}


# ── Payout (worker withdraws) ───────────────────────────────────────────────

def list_banks():
    """Returns Paystack's supported Nigerian bank list — code + name pairs."""
    try:
        res = requests.get(
            f"{PAYSTACK_BASE_URL}/bank?currency=NGN",
            headers=_headers(), timeout=15
        )
        return res.json()
    except requests.RequestException as e:
        return {"status": False, "message": str(e)}


def resolve_account_number(account_number, bank_code):
    """Verifies an account number against a bank, returns the account holder's name."""
    try:
        res = requests.get(
            f"{PAYSTACK_BASE_URL}/bank/resolve",
            params={"account_number": account_number, "bank_code": bank_code},
            headers=_headers(), timeout=15
        )
        return res.json()
    except requests.RequestException as e:
        return {"status": False, "message": str(e)}


def create_transfer_recipient(name, account_number, bank_code):
    """
    Registers a payout destination with Paystack. Only needs account number +
    bank code — no BVN/NIN required for recipients in Nigeria.
    Returns response['data']['recipient_code'] on success — save this on the
    worker's row so you don't recreate a recipient on every withdrawal.
    """
    payload = {
        "type": "nuban",
        "name": name,
        "account_number": account_number,
        "bank_code": bank_code,
        "currency": "NGN",
    }
    try:
        res = requests.post(
            f"{PAYSTACK_BASE_URL}/transferrecipient",
            json=payload, headers=_headers(), timeout=15
        )
        return res.json()
    except requests.RequestException as e:
        return {"status": False, "message": str(e)}


def initiate_transfer(amount_naira, recipient_code, reference, reason=None):
    """
    Sends money out to a previously-created recipient. amount_naira converted
    to kobo automatically. In test mode this always returns success instantly
    with no real money moving — use this for full end-to-end testing before
    switching to live keys.
    """
    payload = {
        "source": "balance",
        "amount": int(round(amount_naira * 100)),
        "recipient": recipient_code,
        "reference": reference,
    }
    if reason:
        payload["reason"] = reason

    try:
        res = requests.post(
            f"{PAYSTACK_BASE_URL}/transfer",
            json=payload, headers=_headers(), timeout=15
        )
        return res.json()
    except requests.RequestException as e:
        return {"status": False, "message": str(e)}


# ── Webhook signature verification ──────────────────────────────────────────

def verify_webhook_signature(request_body_bytes, signature_header):
    """
    Paystack signs every webhook with HMAC-SHA512 of your secret key.
    Reject anything that doesn't match this — otherwise anyone could POST
    a fake 'charge.success' to your webhook URL and get free escrow credit.
    """
    import hmac
    import hashlib
    computed = hmac.new(
        PAYSTACK_SECRET_KEY.encode("utf-8"),
        request_body_bytes,
        hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(computed, signature_header or "")