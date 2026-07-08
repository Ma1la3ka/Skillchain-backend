"""Configuration module for SkillChain Backend"""
import os
from dotenv import load_dotenv

load_dotenv()

# Environment
ENV = os.getenv("FLASK_ENV", "development")

# Module-level configuration variables
SECRET_KEY = os.getenv("SECRET_KEY", "skillchain-secret-key-change-in-production")
SQUAD_KEY = os.getenv("SQUAD_SECRET_KEY")
DEBUG = os.getenv("FLASK_DEBUG", "True").lower() == "true" if ENV == "development" else False

# Squad API configuration
if SQUAD_KEY and SQUAD_KEY.startswith("sandbox_sk_"):
    SQUAD_BASE_URL = "https://sandbox-api-d.squadco.com"
else:
    SQUAD_BASE_URL = "https://api-d.squadco.com"

# MySQL Database configuration
DB_CONFIG = {
    "host":     os.getenv("DB_HOST"),
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "port":     int(os.getenv("DB_PORT", 3306)),
}

# Session configuration
SESSION_COOKIE_SAMESITE = "None"
SESSION_COOKIE_SECURE = False if ENV == "development" else True
SESSION_COOKIE_HTTPONLY = True
ALLOWED_ORIGINS = ["https://skillchain-frontend-omega.vercel.app", "http://localhost:5501", "http://localhost:5502", ]

# Squad API headers
SQUAD_HEADERS = {
    "Authorization": f"Bearer {SQUAD_KEY}" if SQUAD_KEY else "",
    "Content-Type": "application/json"
}

# Email configuration (MUST be before validation prints)
MAIL_SERVER = "smtp.gmail.com"
MAIL_PORT = 587
MAIL_USE_TLS = True
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "hamzlabs01@gmail.com")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "hello@hamzlabs.com")

# NOW print validation
print(f"\n{'='*60}")
print(f"[CONFIG] Database Configuration:")
print(f"  Host: {DB_CONFIG.get('host')}")
print(f"  Port: {DB_CONFIG.get('port')}")
print(f"  User: {DB_CONFIG.get('user')}")
print(f"  Database: {DB_CONFIG.get('database')}")
print(f"  Password: {'*' * len(DB_CONFIG.get('password', '')) if DB_CONFIG.get('password') else 'NOT SET'}")
print(f"[CONFIG] Mail Configuration:")
print(f"  Server: {MAIL_SERVER}")
print(f"  Port: {MAIL_PORT}")
print(f"  Username: {MAIL_USERNAME}")
print(f"  Sender: {MAIL_DEFAULT_SENDER}")
print(f"{'='*60}\n")

# Check for missing critical config
if not DB_CONFIG.get('host'):
    print("[WARNING] DB_HOST environment variable not set! Using None (will fail)")
if not DB_CONFIG.get('user'):
    print("[WARNING] DB_USER environment variable not set!")
if not DB_CONFIG.get('password'):
    print("[WARNING] DB_PASSWORD environment variable not set!")


class Config:
    """Base configuration class"""
    SECRET_KEY = SECRET_KEY
    SQUAD_KEY = SQUAD_KEY
    SQUAD_BASE_URL = SQUAD_BASE_URL
    DB_CONFIG = DB_CONFIG
    SESSION_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE
    SESSION_COOKIE_SECURE = SESSION_COOKIE_SECURE
    MAIL_SERVER = MAIL_SERVER
    MAIL_PORT = MAIL_PORT
    MAIL_USE_TLS = MAIL_USE_TLS
    MAIL_USERNAME = MAIL_USERNAME
    MAIL_PASSWORD = MAIL_PASSWORD
    MAIL_DEFAULT_SENDER = MAIL_DEFAULT_SENDER
    SESSION_COOKIE_HTTPONLY = SESSION_COOKIE_HTTPONLY
    ALLOWED_ORIGINS = ALLOWED_ORIGINS
    SQUAD_HEADERS = SQUAD_HEADERS


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    TESTING = False


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    SESSION_COOKIE_SECURE = True


class TestingConfig(Config):
    """Testing configuration"""
    TESTING = True
    DEBUG = True


# Configuration selector
config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig
}
