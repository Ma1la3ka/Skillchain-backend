"""SkillChain Backend - Main Flask Application"""
from flask import Flask
from flask_cors import CORS
from config import (
    SECRET_KEY, DEBUG, SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE, SESSION_COOKIE_HTTPONLY,
    ALLOWED_ORIGINS
)
from database import init_db
from routes import blueprints


def create_app():
    """Create and configure Flask application"""
    app = Flask(__name__)

    # Configuration
    app.secret_key = SECRET_KEY
    app.config["SESSION_COOKIE_SAMESITE"] = SESSION_COOKIE_SAMESITE
    app.config["SESSION_COOKIE_SECURE"] = SESSION_COOKIE_SECURE
    app.config["SESSION_COOKIE_HTTPONLY"] = SESSION_COOKIE_HTTPONLY

    # CORS
    CORS(app, supports_credentials=True, origins=ALLOWED_ORIGINS)

    # No-cache middleware
    @app.after_request
    def no_cache(response):
        """Prevent browser caching"""
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    # Register blueprints
    for bp in blueprints:
        app.register_blueprint(bp)

    # Initialize database
    with app.app_context():
        init_db()

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=DEBUG, host="0.0.0.0", port=5000)
