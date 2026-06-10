from flask import Flask

from app.routes import bp

app = Flask(__name__, template_folder="app/front/templates", static_folder="app/front/static")
app.secret_key = "change-moi-en-prod"

app.register_blueprint(bp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=8501)
