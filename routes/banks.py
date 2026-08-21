"""Bank verification and withdrawal routes"""
from flask import Blueprint, request, jsonify
from paystack_service import list_banks, resolve_account_number

banks_bp = Blueprint('banks', __name__, url_prefix='/api')


@banks_bp.route("/banks", methods=["GET"])
def api_get_banks():
    """Get list of Nigerian banks from Paystack"""
    result = list_banks()
    if result.get("status") and result.get("data"):
        banks = [{"name": b.get("name"), "code": b.get("code")} for b in result["data"]]
        return jsonify({"success": True, "banks": banks})

    print(f"[banks error] {result.get('message')}")
    return jsonify({"success": False, "message": "Could not load bank list."}), 500


@banks_bp.route("/verify-account", methods=["GET"])
def api_verify_account():
    """Verify bank account details via Paystack"""
    account_no = request.args.get("account_no", "").strip()
    bank_code = request.args.get("bank_code", "").strip()

    if len(account_no) != 10 or not bank_code:
        return jsonify({"success": False, "message": "Invalid account details."})

    result = resolve_account_number(account_no, bank_code)

    if result.get("status") and result.get("data"):
        return jsonify({
            "success": True,
            "account_name": result["data"].get("account_name", "")
        })

    return jsonify({
        "success": False,
        "message": result.get("message", "Could not verify this account.")
    })