"""
ArtikelTrainer — Flask-застосунок для вивчення артиклів німецької мови
Архітектура: MVC, Flask + SQLite + SQLAlchemy + Jinja2
"""

import re
import random
from datetime import date, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash


# НАЛАШТУВАННЯ ЗАСТОСУНКУ

app = Flask(__name__)
app.config['SECRET_KEY'] = 'artikel-trainer-secret-2025'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///artikel.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)



# МОДЕЛІ (MODEL — рівень даних)


class User(db.Model):
    """Обліковий запис користувача"""
    __tablename__ = 'users'

    id       = db.Column(db.Integer, primary_key=True)
    login    = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    streak   = db.Column(db.Integer, default=0)           # серія днів
    last_day = db.Column(db.Date, default=date.today)

    progress = db.relationship('Progress', backref='user', lazy=True)


class Word(db.Model):
    """Слово зі словникової бази"""
    __tablename__ = 'words'

    id       = db.Column(db.Integer, primary_key=True)
    article  = db.Column(db.String(3), nullable=False)    # der / die / das
    german   = db.Column(db.String(100), nullable=False)  # іменник
    ukrainian= db.Column(db.String(100), nullable=False)  # переклад
    category = db.Column(db.String(50), nullable=False)   # категорія
    suffix   = db.Column(db.String(20), nullable=True)    # суфікс (для підказки)
    hint     = db.Column(db.String(200), nullable=True)   # текст підказки

    progress = db.relationship('Progress', backref='word', lazy=True)


class Progress(db.Model):
    """Статистика відповідей — один рядок на пару (user, word)"""
    __tablename__ = 'progress'

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    word_id    = db.Column(db.Integer, db.ForeignKey('words.id'), nullable=False)
    correct    = db.Column(db.Integer, default=0, nullable=False)   # правильних відповідей
    total      = db.Column(db.Integer, default=0, nullable=False)   # всього показів
    last_seen  = db.Column(db.Date, default=date.today)



# АЛГОРИТМ ПІДБОРУ ЗАВДАНЬ (адаптивна вага)

def get_next_word(user_id):
    """
    Адаптивний вибір слова за зваженим алгоритмом:
    - нові слова (ще не з'являлись) → висока вага
    - слова з помилками → висока вага
    - давно не переглянуті → підвищена вага
    """
    all_words = Word.query.all()
    weights   = []

    today = date.today()
    for w in all_words:
        prog = Progress.query.filter_by(user_id=user_id, word_id=w.id).first()

        if prog is None:
            # Нове слово — пріоритет
            weight = 10.0
        else:
            # Коефіцієнт успішності
            success_rate = prog.correct / prog.total if prog.total > 0 else 0
            # Дні без повторення
            days_ago = (today - prog.last_seen).days if prog.last_seen else 7
            # Формула ваги: низька точність + давно не бачили = вища вага
            weight = (1.0 - success_rate) * 5 + min(days_ago, 7) * 0.5 + 0.5

        weights.append(weight)

    # Зважений випадковий вибір
    chosen = random.choices(all_words, weights=weights, k=1)[0]
    return chosen


def update_progress(user_id, word_id, is_correct):
    """Оновити статистику після відповіді"""
    prog = Progress.query.filter_by(user_id=user_id, word_id=word_id).first()
    if prog is None:
        prog = Progress(user_id=user_id, word_id=word_id, total=0, correct=0)
        db.session.add(prog)

    # Запобіжник для старих записів бази даних, де значення можуть бути NULL
    if prog.total is None:
        prog.total = 0
    if prog.correct is None:
        prog.correct = 0

    prog.total    += 1
    prog.last_seen = date.today()
    if is_correct:
        prog.correct += 1
    db.session.commit()


def update_streak(user):
    """Оновити серію днів"""
    today = date.today()
    if user.last_day == today:
        return  # вже відзначено сьогодні
    if user.last_day == today - timedelta(days=1):
        user.streak += 1
    else:
        user.streak = 1
    user.last_day = today
    db.session.commit()


def get_accuracy(user_id):
    """Загальна точність відповідей користувача, %"""
    records = Progress.query.filter_by(user_id=user_id).all()
    if not records:
        return None
    total   = sum(r.total   for r in records)
    correct = sum(r.correct for r in records)
    return round(correct / total * 100) if total > 0 else None


def get_learned_count(user_id):
    """Кількість слів з точністю ≥ 80%"""
    records = Progress.query.filter_by(user_id=user_id).all()
    return sum(1 for r in records if r.total > 0 and r.correct / r.total >= 0.8)



# ДОПОМІЖНІ ФУНКЦІЇ

def login_required(f):
    """Декоратор: перевірка авторизації"""
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            flash('Будь ласка, увійдіть до акаунту.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def detect_hint(german, article):
    """Автоматична підказка на основі суфіксів (регулярні вирази)"""
    w = german.lower()
    rules = [
        # die
        (r'ung$',   'Суфікс -ung → завжди die'),
        (r'heit$',  'Суфікс -heit → завжди die'),
        (r'keit$',  'Суфікс -keit → завжди die'),
        (r'tion$',  'Суфікс -tion → завжди die'),
        (r'schaft$','Суфікс -schaft → завжди die'),
        (r'e$',     'Суфікс -e → часто die'),
        # das
        (r'chen$',  'Суфікс -chen → завжди das'),
        (r'lein$',  'Суфікс -lein → завжди das'),
        (r'ment$',  'Суфікс -ment → часто das'),
        (r'um$',    'Суфікс -um → часто das'),
        # der
        (r'er$',    'Суфікс -er → часто der'),
        (r'el$',    'Суфікс -el → часто der'),
        (r'ismus$', 'Суфікс -ismus → завжди der'),
        (r'or$',    'Суфікс -or → часто der'),
    ]
    for pattern, hint in rules:
        if re.search(pattern, w):
            return hint
    return f'Слово «{german}» — {article} (вивчіть як виняток)'



# МАРШРУТИ — АВТОРИЗАЦІЯ (CONTROLLER)


@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('progress'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_val = request.form.get('login', '').strip()
        password  = request.form.get('password', '')
        user = User.query.filter_by(login=login_val).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['login']   = user.login
            update_streak(user)
            return redirect(url_for('progress'))
        flash('Невірний логін або пароль.', 'error')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        login_val = request.form.get('login', '').strip()
        password  = request.form.get('password', '')
        password2 = request.form.get('password2', '')

        if not login_val or not password:
            flash('Заповніть усі поля.', 'error')
        elif len(password) < 4:
            flash('Пароль мінімум 4 символи.', 'error')
        elif password != password2:
            flash('Паролі не збігаються.', 'error')
        elif User.query.filter_by(login=login_val).first():
            flash('Цей логін вже зайнятий.', 'error')
        else:
            user = User(
                login    = login_val,
                password = generate_password_hash(password),
                streak   = 1,
                last_day = date.today()
            )
            db.session.add(user)
            db.session.commit()
            session['user_id'] = user.id
            session['login']   = user.login
            flash('Акаунт створено! Ласкаво просимо.', 'success')
            return redirect(url_for('progress'))
    return render_template('register.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))



# МАРШРУТИ — ОСНОВНІ ЕКРАНИ


@app.route('/progress')
@login_required
def progress():
    user_id = session['user_id']
    user    = db.session.get(User, user_id)
    accuracy = get_accuracy(user_id)
    learned  = get_learned_count(user_id)
    total_words = Word.query.count()
    pct = round(learned / total_words * 100) if total_words else 0
    return render_template('progress.html',
        user=user, accuracy=accuracy,
        learned=learned, total_words=total_words, pct=pct
    )


@app.route('/train', methods=['GET', 'POST'])
@login_required
def train():
    user_id = session['user_id']
    feedback = None
    word     = None

    if request.method == 'POST':
        word_id  = int(request.form.get('word_id'))
        chosen   = request.form.get('chosen', '')
        word     = db.session.get(Word, word_id)
        correct  = (chosen == word.article)
        update_progress(user_id, word_id, correct)

        if correct:
            feedback = {'type': 'ok', 'title': '✓ Правильно!',
                        'body': f'{word.article} {word.german} — {word.ukrainian}'}
        else:
            hint = word.hint or detect_hint(word.german, word.article)
            feedback = {'type': 'err',
                        'title': f'✗ Правильно: {word.article} {word.german}',
                        'body': f'💡 {hint}'}
        # Після відповіді обираємо нове слово
        word = get_next_word(user_id)
    else:
        word = get_next_word(user_id)

    prog = Progress.query.filter_by(user_id=user_id, word_id=word.id).first()
    total_words = Word.query.count()
    learned     = get_learned_count(user_id)
    pct = round(learned / total_words * 100) if total_words else 0

    return render_template('train.html',
        word=word, feedback=feedback,
        learned=learned, total_words=total_words, pct=pct
    )


@app.route('/dictionary')
@login_required
def dictionary():
    article  = request.args.get('article', '')
    category = request.args.get('category', '')
    search   = request.args.get('search', '').strip()

    query = Word.query
    if article in ('der', 'die', 'das'):
        query = query.filter_by(article=article)
    if category:
        query = query.filter_by(category=category)
    if search:
        query = query.filter(
            db.or_(
                Word.german.ilike(f'%{search}%'),
                Word.ukrainian.ilike(f'%{search}%')
            )
        )

    words      = query.order_by(Word.category, Word.german).all()
    categories = [r[0] for r in db.session.query(Word.category).distinct().order_by(Word.category)]

    return render_template('dictionary.html',
        words=words, categories=categories,
        selected_article=article,
        selected_category=category,
        search=search
    )


# ІНІЦІАЛІЗАЦІЯ БАЗИ ТА ЗАПОВНЕННЯ СЛОВНИКА


WORD_DATA = [
    # (article, german, ukrainian, category, suffix, hint)
    # ── Їжа ──────────────────────────────────────────
    ('der','Apfel','яблуко','Їжа','-el','Суфікс -el → часто der'),
    ('die','Banane','банан','Їжа','-e','Суфікс -e → часто die'),
    ('die','Karotte','морква','Їжа','-e','Суфікс -e → часто die'),
    ('das','Brot','хліб','Їжа','–','Brot — середній рід, виняток'),
    ('die','Suppe','суп','Їжа','-e','Суфікс -e → часто die'),
    ('der','Käse','сир','Їжа','–','Käse — чоловічий рід'),
    ('das','Ei','яйце','Їжа','–','Ei — завжди das'),
    ('die','Tomate','помідор','Їжа','-e','Суфікс -e → часто die'),
    ('der','Kaffee','кава','Їжа','–','Kaffee — чоловічий рід'),
    ('das','Wasser','вода','Їжа','–','Wasser — das, виняток'),
    ('der','Tee','чай','Їжа','–','Напої чол. роду → der'),
    ('die','Milch','молоко','Їжа','–','Milch — жіночий рід'),
    ('das','Fleisch','м\'ясо','Їжа','–','Fleisch — das'),
    ('der','Fisch','риба (їжа)','Їжа','–','Fisch — чоловічий рід'),
    ('die','Butter','масло','Їжа','–','Butter — жіночий рід'),
    # ── Тварини ───────────────────────────────────────
    ('der','Hund','собака','Тварини','–','Самці тварин → der'),
    ('die','Katze','кішка','Тварини','-e','Суфікс -e → часто die'),
    ('der','Vogel','птах','Тварини','-el','Суфікс -el → часто der'),
    ('das','Pferd','кінь','Тварини','–','Pferd — das'),
    ('die','Maus','миша','Тварини','–','Maus — жіночий рід'),
    ('der','Bär','ведмідь','Тварини','–','Bär — чоловічий рід'),
    ('die','Schlange','змія','Тварини','-e','Суфікс -e → часто die'),
    ('das','Schwein','свиня','Тварини','–','Schwein — das'),
    ('die','Kuh','корова','Тварини','–','Кuh — жіночий рід'),
    ('der','Löwe','лев','Тварини','-e','Löwe — die або der (тут der)'),
    ('der','Elefant','слон','Тварини','–','Elefant — чоловічий рід'),
    ('die','Ente','качка','Тварини','-e','Суфікс -e → часто die'),
    ('das','Kaninchen','кролик','Тварини','-chen','Суфікс -chen → das'),
    # ── Сім\'я ─────────────────────────────────────────
    ('die','Mutter','мати','Сім\'я','–','Жін. члени родини → die'),
    ('der','Vater','батько','Сім\'я','–','Чол. члени родини → der'),
    ('das','Kind','дитина','Сім\'я','–','Kind — завжди das'),
    ('die','Schwester','сестра','Сім\'я','–','Жіночий рід → die'),
    ('der','Bruder','брат','Сім\'я','–','Чоловічий рід → der'),
    ('die','Oma','бабуся','Сім\'я','–','Oma — завжди die'),
    ('der','Opa','дідусь','Сім\'я','–','Opa — завжди der'),
    ('das','Mädchen','дівчина','Сім\'я','-chen','Суфікс -chen → das'),
    ('die','Tante','тітка','Сім\'я','-e','Суфікс -e → часто die'),
    ('der','Onkel','дядько','Сім\'я','-el','Суфікс -el → часто der'),
    # ── Місця ─────────────────────────────────────────
    ('die','Schule','школа','Місця','-e','Суфікс -e → часто die'),
    ('das','Haus','будинок','Місця','–','Haus — das'),
    ('die','Stadt','місто','Місця','–','Stadt — жіночий рід'),
    ('das','Zimmer','кімната','Місця','-er','Суфікс -er → das (у іменниках)'),
    ('der','Markt','ринок','Місця','–','Markt — чоловічий рід'),
    ('die','Küche','кухня','Місця','-e','Суфікс -e → часто die'),
    ('das','Büro','офіс','Місця','–','Запозичення → das'),
    ('der','Park','парк','Місця','–','Park — чоловічий рід'),
    ('die','Bibliothek','бібліотека','Місця','-thek','Суфікс -thek → die'),
    ('das','Krankenhaus','лікарня','Місця','–','Складне слово → das Haus'),
    # ── Транспорт ─────────────────────────────────────
    ('das','Auto','автомобіль','Транспорт','–','Транспорт → переважно das'),
    ('der','Zug','поїзд','Транспорт','–','Zug — чоловічий рід'),
    ('das','Flugzeug','літак','Транспорт','-zeug','Суфікс -zeug → das'),
    ('das','Fahrrad','велосипед','Транспорт','–','Fahrrad — das Rad'),
    ('der','Bus','автобус','Транспорт','–','Bus — чоловічий рід'),
    ('die','U-Bahn','метро','Транспорт','-Bahn','Суфікс -Bahn → die'),
    ('das','Schiff','корабель','Транспорт','–','Schiff — das'),
    # ── Професії ──────────────────────────────────────
    ('der','Lehrer','вчитель','Професії','-er','Суфікс -er (особа чол.) → der'),
    ('die','Lehrerin','вчителька','Професії','-erin','Суфікс -erin → die'),
    ('der','Arzt','лікар','Професії','–','Arzt — чоловічий рід'),
    ('die','Ärztin','лікарка','Професії','-in','Суфікс -in → die'),
    ('der','Koch','кухар','Професії','–','Koch — чоловічий рід'),
    ('die','Köchin','кухарка','Професії','-in','Суфікс -in → die'),
    ('der','Ingenieur','інженер','Професії','-eur','Суфікс -eur → der'),
    ('die','Sekretärin','секретарка','Професії','-in','Суфікс -in → die'),
    # ── Предмети ──────────────────────────────────────
    ('das','Buch','книга','Предмети','–','Buch — das'),
    ('der','Tisch','стіл','Предмети','–','Tisch — чоловічий рід'),
    ('die','Lampe','лампа','Предмети','-e','Суфікс -e → часто die'),
    ('das','Handy','телефон','Предмети','–','Запозичення → das'),
    ('der','Schlüssel','ключ','Предмети','-el','Суфікс -el → часто der'),
    ('die','Tasche','сумка','Предмети','-e','Суфікс -e → часто die'),
    ('das','Fenster','вікно','Предмети','–','Fenster — das'),
    ('der','Stift','ручка/олівець','Предмети','–','Stift — чоловічий рід'),
    ('die','Brille','окуляри','Предмети','-e','Суфікс -e → часто die'),
    ('das','Heft','зошит','Предмети','–','Heft — das'),
    # ── Природа ───────────────────────────────────────
    ('die','Blume','квітка','Природа','-e','Суфікс -e → часто die'),
    ('der','Baum','дерево','Природа','–','Baum — чоловічий рід'),
    ('die','Sonne','сонце','Природа','-e','Sonne — die'),
    ('der','Mond','місяць','Природа','–','Mond — чоловічий рід'),
    ('das','Meer','море','Природа','–','Meer — das'),
    ('der','Berg','гора','Природа','–','Berg — чоловічий рід'),
    ('die','Wolke','хмара','Природа','-e','Суфікс -e → часто die'),
    ('das','Gras','трава','Природа','–','Gras — das'),
    ('der','Fluss','річка','Природа','–','Fluss — чоловічий рід'),
    ('die','Erde','земля','Природа','-e','Суфікс -e → часто die'),
    # ── Абстракції ────────────────────────────────────
    ('die','Freiheit','свобода','Абстракції','-heit','Суфікс -heit → завжди die'),
    ('die','Meinung','думка','Абстракції','-ung','Суфікс -ung → завжди die'),
    ('die','Möglichkeit','можливість','Абстракції','-keit','Суфікс -keit → завжди die'),
    ('die','Freundschaft','дружба','Абстракції','-schaft','Суфікс -schaft → die'),
    ('das','Gefühl','почуття','Абстракції','–','Gefühl — das'),
    ('der','Gedanke','думка (думання)','Абстракції','-e','Gedanke — der (виняток)'),
    ('die','Hoffnung','надія','Абстракції','-ung','Суфікс -ung → завжди die'),
    ('das','Glück','щастя','Абстракції','–','Glück — das'),
    ('die','Angst','страх','Абстракції','–','Angst — жіночий рід'),
    ('der','Traum','мрія/сон','Абстракції','–','Traum — чоловічий рід'),
]


def init_db():
    """Створити таблиці та заповнити словник"""
    with app.app_context():
        db.create_all()
        if Word.query.count() == 0:
            for art, ger, ukr, cat, suf, hint in WORD_DATA:
                w = Word(article=art, german=ger, ukrainian=ukr,
                         category=cat, suffix=suf, hint=hint)
                db.session.add(w)
            db.session.commit()
            print(f'✓ Словник заповнено: {len(WORD_DATA)} слів')



# ЗАПУСК

if __name__ == '__main__':
    init_db()
app.run(debug=True, port=5001)
