"""SkillChain Backend - Main Flask Application"""
from flask import Flask
from flask_cors import CORS
from config import (
    SECRET_KEY, DEBUG, SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE, SESSION_COOKIE_HTTPONLY,
    ALLOWED_ORIGINS
)

from routes import blueprints
from scheduler import start_scheduler


def create_app():
    """Create and configure Flask application"""
    app = Flask(__name__)


    # Configuration
    app.secret_key = SECRET_KEY
    app.config["SESSION_COOKIE_SAMESITE"] = SESSION_COOKIE_SAMESITE
    app.config["SESSION_COOKIE_SECURE"] = SESSION_COOKIE_SECURE
    app.config["SESSION_COOKIE_HTTPONLY"] = SESSION_COOKIE_HTTPONLY

    # CORS
    CORS(app,
     supports_credentials=True,
     origins=ALLOWED_ORIGINS,
     allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
     expose_headers=["Content-Type"]
)

    @app.before_request
    def handle_preflight():

        from flask import request, Response
        if request.method == "OPTIONS":
            res = Response()
            res.headers["Access-Control-Allow-Origin"]  = request.headers.get("Origin", "*")
            res.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            res.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
            res.headers["Access-Control-Allow-Credentials"] = "true"
            res.headers["Access-Control-Max-Age"] = "86400"
            return res, 200

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

    # Start background job that auto-releases escrow after 24h of no client
    # response on a pending_review job. See scheduler.py for details/caveats
    # around multi-process deployments.
    start_scheduler()

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=DEBUG, host="0.0.0.0", port=5000)