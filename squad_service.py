"""Squad (GTCO) integration: checkout collection, payment verification, account lookup,
bank transfers (payouts), transfer requery and webhook signature check. Not a blueprint.

All amounts passed in are NAIRA; Squad wants KOBO, converted here with Decimal (no float drift).
"""
import hashlib
import hmac
import json
from decimal import Decimal, ROUND_HALF_UP
import requests
from config import SQUAD_SECRET_KEY, SQUAD_MERCHANT_ID, SQUAD_BASE_URL

# Bank list with NIP codes (what Squad's lookup/transfer expect). Confirm against your Squad
# dashboard; a wrong code fails safely at account lookup, before any money moves.
BANKS = [
    ("Access Bank", "000014"), ("Citibank", "000009"), ("Ecobank", "000010"),
    ("Fidelity Bank", "000007"), ("First Bank of Nigeria", "000016"), ("FCMB", "000003"),
    ("Globus Bank", "000027"), ("Guaranty Trust Bank", "000013"), ("Heritage Bank", "000020"),
    ("Jaiz Bank", "000006"), ("Keystone Bank", "000002"), ("Polaris Bank", "000008"),
    ("Providus Bank", "000023"), ("Stanbic IBTC Bank", "000012"),
    ("Standard Chartered Bank", "000021"), ("Sterling Bank", "000001"), ("SunTrust Bank", "000022"),
    ("Titan Trust Bank", "000025"), ("United Bank for Africa", "000004"), ("Union Bank", "000018"),
    ("Unity Bank", "000011"), ("Wema Bank", "000017"), ("Zenith Bank", "000015"),
    ("Opay", "100004"), ("PalmPay", "100033"), ("Kuda Bank", "090267"), ("Moniepoint", "090405"),
    ("Carbon", "100026"), ("VFD Microfinance Bank", "090110"), ("Sparkle", "090325"),
    ("FairMoney", "090551"), ("Paga", "100002"),
]
BANK_BY_CODE = {code: name for name, code in BANKS}


def to_kobo(amount_naira):
    return int((Decimal(str(amount_naira)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _headers():
    return {"Authorization": f"Bearer {SQUAD_SECRET_KEY}", "Content-Type": "application/json"}


def _call(method, path, payload=None, timeout=25):
    """Returns {"http", "body", "ambiguous"}. ambiguous=True means we cannot tell whether Squad
    acted (timeout / 5xx / 424), so money movers must NOT assume failure."""
    try:
        r = requests.request(method, SQUAD_BASE_URL + path, json=payload, headers=_headers(), timeout=timeout)
    except requests.RequestException as e:
        return {"http": None, "body": {"message": str(e)}, "ambiguous": True}
    try:
        body = r.json()
    except ValueError:
        body = {}
    return {"http": r.status_code, "body": body or {}, "ambiguous": r.status_code >= 500 or r.status_code == 424}


def _ok(res):
    return res["http"] == 200 and (res["body"].get("success") is True or res["body"].get("status") == 200)


# ── Collection ──────────────────────────────────────────────────────────────
def initialize_payment(email, amount_naira, reference, callback_url=None, customer_name=None, metadata=None):
    payload = {
        "amount": to_kobo(amount_naira), "email": email, "currency": "NGN",
        "initiate_type": "inline", "transaction_ref": reference,
    }
    if callback_url:
        payload["callback_url"] = callback_url
    if customer_name:
        payload["customer_name"] = customer_name
    if metadata:
        payload["metadata"] = metadata
    res = _call("POST", "/transaction/initiate", payload)
    url = (res["body"].get("data") or {}).get("checkout_url")
    return {"ok": bool(_ok(res) and url), "checkout_url": url, "message": res["body"].get("message", "")}


def verify_payment(reference):
    """{"ok": call worked, "paid": bool, "amount_kobo": int|None}. Never trust the webhook alone."""
    res = _call("GET", f"/transaction/verify/{reference}")
    if not _ok(res):
        return {"ok": False, "paid": False, "amount_kobo": None}
    d = res["body"].get("data") or {}
    status = str(d.get("transaction_status") or "").lower()
    amt = d.get("transaction_amount")
    return {"ok": True, "paid": status == "success", "amount_kobo": int(amt) if amt is not None else None}


# ── Payout ──────────────────────────────────────────────────────────────────
def lookup_account(bank_code, account_number):
    res = _call("POST", "/payout/account/lookup", {"bank_code": bank_code, "account_number": account_number})
    name = (res["body"].get("data") or {}).get("account_name")
    return {"ok": bool(_ok(res) and name), "account_name": name, "message": res["body"].get("message", "")}


def full_reference(suffix):
    return f"{SQUAD_MERCHANT_ID}_{suffix}"


def transfer(suffix, amount_naira, bank_code, account_number, account_name, remark="SkillChain withdrawal"):
    """state: success | failed | unknown. 'unknown' = leave the payout pending and requery later."""
    ref = full_reference(suffix)
    if not SQUAD_SECRET_KEY or not SQUAD_MERCHANT_ID:
        return {"state": "failed", "reference": ref, "message": "Payouts are not configured."}
    res = _call("POST", "/payout/transfer", {
        "transaction_reference": ref, "amount": str(to_kobo(amount_naira)),
        "bank_code": bank_code, "account_number": account_number,
        "account_name": account_name, "currency_id": "NGN", "remark": remark,
    }, timeout=45)
    msg = str(res["body"].get("message", ""))
    if res["ambiguous"]:
        return {"state": "unknown", "reference": ref, "message": msg}
    if _ok(res):
        return {"state": "success", "reference": ref, "message": msg}
    return {"state": "failed", "reference": ref, "message": msg}


def requery_transfer(suffix_or_ref):
    """state: success | failed | pending | unknown"""
    ref = suffix_or_ref if suffix_or_ref.startswith(f"{SQUAD_MERCHANT_ID}_") else full_reference(suffix_or_ref)
    res = _call("POST", "/payout/requery", {"transaction_reference": ref})
    if res["ambiguous"] or not _ok(res):
        return {"state": "unknown", "message": str(res["body"].get("message", ""))}
    d = res["body"].get("data") or {}
    text = " ".join(str(d.get(k) or "") for k in ("transaction_status", "status", "response_description")).lower()
    if "revers" in text or "fail" in text:
        return {"state": "failed", "message": text}
    if "success" in text or "approved" in text or "completed" in text:
        return {"state": "success", "message": text}
    if "pend" in text or "process" in text:
        return {"state": "pending", "message": text}
    return {"state": "unknown", "message": text}


# ── Webhook signature ───────────────────────────────────────────────────────
def verify_webhook_signature(raw_body, signature):
    if not signature or not SQUAD_SECRET_KEY:
        return False
    key = SQUAD_SECRET_KEY.encode()
    candidates = [raw_body]
    try:   # Squad's own sample code signs the re-serialised JSON, so accept that form too
        candidates.append(json.dumps(json.loads(raw_body), separators=(",", ":"), ensure_ascii=False).encode())
    except ValueError:
        pass
    sig = signature.strip().lower()
    return any(hmac.compare_digest(hmac.new(key, c, hashlib.sha512).hexdigest(), sig) for c in candidates)
