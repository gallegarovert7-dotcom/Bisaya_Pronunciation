from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_cors import CORS
import whisper
import os
import re
import uuid
import sqlite3
from datetime import datetime
from difflib import SequenceMatcher
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "bisaya tongue pro secret"
CORS(app)


# --- CEBUANO PHONETIC RULES ENGINE ---
class CebuanoLinguisticEngine:
    PHONETIC_MAP = {
        "Pula": {"ipa": "/ˈpu.la/", "tips": ["Roll the 'U' short", "Stress: PU-la"],
                 "common_errors": ["poola", "pola"]},
        "Asul": {"ipa": "/ˈa.sul/", "tips": ["'A' is wide open like 'ah'"], "common_errors": ["azul", "asool"]},
        "Dalag": {"ipa": "/ˈda.lag/", "tips": ["Final 'g' is a hard stop"], "common_errors": ["dalog", "dalak"]},
        "Berde": {"ipa": "/ˈber.de/", "tips": ["'E' sounds like 'eh'"], "common_errors": ["verde", "birde"]},
        "Puti": {"ipa": "/ˈpu.ti/", "tips": ["'I' at the end is short"], "common_errors": ["putee", "puti"]},
        "Iro": {"ipa": "/ˈi.ɾo/", "tips": ["Tapped 'r'"], "common_errors": ["ero"]},
        "Iring": {"ipa": "/ˈi.ɾiŋ/", "tips": ["Nasal 'ng' like 'sing'"], "common_errors": ["eering"]},
        "Manok": {"ipa": "/ˈma.nok/", "tips": ["Final 'k' is glottal stop"], "common_errors": ["manoc"]},
        "Baka": {"ipa": "/ˈba.ka/", "tips": ["Balanced BA-ka"], "common_errors": ["baca"]},
        "Baboy": {"ipa": "/ˈba.boj/", "tips": ["'oy' rhymes with 'boy'"], "common_errors": ["babuy"]},
        "Ulo": {"ipa": "/ˈu.lo/", "tips": ["'U' as in 'food'"], "common_errors": ["olo"]},
        "Kamot": {"ipa": "/ˈka.mot/", "tips": ["Final 't' is soft stop"], "common_errors": ["kamut"]},
        "Tuhod": {"ipa": "/ˈtu.hod/", "tips": ["'H' is breathy"], "common_errors": ["tohod"]},
        "Sapa": {"ipa": "/ˈsa.pa/", "tips": ["Pure 'a' vowels"], "common_errors": ["sappa"]},
        "Dunggan": {"ipa": "/ˈduŋ.gan/", "tips": ["Middle 'ng' is nasal"], "common_errors": ["dungan"]},
    }

    @staticmethod
    def normalize_bisaya(text):
        text = text.lower().strip().replace('.', '').replace('?', '').replace(',', '')
        text = re.sub(r'[ou]', '(ou)', text)
        text = re.sub(r'[ei]', '(ei)', text)
        return text

    @staticmethod
    def get_phonetic_info(word):
        return CebuanoLinguisticEngine.PHONETIC_MAP.get(word,
                                                        {"ipa": "", "tips": ["Speak clearly"], "common_errors": []})


# --- DATABASE LOGIC ---
def get_db():
    conn = sqlite3.connect('Cebuano_Pronunciation.db')
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS users
                    (
                        id
                        INTEGER
                        PRIMARY
                        KEY
                        AUTOINCREMENT,
                        username
                        TEXT
                        UNIQUE
                        NOT
                        NULL,
                        password
                        TEXT
                        NOT
                        NULL,
                        role
                        TEXT
                        DEFAULT
                        "student"
                    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS user_scores
                    (
                        id
                        INTEGER
                        PRIMARY
                        KEY
                        AUTOINCREMENT,
                        user_id
                        INTEGER,
                        word
                        TEXT,
                        level
                        TEXT,
                        accuracy
                        REAL,
                        timestamp
                        DATETIME
                    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS test_results
                    (
                        id
                        INTEGER
                        PRIMARY
                        KEY
                        AUTOINCREMENT,
                        user_id
                        INTEGER,
                        level
                        TEXT,
                        test_type
                        TEXT,
                        word
                        TEXT,
                        accuracy
                        REAL,
                        timestamp
                        DATETIME
                    )''')

    admin_exists = conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone()
    if not admin_exists:
        conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                     ('admin', generate_password_hash("adminko"), 'admin'))
    conn.commit()
    conn.close()


init_db()
model = whisper.load_model("tiny")


# --- ROUTES ---
@app.route('/')
def login_page():
    return render_template('login.html')


@app.route('/Student.html')
def student_lab():
    if 'user_id' not in session or session.get('role') != 'student':
        return redirect(url_for('login_page'))
    return render_template('Student.html', user=session['username'])


@app.route('/admin.html')
def admin_panel():
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login_page'))
    return render_template('admin.html', user=session['username'])


# --- AUTH API ---
@app.route('/auth/login', methods=['POST'])
def login():
    data = request.json
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (data.get('username'),)).fetchone()
    conn.close()
    if user and check_password_hash(user['password'], data.get('password')):
        session['user_id'] = user['id'];
        session['username'] = user['username'];
        session['role'] = user['role']
        return jsonify({"success": True, "role": user['role'], "username": user['username']})
    return jsonify({"success": False, "error": "Invalid credentials"}), 401


@app.route('/auth/register', methods=['POST'])
def register():
    data = request.json
    try:
        conn = get_db()
        conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                     (data.get('username'), generate_password_hash(data.get('password')), 'student'))
        conn.commit();
        conn.close()
        return jsonify({"success": True})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "error": "Username taken"}), 400


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))


# --- ANALYSIS API ---
@app.route('/analyze', methods=['POST'])
def analyze_audio():
    if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
    target_word = request.form.get('target', '');
    level_name = request.form.get('level', 'Foundation')
    test_type = request.form.get('test_type', None);
    audio_file = request.files['audio']
    filename = f"temp_{uuid.uuid4()}.wav";
    audio_file.save(filename)
    try:
        result = model.transcribe(filename, language="tl", fp16=False)
        detected = result['text'].strip().lower().replace('.', '')
        norm_detected = CebuanoLinguisticEngine.normalize_bisaya(detected)
        norm_target = CebuanoLinguisticEngine.normalize_bisaya(target_word)
        score = SequenceMatcher(None, norm_detected, norm_target).ratio()
        accuracy = round(score * 100, 2)
        conn = get_db()
        if test_type:
            conn.execute(
                'INSERT INTO test_results (user_id, level, test_type, word, accuracy, timestamp) VALUES (?, ?, ?, ?, ?, ?)',
                (session['user_id'], level_name, test_type, target_word, accuracy, datetime.now()))
        else:
            conn.execute('INSERT INTO user_scores (user_id, word, level, accuracy, timestamp) VALUES (?, ?, ?, ?, ?)',
                         (session['user_id'], target_word, level_name, accuracy, datetime.now()))
        conn.commit();
        conn.close()
        return jsonify({"detected": detected, "accuracy": accuracy, "success": score >= 0.75,
                        "phonetic": CebuanoLinguisticEngine.get_phonetic_info(target_word)})
    finally:
        if os.path.exists(filename): os.remove(filename)


# --- UPDATED ADMIN API ---
@app.route('/get_admin_summary')
def get_admin_summary():
    if session.get('role') != 'admin': return jsonify([])
    conn = get_db()
    # Pulls average from both Practice AND Test tables
    rows = conn.execute('''
                        SELECT u.username, u.id, AVG(all_scores.accuracy) as avg_accuracy
                        FROM users u
                                 LEFT JOIN (SELECT user_id, accuracy
                                            FROM user_scores
                                            UNION ALL
                                            SELECT user_id, accuracy
                                            FROM test_results) as all_scores ON u.id = all_scores.user_id
                        WHERE u.role = "student"
                        GROUP BY u.id
                        ''').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/get_student_details/<int:student_id>')
def get_student_details(student_id):
    if session.get('role') != 'admin': return jsonify({"error": "Unauthorized"}), 403
    conn = get_db()
    # Combined query for full history
    details = conn.execute('''
                           SELECT word, level, accuracy, timestamp, 'Practice' as test_type
                           FROM user_scores
                           WHERE user_id = ?
                           UNION ALL
                           SELECT word, level, accuracy, timestamp, test_type
                           FROM test_results
                           WHERE user_id = ?
                           ORDER BY timestamp DESC
                           ''', (student_id, student_id)).fetchall()
    conn.close()
    return jsonify([dict(d) for d in details])


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
#
# from flask import Flask, request, jsonify, render_template, session, redirect, url_for
# from flask_cors import CORS
# import whisper
# import os
# import re
# import uuid
# import sqlite3
# from datetime import datetime
# from difflib import SequenceMatcher
# from werkzeug.security import generate_password_hash, check_password_hash
#
# app = Flask(__name__)
# app.secret_key = "bisaya tongue pro secret"
# CORS(app)
#
#
# # --- CEBUANO PHONETIC RULES ENGINE ---
# class CebuanoLinguisticEngine:
#     PHONETIC_MAP = {
#         "Pula": {"ipa": "/ˈpu.la/", "tips": ["Roll the 'U' short", "Stress: PU-la"],
#                  "common_errors": ["poola", "pola"]},
#         "Asul": {"ipa": "/ˈa.sul/", "tips": ["'A' is wide open like 'ah'"], "common_errors": ["azul", "asool"]},
#         "Dalag": {"ipa": "/ˈda.lag/", "tips": ["Final 'g' is a hard stop"], "common_errors": ["dalog", "dalak"]},
#         "Berde": {"ipa": "/ˈber.de/", "tips": ["'E' sounds like 'eh'"], "common_errors": ["verde", "birde"]},
#         "Puti": {"ipa": "/ˈpu.ti/", "tips": ["'I' at the end is short"], "common_errors": ["putee", "puti"]},
#         "Iro": {"ipa": "/ˈi.ɾo/", "tips": ["Tapped 'r'"], "common_errors": ["ero"]},
#         "Iring": {"ipa": "/ˈi.ɾiŋ/", "tips": ["Nasal 'ng' like 'sing'"], "common_errors": ["eering"]},
#         "Manok": {"ipa": "/ˈma.nok/", "tips": ["Final 'k' is glottal stop"], "common_errors": ["manoc"]},
#         "Baka": {"ipa": "/ˈba.ka/", "tips": ["Balanced BA-ka"], "common_errors": ["baca"]},
#         "Baboy": {"ipa": "/ˈba.boj/", "tips": ["'oy' rhymes with 'boy'"], "common_errors": ["babuy"]},
#         "Ulo": {"ipa": "/ˈu.lo/", "tips": ["'U' as in 'food'"], "common_errors": ["olo"]},
#         "Kamot": {"ipa": "/ˈka.mot/", "tips": ["Final 't' is soft stop"], "common_errors": ["kamut"]},
#         "Tuhod": {"ipa": "/ˈtu.hod/", "tips": ["'H' is breathy"], "common_errors": ["tohod"]},
#         "Sapa": {"ipa": "/ˈsa.pa/", "tips": ["Pure 'a' vowels"], "common_errors": ["sappa"]},
#         "Dunggan": {"ipa": "/ˈduŋ.gan/", "tips": ["Middle 'ng' is nasal"], "common_errors": ["dungan"]},
#     }
#
#     RULES = [
#         {"rule": "Vowel Purity", "desc": "Cebuano has 3 pure vowels: A, I, U."},
#         {"rule": "Glottal Stops", "desc": "Clip final K, T, D sounds short."},
#         {"rule": "Tapped R", "desc": "The 'R' is a single tap."},
#         {"rule": "NG Cluster", "desc": "'NG' is a single nasal sound."},
#     ]
#
#     @staticmethod
#     def normalize_bisaya(text):
#         text = text.lower().strip().replace('.', '').replace('?', '').replace(',', '')
#         text = re.sub(r'[ou]', '(ou)', text)
#         text = re.sub(r'[ei]', '(ei)', text)
#         return text
#
#     @staticmethod
#     def get_phonetic_info(word):
#         return CebuanoLinguisticEngine.PHONETIC_MAP.get(word, {
#             "ipa": "", "tips": ["Speak clearly"], "common_errors": []
#         })
#
#
# # --- DATABASE LOGIC ---
# def get_db():
#     conn = sqlite3.connect('Cebuano_Pronunciation.db')
#     conn.row_factory = sqlite3.Row
#     return conn
#
#
# def init_db():
#     conn = get_db()
#     conn.execute('''CREATE TABLE IF NOT EXISTS users
#                     (
#                         id
#                         INTEGER
#                         PRIMARY
#                         KEY
#                         AUTOINCREMENT,
#                         username
#                         TEXT
#                         UNIQUE
#                         NOT
#                         NULL,
#                         password
#                         TEXT
#                         NOT
#                         NULL,
#                         role
#                         TEXT
#                         DEFAULT
#                         "student"
#                     )''')
#     conn.execute('''CREATE TABLE IF NOT EXISTS user_scores
#                     (
#                         id
#                         INTEGER
#                         PRIMARY
#                         KEY
#                         AUTOINCREMENT,
#                         user_id
#                         INTEGER,
#                         word
#                         TEXT,
#                         level
#                         TEXT,
#                         accuracy
#                         REAL,
#                         timestamp
#                         DATETIME
#                     )''')
#     conn.execute('''CREATE TABLE IF NOT EXISTS test_results
#                     (
#                         id
#                         INTEGER
#                         PRIMARY
#                         KEY
#                         AUTOINCREMENT,
#                         user_id
#                         INTEGER,
#                         level
#                         TEXT,
#                         test_type
#                         TEXT,
#                         word
#                         TEXT,
#                         accuracy
#                         REAL,
#                         timestamp
#                         DATETIME
#                     )''')
#
#     # Set your specific Admin credentials
#     admin_exists = conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone()
#     if not admin_exists:
#         conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
#                      ('admin', generate_password_hash("adminko"), 'admin'))
#         print("Admin account initialized: admin / adminko")
#
#     conn.commit()
#     conn.close()
#
#
# init_db()
#
# print("Initializing AI Engine (Whisper)...")
# model = whisper.load_model("tiny")
#
#
# # --- NAVIGATION ROUTES ---
#
# @app.route('/')
# def login_page():
#     return render_template('login.html')
#
#
# @app.route('/Student.html')
# def student_lab():
#     if 'user_id' not in session or session.get('role') != 'student':
#         return redirect(url_for('login_page'))
#     return render_template('Student.html', user=session['username'])
#
#
# @app.route('/admin.html')
# def admin_panel():
#     if 'user_id' not in session or session.get('role') != 'admin':
#         return redirect(url_for('login_page'))
#     return render_template('admin.html', user=session['username'])
#
#
# # --- AUTH API ---
#
# @app.route('/auth/login', methods=['POST'])
# def login():
#     data = request.json
#     username = data.get('username')
#     password = data.get('password')
#
#     conn = get_db()
#     user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
#     conn.close()
#
#     if user and check_password_hash(user['password'], password):
#         session['user_id'] = user['id']
#         session['username'] = user['username']
#         session['role'] = user['role']
#
#         return jsonify({
#             "success": True,
#             "role": user['role'],
#             "username": user['username']
#         })
#
#     return jsonify({"success": False, "error": "Invalid username or password"}), 401
#
#
# @app.route('/auth/register', methods=['POST'])
# def register():
#     data = request.json
#     username = data.get('username')
#     password = data.get('password')
#
#     if not username or not password:
#         return jsonify({"success": False, "error": "Missing fields"}), 400
#
#     try:
#         conn = get_db()
#         conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
#                      (username, generate_password_hash(password), 'student'))
#         conn.commit()
#         conn.close()
#         return jsonify({"success": True})
#     except sqlite3.IntegrityError:
#         return jsonify({"success": False, "error": "Username already exists"}), 400
#
#
# @app.route('/logout')
# def logout():
#     session.clear()
#     return redirect(url_for('login_page'))
#
#
# # --- AI ANALYSIS ---
#
# @app.route('/analyze', methods=['POST'])
# def analyze_audio():
#     if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
#
#     target_word = request.form.get('target', '')
#     level_name = request.form.get('level', 'Foundation')
#     test_type = request.form.get('test_type', None)
#     audio_file = request.files['audio']
#     filename = f"temp_{uuid.uuid4()}.wav"
#     audio_file.save(filename)
#
#     try:
#         result = model.transcribe(filename, language="tl", fp16=False)
#         detected = result['text'].strip().lower().replace('.', '')
#
#         norm_detected = CebuanoLinguisticEngine.normalize_bisaya(detected)
#         norm_target = CebuanoLinguisticEngine.normalize_bisaya(target_word)
#
#         score = SequenceMatcher(None, norm_detected, norm_target).ratio()
#         accuracy = round(score * 100, 2)
#
#         conn = get_db()
#         table = 'test_results' if test_type else 'user_scores'
#         if test_type:
#             conn.execute(
#                 f'INSERT INTO {table} (user_id, level, test_type, word, accuracy, timestamp) VALUES (?, ?, ?, ?, ?, ?)',
#                 (session['user_id'], level_name, test_type, target_word, accuracy, datetime.now()))
#         else:
#             conn.execute(f'INSERT INTO {table} (user_id, word, level, accuracy, timestamp) VALUES (?, ?, ?, ?, ?)',
#                          (session['user_id'], target_word, level_name, accuracy, datetime.now()))
#         conn.commit()
#         conn.close()
#
#         return jsonify({
#             "detected": detected, "accuracy": accuracy,
#             "success": score >= 0.75, "phonetic": CebuanoLinguisticEngine.get_phonetic_info(target_word)
#         })
#     finally:
#         if os.path.exists(filename): os.remove(filename)
#
#
# # --- ADMIN UTILS ---
#
# @app.route('/get_admin_summary')
# def get_admin_summary():
#     if session.get('role') != 'admin': return jsonify([])
#     conn = get_db()
#     rows = conn.execute('''
#                         SELECT u.username, u.id, AVG(s.accuracy) as avg_accuracy
#                         FROM users u
#                                  LEFT JOIN user_scores s ON u.id = s.user_id
#                         WHERE u.role = "student"
#                         GROUP BY u.id
#                         ''').fetchall()
#     conn.close()
#     return jsonify([dict(r) for r in rows])
#
#
# if __name__ == '__main__':
#     app.run(host='0.0.0.0', port=5000, debug=True)



#
#
# from flask import Flask, request, jsonify, render_template, session, redirect, url_for
# from flask_cors import CORS
# import whisper
# import os
# import re
# import uuid
# import sqlite3
# from datetime import datetime
# from difflib import SequenceMatcher
# from werkzeug.security import generate_password_hash, check_password_hash
#
# app = Flask(__name__)
# app.secret_key = "bisaya tongue"
# CORS(app)
#
#
# # --- CEBUANO PHONETIC RULES ENGINE ---
# class CebuanoLinguisticEngine:
#     PHONETIC_MAP = {
#         "Pula": {"ipa": "/ˈpu.la/", "tips": ["Roll the 'U' short", "Stress: PU-la"],
#                  "common_errors": ["poola", "pola"]},
#         "Asul": {"ipa": "/ˈa.sul/", "tips": ["'A' is wide open like 'ah'"], "common_errors": ["azul", "asool"]},
#         "Dalag": {"ipa": "/ˈda.lag/", "tips": ["Final 'g' is a hard stop"], "common_errors": ["dalog", "dalak"]},
#         "Berde": {"ipa": "/ˈber.de/", "tips": ["'E' sounds like 'eh'"], "common_errors": ["verde", "birde"]},
#         "Puti": {"ipa": "/ˈpu.ti/", "tips": ["'I' at the end is short"], "common_errors": ["putee", "pooti"]},
#         "Iro": {"ipa": "/ˈi.ɾo/", "tips": ["Tapped 'r'"], "common_errors": ["ero"]},
#         "Iring": {"ipa": "/ˈi.ɾiŋ/", "tips": ["Nasal 'ng' like 'sing'"], "common_errors": ["eering"]},
#         "Manok": {"ipa": "/ˈma.nok/", "tips": ["Final 'k' is glottal stop"], "common_errors": ["manoc"]},
#         "Baka": {"ipa": "/ˈba.ka/", "tips": ["Balanced BA-ka"], "common_errors": ["baca"]},
#         "Baboy": {"ipa": "/ˈba.boj/", "tips": ["'oy' rhymes with 'boy'"], "common_errors": ["babuy"]},
#         "Ulo": {"ipa": "/ˈu.lo/", "tips": ["'U' as in 'food'"], "common_errors": ["olo"]},
#         "Kamot": {"ipa": "/ˈka.mot/", "tips": ["Final 't' is soft stop"], "common_errors": ["kamut"]},
#         "Tuhod": {"ipa": "/ˈtu.hod/", "tips": ["'H' is breathy"], "common_errors": ["tohod"]},
#         "Sapa": {"ipa": "/ˈsa.pa/", "tips": ["Pure 'a' vowels"], "common_errors": ["sappa"]},
#         "Dunggan": {"ipa": "/ˈduŋ.gan/", "tips": ["Middle 'ng' is nasal"], "common_errors": ["dungan"]},
#     }
#
#     RULES = [
#         {"rule": "Vowel Purity", "desc": "Cebuano has 3 pure vowels: A, I, U."},
#         {"rule": "Glottal Stops", "desc": "Clip final K, T, D sounds short."},
#         {"rule": "Tapped R", "desc": "The 'R' is a single tap."},
#         {"rule": "NG Cluster", "desc": "'NG' is a single nasal sound."},
#     ]
#
#     @staticmethod
#     def normalize_bisaya(text):
#         text = text.lower().strip().replace('.', '').replace('?', '').replace(',', '')
#         text = re.sub(r'[ou]', '(ou)', text)
#         text = re.sub(r'[ei]', '(ei)', text)
#         return text
#
#     @staticmethod
#     def get_phonetic_info(word):
#         return CebuanoLinguisticEngine.PHONETIC_MAP.get(word, {
#             "ipa": "", "tips": ["Speak clearly"], "common_errors": []
#         })
#
#
# # --- DATABASE LOGIC ---
# def get_db():
#     conn = sqlite3.connect('Cebuano_Pronunciation.db')
#     conn.row_factory = sqlite3.Row
#     return conn
#
#
# def init_db():
#     conn = get_db()
#     conn.execute(
#         'CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT DEFAULT "student")')
#     conn.execute(
#         'CREATE TABLE IF NOT EXISTS user_scores (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, word TEXT, level TEXT, accuracy REAL, timestamp DATETIME)')
#     conn.execute(
#         'CREATE TABLE IF NOT EXISTS test_results (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, level TEXT, test_type TEXT, word TEXT, accuracy REAL, timestamp DATETIME)')
#
#     admin_exists = conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone()
#     if not admin_exists:
#         conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
#                      ('admin', generate_password_hash("admin123"), 'admin'))
#     conn.commit()
#     conn.close()
#
#
# init_db()
# model = whisper.load_model("tiny")
#
#
# # --- PAGE NAVIGATION ROUTES ---
#
# @app.route('/')
# def login_page():
#     return render_template('login.html')
#
#
# @app.route('/Student.html')
# def student_lab():
#     if 'user_id' not in session or session.get('role') != 'student':
#         return redirect(url_for('login_page'))
#     return render_template('Student.html', user=session['username'])
#
#
# @app.route('/admin.html')
# def admin_panel():
#     if 'user_id' not in session or session.get('role') != 'admin':
#         return redirect(url_for('login_page'))
#     return render_template('admin.html', user=session['username'])
#
#
# # --- AUTH API ROUTES ---
#
# @app.route('/auth/login', methods=['POST'])
# def login():
#     data = request.json
#     username = data.get('username')
#     password = data.get('password')
#
#     conn = get_db()
#     user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
#     conn.close()
#
#     if user and check_password_hash(user['password'], password):
#         session['user_id'] = user['id']
#         session['username'] = user['username']
#         session['role'] = user['role']
#
#         return jsonify({
#             "success": True,
#             "role": user['role'],
#             "username": user['username']
#         })
#
#     return jsonify({"success": False, "error": "Invalid username or password"}), 401
#
#
# @app.route('/auth/register', methods=['POST'])
# def register():
#     data = request.json
#     username = data.get('username')
#     password = data.get('password')
#
#     if not username or not password:
#         return jsonify({"success": False, "error": "Missing fields"}), 400
#
#     try:
#         conn = get_db()
#         conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
#                      (username, generate_password_hash(password), 'student'))
#         conn.commit()
#         conn.close()
#         return jsonify({"success": True})
#     except sqlite3.IntegrityError:
#         return jsonify({"success": False, "error": "Username taken"}), 400
#
#
# @app.route('/logout')
# def logout():
#     session.clear()
#     return redirect(url_for('login_page'))
#
#
# # --- AI & DATA ROUTES ---
#
# @app.route('/analyze', methods=['POST'])
# def analyze_audio():
#     if 'user_id' not in session: return jsonify({"error": "Unauthorized"}), 401
#
#     target_word = request.form.get('target', '')
#     level_name = request.form.get('level', 'Foundation')
#     test_type = request.form.get('test_type', None)
#     audio_file = request.files['audio']
#     filename = f"temp_{uuid.uuid4()}.wav"
#     audio_file.save(filename)
#
#     try:
#         result = model.transcribe(filename, language="tl", fp16=False)
#         detected = result['text'].strip().lower().replace('.', '')
#
#         norm_detected = CebuanoLinguisticEngine.normalize_bisaya(detected)
#         norm_target = CebuanoLinguisticEngine.normalize_bisaya(target_word)
#
#         score = SequenceMatcher(None, norm_detected, norm_target).ratio()
#         accuracy = round(score * 100, 2)
#
#         conn = get_db()
#         table = 'test_results' if test_type else 'user_scores'
#         if test_type:
#             conn.execute(
#                 f'INSERT INTO {table} (user_id, level, test_type, word, accuracy, timestamp) VALUES (?, ?, ?, ?, ?, ?)',
#                 (session['user_id'], level_name, test_type, target_word, accuracy, datetime.now()))
#         else:
#             conn.execute(f'INSERT INTO {table} (user_id, word, level, accuracy, timestamp) VALUES (?, ?, ?, ?, ?)',
#                          (session['user_id'], target_word, level_name, accuracy, datetime.now()))
#         conn.commit()
#         conn.close()
#
#         return jsonify({
#             "detected": detected, "accuracy": accuracy,
#             "success": score >= 0.75, "phonetic": CebuanoLinguisticEngine.get_phonetic_info(target_word)
#         })
#     finally:
#         if os.path.exists(filename): os.remove(filename)
#
#
# @app.route('/get_admin_summary')
# def get_admin_summary():
#     if session.get('role') != 'admin': return jsonify([])
#     conn = get_db()
#     rows = conn.execute(
#         'SELECT u.username, u.id, AVG(s.accuracy) as avg_accuracy FROM users u LEFT JOIN user_scores s ON u.id = s.user_id WHERE u.role = "student" GROUP BY u.id').fetchall()
#     conn.close()
#     return jsonify([dict(r) for r in rows])
#
#
# if __name__ == '__main__':
#     app.run(host='0.0.0.0', port=5000, debug=True)