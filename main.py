"""SkillChain Backend - Flask app. Run with:  gunicorn -w 1 --threads 8 "main:create_app()" """
from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from config import SECRET_KEY, DEBUG, ALLOWED_ORIGINS
from security import init_security
from routes import blueprints
from scheduler import start_scheduler


def create_app():
    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024        # proof videos
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)             # Render sits behind one proxy

    # Token auth in the Authorization header: no cookies, so no credentialed CORS.
    CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=False,
         allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
         methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], max_age=86400)
    init_security(app)

    @app.after_request
    def headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    for bp in blueprints:
        app.register_blueprint(bp)
    start_scheduler()
    return app


if __name__ == "__main__":
    create_app().run(debug=DEBUG, host="0.0.0.0", port=5000)
