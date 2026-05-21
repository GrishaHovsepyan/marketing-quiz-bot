import os
import logging
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from questions import ALL_QUESTIONS, LECTURES

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
DB = "quiz.db"
TOTAL = len(ALL_QUESTIONS)
LT = ["A", "B", "C", "D"]
TIME_LIMIT_MINUTES = 65

def get_db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with get_db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            registered_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            started_at TEXT,
            finished_at TEXT,
            score INTEGER DEFAULT 0,
            finished INTEGER DEFAULT 0,
            timed_out INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS answers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            q_index INTEGER,
            chosen INTEGER,
            correct INTEGER,
            is_correct INTEGER
        );
        """)
    # Add timed_out column if not exists (for existing DBs)
    try:
        with get_db() as c:
            c.execute("ALTER TABLE sessions ADD COLUMN timed_out INTEGER DEFAULT 0")
    except:
        pass

def active_session(tg_id):
    with get_db() as c:
        return c.execute(
            "SELECT * FROM sessions WHERE tg_id=? AND finished=0 ORDER BY id DESC LIMIT 1",
            (tg_id,)
        ).fetchone()

def finished_session(tg_id):
    with get_db() as c:
        return c.execute(
            "SELECT * FROM sessions WHERE tg_id=? AND finished=1 ORDER BY id DESC LIMIT 1",
            (tg_id,)
        ).fetchone()

def answered_count(sid):
    with get_db() as c:
        return c.execute(
            "SELECT COUNT(*) FROM answers WHERE session_id=?", (sid,)
        ).fetchone()[0]

def is_timed_out(session):
    if not session or not session["started_at"]:
        return False
    started = datetime.fromisoformat(session["started_at"])
    return datetime.now() > started + timedelta(minutes=TIME_LIMIT_MINUTES)

def time_remaining(session):
    if not session or not session["started_at"]:
        return 0
    started = datetime.fromisoformat(session["started_at"])
    deadline = started + timedelta(minutes=TIME_LIMIT_MINUTES)
    remaining = (deadline - datetime.now()).total_seconds()
    return max(0, int(remaining))

def progress_bar(done, total):
    f = int(done / total * 10)
    return "[" + "#" * f + "-" * (10 - f) + "]"

def grade_text(pct):
    if pct >= 90:
        return "5 (Grandz)"
    elif pct >= 75:
        return "4 (Lav)"
    elif pct >= 55:
        return "3 (Bavarar)"
    else:
        return "2 (Voch bavarar)"

def make_question_text(qi, session):
    q = ALL_QUESTIONS[qi]
    mins = time_remaining(session) // 60
    secs = time_remaining(session) % 60
    text = q["lecture"] + "\n"
    text += "--------------------------------\n"
    text += str(qi + 1) + "/" + str(TOTAL)
    text += "  |  Mnatsum e: " + str(mins) + ":" + str(secs).zfill(2) + "\n\n"
    text += q["q"]
    return text

def make_question_keyboard(qi):
    q = ALL_QUESTIONS[qi]
    buttons = []
    for i, opt in enumerate(q["opts"]):
        label = LT[i] + ") " + opt
        buttons.append([InlineKeyboardButton(label, callback_data="a:" + str(qi) + ":" + str(i))])
    return InlineKeyboardMarkup(buttons)

# Store waiting-for-name users: {tg_id: True}
waiting_for_name = {}

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tg_id = user.id

    # Check already finished
    done_sess = finished_session(tg_id)
    if done_sess:
        score = done_sess["score"]
        pct = round(score / TOTAL * 100)
        name = done_sess["timed_out"] and "timeout" or ""
        with get_db() as c:
            u = c.execute("SELECT full_name FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        uname = u["full_name"] if u else ""
        text = uname + ", duq ardеn hanjnel eq ays teste.\n\n"
        text += "Zer ardyounke: " + str(score) + "/" + str(TOTAL) + " (" + str(pct) + "%)\n"
        text += "Gnahatakan: " + grade_text(pct) + "\n\n"
        text += "Teste kareli e hanjnel miain mek angam."
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Ardyunknery tesnel", callback_data="show_all_results")]
        ]))
        return

    # Check active session
    sess = active_session(tg_id)
    if sess:
        if is_timed_out(sess):
            # Force finish
            with get_db() as c:
                c.execute(
                    "UPDATE sessions SET finished=1, timed_out=1, finished_at=? WHERE id=?",
                    (datetime.now().isoformat(), sess["id"])
                )
            await update.message.reply_text(
                "Zhamanakamidzum e avartvatс. Teste avtomatik kerpov avartvatс e.\n\n"
                "/start - tesnel ardyunky"
            )
            return
        done = answered_count(sess["id"])
        if done < TOTAL:
            mins = time_remaining(sess) // 60
            text = "Bari veradarts!\n\n"
            text += "Duk ouneq chkatarats test.\n"
            text += progress_bar(done, TOTAL) + " " + str(done) + "/" + str(TOTAL) + "\n"
            text += "Mnatsum e: " + str(mins) + " rope"
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Sharounakel", callback_data="cont")],
            ]))
            return

    # Ask for name
    waiting_for_name[tg_id] = True
    await update.message.reply_text(
        "Bari galust!\n\n"
        "Marketingayin Hagordaktsutyan Test\n"
        "--------------------------------\n"
        "6 lekcija | 60 harts | " + str(TIME_LIMIT_MINUTES) + " rope\n\n"
        "Sksel nakhord, kgrel dzez anoun ev azganoun (orinakе: Ani Petrosyan):"
    )

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tg_id = user.id
    text = update.message.text.strip()

    if tg_id in waiting_for_name:
        # Validate name (at least 2 words)
        parts = text.split()
        if len(parts) < 2:
            await update.message.reply_text(
                "Khndrum enk grel dzez ANE anoun EV azganoun.\n"
                "Orinakе: Ani Petrosyan"
            )
            return

        full_name = text
        with get_db() as c:
            c.execute(
                "INSERT OR REPLACE INTO users VALUES(?,?,?,?)",
                (tg_id, user.username or "", full_name, datetime.now().isoformat())
            )
        del waiting_for_name[tg_id]

        await update.message.reply_text(
            "Shnorhakalutyun, " + full_name + "!\n\n"
            "Uzhadrutyun:\n"
            "- Teste kareli e hanjnel MIAIN MEK ANGAM\n"
            "- Dzez kta " + str(TIME_LIMIT_MINUTES) + " rope\n"
            "- Zamanakamidzum avartvelits heto teste avtomatik kkapvi\n\n"
            "Patrastak e՞q skselu:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Sksеl Teste", callback_data="begin")]
            ])
        )
        return

    await update.message.reply_text("Ogtag /start hramane test surpeldu hamar.")

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    d = q.data

    if d == "begin":
        done_sess = finished_session(uid)
        if done_sess:
            score = done_sess["score"]
            pct = round(score / TOTAL * 100)
            await q.edit_message_text(
                "Duq ardеn hanjnel eq ays teste.\n\n"
                "Zer ardyounke: " + str(score) + "/" + str(TOTAL) + " (" + str(pct) + "%)\n"
                "Gnahatakan: " + grade_text(pct),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Ardyunknery tesnel", callback_data="show_all_results")]
                ])
            )
            return
        await start_new_session(q, uid)

    elif d == "cont":
        s = active_session(uid)
        if s:
            if is_timed_out(s):
                with get_db() as c:
                    c.execute(
                        "UPDATE sessions SET finished=1, timed_out=1, finished_at=? WHERE id=?",
                        (datetime.now().isoformat(), s["id"])
                    )
                await q.edit_message_text("Zhamanakamidzum e avartvatс. /start - tesnel ardyunky")
                return
            qi = answered_count(s["id"])
            await send_question(q, qi, s["id"], s)

    elif d.startswith("a:"):
        parts = d.split(":")
        await process_answer(q, uid, int(parts[1]), int(parts[2]))

    elif d == "show_all_results":
        await show_all_results(q)

async def start_new_session(q, uid):
    with get_db() as c:
        sid = c.execute(
            "INSERT INTO sessions(tg_id, started_at) VALUES(?,?)",
            (uid, datetime.now().isoformat())
        ).lastrowid
        sess = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    await send_question(q, 0, sid, sess)

async def send_question(q, qi, sid, sess=None):
    if sess is None:
        with get_db() as c:
            sess = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    await q.edit_message_text(
        make_question_text(qi, sess),
        reply_markup=make_question_keyboard(qi)
    )

async def process_answer(q, uid, qi, chosen):
    s = active_session(uid)
    if not s:
        await q.edit_message_text("Niste chi gtnvel. /start")
        return
    sid = s["id"]

    # Check timeout
    if is_timed_out(s):
        with get_db() as c:
            c.execute(
                "UPDATE sessions SET finished=1, timed_out=1, finished_at=? WHERE id=?",
                (datetime.now().isoformat(), sid)
            )
        await q.edit_message_text(
            "Zhamanakamidzum e lrtsel!\n\n"
            "Teste avtomatik kerpov avartvatс e.\n"
            "/start - tesnel ardyunky"
        )
        return

    with get_db() as c:
        existing = c.execute(
            "SELECT 1 FROM answers WHERE session_id=? AND q_index=?", (sid, qi)
        ).fetchone()
    if existing:
        qi2 = answered_count(sid)
        if qi2 >= TOTAL:
            await finish_session(q, uid, sid)
        else:
            await send_question(q, qi2, sid, s)
        return

    qdata = ALL_QUESTIONS[qi]
    correct = qdata["correct"]
    ok = chosen == correct

    with get_db() as c:
        c.execute(
            "INSERT INTO answers(session_id,q_index,chosen,correct,is_correct) VALUES(?,?,?,?,?)",
            (sid, qi, chosen, correct, int(ok))
        )
        if ok:
            c.execute("UPDATE sessions SET score=score+1 WHERE id=?", (sid,))

    next_qi = qi + 1

    # Show feedback briefly then auto-advance
    if ok:
        result_text = "Chtor e! (+1)\n\n"
    else:
        result_text = "Skhal!\n\n"

    for i, opt in enumerate(qdata["opts"]):
        if i == correct:
            prefix = "[V] "
        elif i == chosen and not ok:
            prefix = "[X] "
        else:
            prefix = "    "
        result_text += prefix + LT[i] + ") " + opt + "\n"

    if not ok:
        result_text += "\nChtort patasxane: " + LT[correct] + ") " + qdata["opts"][correct]

    result_text += "\n\n" + progress_bar(next_qi, TOTAL) + " " + str(next_qi) + "/" + str(TOTAL)

    if next_qi >= TOTAL:
        # Last question - show results button
        await q.edit_message_text(
            result_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Tesnel ardyunkner", callback_data="show_results_" + str(sid))]
            ])
        )
    else:
        # Auto advance - show next question immediately
        await q.edit_message_text(result_text)
        # Immediately send next question as new message
        import asyncio
        await asyncio.sleep(1.5)
        s_updated = active_session(uid)
        if s_updated:
            await q.edit_message_text(
                make_question_text(next_qi, s_updated),
                reply_markup=make_question_keyboard(next_qi)
            )

async def finish_session(q, uid, sid):
    with get_db() as c:
        s = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    await show_results(q, uid, sid, s)

async def show_results(q, uid, sid, session=None):
    with get_db() as c:
        s = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        c.execute(
            "UPDATE sessions SET finished=1, finished_at=? WHERE id=?",
            (datetime.now().isoformat(), sid)
        )
        rows = c.execute(
            "SELECT q_index, is_correct FROM answers WHERE session_id=?", (sid,)
        ).fetchall()
        user = c.execute("SELECT full_name FROM users WHERE tg_id=?", (uid,)).fetchone()

    score = s["score"]
    pct = round(score / TOTAL * 100)
    name = user["full_name"] if user else ""
    timed = s["timed_out"] if "timed_out" in s.keys() else 0

    lec_stats = {}
    for lec in LECTURES:
        lec_stats[lec["title"]] = [0, lec["count"]]

    for r in rows:
        lec_title = ALL_QUESTIONS[r["q_index"]]["lecture"]
        for lt in lec_stats:
            if lt in lec_title or lec_title in lt:
                if r["is_correct"]:
                    lec_stats[lt][0] += 1
                break

    text = name + "\n"
    text += "TEST AVARTVATС E!\n"
    if timed:
        text += "(!!! Zhamanakamidzum e lrtsel !!!)\n"
    text += "--------------------------------\n"
    text += "Ardyunk: " + str(score) + "/" + str(TOTAL) + " (" + str(pct) + "%)\n"
    text += "Gnahatakan: " + grade_text(pct) + "\n"
    text += "--------------------------------\n\n"

    for lt, (c_ok, c_tot) in lec_stats.items():
        p = round(c_ok / c_tot * 100) if c_tot else 0
        text += lt + "\n"
        text += progress_bar(c_ok, c_tot) + " " + str(c_ok) + "/" + str(c_tot) + " (" + str(p) + "%)\n\n"

    text += "--------------------------------\n"
    if pct >= 90:
        text += "Fantastik ardyunk!"
    elif pct >= 75:
        text += "Lav ardyunk!"
    elif pct >= 55:
        text += "Bavar. Vor temanerе petk e krknel."
    else:
        text += "Voch bav. Lekcijanerе petk e krknel."

    await q.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Bnagrich ardyunkner", callback_data="show_all_results")]
        ])
    )

async def show_all_results(q):
    with get_db() as c:
        results = c.execute("""
            SELECT u.full_name, s.score, s.timed_out,
                   ROUND(CAST(s.score AS REAL)/60*100) as pct
            FROM sessions s
            JOIN users u ON s.tg_id=u.tg_id
            WHERE s.finished=1
            ORDER BY s.score DESC
        """).fetchall()

    if not results:
        await q.edit_message_text("Depo voч mek chem avartvel teste.")
        return

    text = "BNAGRICH ARDYUNKNER\n"
    text += "================================\n\n"
    for i, r in enumerate(results, 1):
        timed = " (timeout)" if r["timed_out"] else ""
        text += str(i) + ". " + r["full_name"] + timed + "\n"
        text += "   " + str(r["score"]) + "/60 (" + str(int(r["pct"])) + "%) - " + grade_text(int(r["pct"])) + "\n\n"

    await q.edit_message_text(text)

async def handle_results_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    uid = q.from_user.id

    if d.startswith("show_results_"):
        sid = int(d.split("_")[-1])
        await show_results(q, uid, sid)
    elif d == "show_all_results":
        await show_all_results(q)

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with get_db() as c:
        users_n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        fin_n = c.execute("SELECT COUNT(*) FROM sessions WHERE finished=1").fetchone()[0]
        avg_row = c.execute(
            "SELECT AVG(CAST(score AS REAL)/60*100) FROM sessions WHERE finished=1"
        ).fetchone()[0]
        top = c.execute("""
            SELECT u.full_name, s.score, s.timed_out,
                   ROUND(CAST(s.score AS REAL)/60*100) as pct
            FROM sessions s JOIN users u ON s.tg_id=u.tg_id
            WHERE s.finished=1 ORDER BY s.score DESC LIMIT 30
        """).fetchall()

    text = "VICHAKAGRUTYUNNER\n"
    text += "Grantsvats: " + str(users_n) + "\n"
    text += "Avartats testner: " + str(fin_n) + "\n"
    text += "Mittlakan ardyunk: " + str(round(avg_row or 0)) + "%\n\n"
    text += "TOP 30\n\n"
    for i, r in enumerate(top, 1):
        timed = " (!)" if r["timed_out"] else ""
        text += str(i) + ". " + r["full_name"] + timed + " - " + str(r["score"]) + "/60 (" + str(int(r["pct"])) + "%)\n"
    await update.message.reply_text(text)

def load_token():
    t = os.environ.get("BOT_TOKEN", "").strip()
    if t:
        return t
    env = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env):
        for line in open(env):
            line = line.strip()
            if line.startswith("BOT_TOKEN"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

def main():
    token = load_token()
    if not token:
        print("BOT_TOKEN chi gtnvel!")
        return
    init_db()
    log.info("DB OK | %d harts", TOTAL)
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(handle_results_callback, pattern="^show_results_|^show_all_results$"))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    log.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
