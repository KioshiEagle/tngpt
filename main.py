import os

from flask import Flask

from app.routes import bp

app = Flask(
    __name__,
    template_folder="app/front/templates",
    static_folder="app/front/static",
)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-moi-en-prod")

app.register_blueprint(bp)

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "True").lower() in ["true", "1", "t"]
    app.run(host="127.0.0.1", debug=debug_mode, port=8501)
