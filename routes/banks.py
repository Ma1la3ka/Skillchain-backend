"""Bank list and account-name lookup (Squad)."""
from flask import Blueprint, request, jsonify
import squad_service

banks_bp = Blueprint('banks', __name__, url_prefix='/api')


@banks_bp.route("/banks", methods=["GET"])
def api_get_banks():
    return jsonify({"success": True, "banks": [{"name": n, "code": c} for n, c in squad_service.BANKS]})


@banks_bp.route("/verify-account", methods=["GET"])
def api_verify_account():
    account_no = request.args.get("account_no", "").strip()
    bank_code = request.args.get("bank_code", "").strip()
    if len(account_no) != 10 or not account_no.isdigit() or bank_code not in squad_service.BANK_BY_CODE:
        return jsonify({"success": False, "message": "Invalid account details."})
    res = squad_service.lookup_account(bank_code, account_no)
    if res["ok"]:
        return jsonify({"success": True, "account_name": res["account_name"]})
    return jsonify({"success": False, "message": res["message"] or "Could not verify this account."})
