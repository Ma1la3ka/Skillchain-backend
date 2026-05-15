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
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "Ola01/2007"),
    "database": os.getenv("DB_NAME", "skillchain"),
}

# Session configuration
SESSION_COOKIE_SAMESITE = "None"
SESSION_COOKIE_SECURE = False if ENV == "development" else True
SESSION_COOKIE_HTTPONLY = True
ALLOWED_ORIGINS = ["http://127.0.0.1:5501", "http://localhost:5501"]

# Squad API headers
SQUAD_HEADERS = {
    "Authorization": f"Bearer {SQUAD_KEY}" if SQUAD_KEY else "",
    "Content-Type": "application/json"
}


class Config:
    """Base configuration class"""
    SECRET_KEY = SECRET_KEY
    SQUAD_KEY = SQUAD_KEY
    SQUAD_BASE_URL = SQUAD_BASE_URL
    DB_CONFIG = DB_CONFIG
    SESSION_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE
    SESSION_COOKIE_SECURE = SESSION_COOKIE_SECURE
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
