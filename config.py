"""Configuration for SkillChain backend. Everything secret comes from environment variables."""
import os
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()

ENV = os.getenv("FLASK_ENV", "development")
IS_PROD = ENV == "production"

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    if IS_PROD:
        raise RuntimeError("SECRET_KEY must be set in production.")
    SECRET_KEY = "dev-only-secret-do-not-use-in-production"

DEBUG = (not IS_PROD) and os.getenv("FLASK_DEBUG", "false").lower() == "true"
TOKEN_TTL_DAYS = int(os.getenv("TOKEN_TTL_DAYS", "14"))

# ── Squad (payments + payouts) ──────────────────────────────────────────────
SQUAD_SECRET_KEY  = os.getenv("SQUAD_SECRET_KEY", "")
SQUAD_MERCHANT_ID = os.getenv("SQUAD_MERCHANT_ID", "")   # prefix Squad requires on transfer references
SQUAD_BASE_URL = os.getenv(
    "SQUAD_BASE_URL",
    "https://sandbox-api-d.squadco.com" if SQUAD_SECRET_KEY.startswith("sandbox_")
    else "https://api-d.squadco.com",
)
if IS_PROD and not SQUAD_SECRET_KEY:
    raise RuntimeError("SQUAD_SECRET_KEY must be set in production.")

# ── Fees (platform revenue). Change via env, no code edit needed ────────────
CLIENT_FEE_RATE  = Decimal(os.getenv("CLIENT_FEE_RATE",  "0.025"))
ARTISAN_FEE_RATE = Decimal(os.getenv("ARTISAN_FEE_RATE", "0.025"))
MAX_CLIENT_FEE   = Decimal(os.getenv("MAX_CLIENT_FEE",   "1250"))
MAX_ARTISAN_FEE  = Decimal(os.getenv("MAX_ARTISAN_FEE",  "1250"))

# ── Withdrawal limits ───────────────────────────────────────────────────────
MIN_WITHDRAWAL = Decimal(os.getenv("MIN_WITHDRAWAL", "100"))
MAX_WITHDRAWAL = Decimal(os.getenv("MAX_WITHDRAWAL", "500000"))

CLOUDINARY_URL = os.getenv("CLOUDINARY_URL")

DB_CONFIG = {
    "host":     os.getenv("DB_HOST"),
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "port":     int(os.getenv("DB_PORT", 3306)),
}

# ── Frontend / CORS ─────────────────────────────────────────────────────────
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://skillchain-frontend-omega.vercel.app").rstrip("/")
_extra = [o.strip() for o in os.getenv("EXTRA_ALLOWED_ORIGINS", "").split(",") if o.strip()]
ALLOWED_ORIGINS = [
    FRONTEND_URL,
    "https://localhost", "capacitor://localhost", "http://localhost",   # Capacitor (Android / iOS)
    "http://localhost:5501", "http://localhost:5502",                    # local dev
] + _extra

# ── Email ───────────────────────────────────────────────────────────────────
RESEND_API_KEY = os.getenv("RESEND_API_KEY")

if not DB_CONFIG["host"] or not DB_CONFIG["password"]:
    print("[CONFIG] WARNING: DB_HOST / DB_PASSWORD not set.")
