from dotenv import load_dotenv
load_dotenv()

import os
from flask import Flask
from extensions import db
import models  # Yeh ensure karega ki aapke saare database models load ho jayein

# Ek temporary Flask app instance banayein Supabase connection ke liye
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

with app.app_context():
    db.create_all()
    print("Tables created successfully on Supabase!")