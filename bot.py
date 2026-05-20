# -*- coding: utf-8 -*-
import os, logging, sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)
from questions import ALL_QUESTIONS, LECTURES

logging.basicConfig(format="%(asctime)s | %(levelname)-8s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

DB = "quiz.db"

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            tg_id INTEGER PRIMARY KEY,
            username TEXT, full_name TEXT, registered_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER, started_at TEXT, finished_at TEXT,
            score INTEGER DEFAULT 0, finished INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS answers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER, q_index INTEGER,
            chosen INTEGER, correct INTEGER, is_correct INTEGER
        );
        """)

def active_session(tg_id):
    with db() as c:
        return c.execute(
            "SELECT * FROM sessions WHERE tg_id=? AND finished=0 ORDER BY id DESC LIMIT 1",
            (tg_id,)).fetchone()

def answered_count(sid):
    with db() as c:
        return c.execute(
            "SELECT COUNT(*) FROM answers WHERE session_id=?", (sid,)
        ).fetchone()[0]

TOTAL = len(ALL_QUESTIONS)
LT = ["A", "B", "C", "D"]

def bar(done, total, w=10):
    f = int(done / total * w)
    return "[" + "#" * f + "-" * (w - f) + "]"

def grade(pct):
    if pct >= 90: return "Grandz (90%+)"
    if pct >= 75: return "Lav (75%+)"
    if pct >= 55: return "Bavarar (55%+)"
    return "Voch bavarar"

def q_text(qi):
    q = ALL_QUESTIONS[qi]
    return (
        f"Lekcija: {q['lecture']}\n"
        f"{'─' * 30}\n"
        f"Harts {qi+1}/{TOTAL}\n\n"
        f"{q['q']}"
    )

def q_keyboard(qi):
    q = ALL_QUESTIONS[qi]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{LT[i]}) {opt}", callback_data=f"a:{qi}:{i}")]
        for i, opt in enumerate(q["opts"])
    ])

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO users VALUES(?,?,?,?)",
            (u.id, u.username or "", u.full_name or "", datetime.now().isoformat())
        )
    sess = active_session(u.id)
    name = u.full_name or "Usanoghy"
    if sess:
        done = answered_count(sess["id"])
        if done < TOTAL:
            await update.message.reply_text(
                f"Bari veradarts, {name}!\n\n"
                f"Duq ouneq chkatarats test.\n"
                f"{bar(done, TOTAL)} {done}/{TOTAL} hartsadarvats",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Sharounakel", callback_data="cont")],
                    [InlineKeyboardButton("Sksel norits", callback_data="restart")],
                ])
            )
            return
    await update.message.reply_text(
        f"Bari galust, {name}!\n\n"
        f"Markʼetingayin Hagordaktsutʼyunner\n"
        f"{'─' * 30}\n"
        f"6 lekcija · 60 harts · 4 tarberак\n\n"
        f"Amen hartsi depqoum karandaiq ardzagunte.\n"
        f"Verjoum kkstanaq gnahahatakan.\n\n"
        f"Patrastak eq?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Sksel Teste", callback_data="begin")]
        ])
    )

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    d = q.data

    if d == "begin":
        await new_session(q, uid)
    elif d == "cont":
        s = active_session(uid)
        if s:
            qi = answered_count(s["id"])
            await send_q(q, qi, s["id"])
    elif d == "restart":
        with db() as c:
            c.execute(
                "UPDATE sessions SET finished=1, finished_at=? WHERE tg_id=? AND finished=0",
                (datetime.now().isoformat(), uid)
            )
        await new_session(q, uid)
    elif d.startswith("a:"):
        _, qi_s, ch_s = d.split(":")
        await on_answer(q, uid, int(qi_s), int(ch_s))
    elif d == "next":
        s = active_session(uid)
        if not s:
            return
        qi = answered_count(s["id"])
        if qi >= TOTAL:
            await show_results(q, uid, s["id"])
        else:
            await send_q(q, qi, s["id"])
    elif d == "results":
        s = active_session(uid)
        if not s:
            with db() as c:
                s = c.execute(
                    "SELECT * FROM sessions WHERE tg_id=? ORDER BY id DESC LIMIT 1",
                    (uid,)).fetchone()
        if s:
            await show_results(q, uid, s["id"])
    elif d == "retry":
        with db() as c:
            c.execute(
                "UPDATE sessions SET finished=1 WHERE tg_id=? AND finished=0", (uid,)
            )
        await new_session(q, uid)

async def new_session(q, uid):
    with db() as c:
        sid = c.execute(
            "INSERT INTO sessions(tg_id, started_at) VALUES(?,?)",
            (uid, datetime.now().isoformat())
        ).lastrowid
    await send_q(q, 0, sid)

async def send_q(q, qi, sid):
    await q.edit_message_text(q_text(qi), reply_markup=q_keyboard(qi))

async def on_answer(q, uid, qi, chosen):
    s = active_session(uid)
    if not s:
        await q.edit_message_text("Niste chi gtнvel: /start")
        return
    sid = s["id"]
    with db() as c:
        if c.execute(
            "SELECT 1 FROM answers WHERE session_id=? AND q_index=?", (sid, qi)
        ).fetchone():
            qi2 = answered_count(sid)
            if qi2 >= TOTAL:
                await show_results(q, uid, sid)
            else:
                await send_q(q, qi2, sid)
            return

    qdata = ALL_QUESTIONS[qi]
    correct = qdata["correct"]
    ok = chosen == correct

    with db() as c:
        c.execute(
            "INSERT INTO answers(session_id,q_index,chosen,correct,is_correct) VALUES(?,?,?,?,?)",
            (sid, qi, chosen, correct, int(ok))
        )
        if ok:
            c.execute("UPDATE sessions SET score=score+1 WHERE id=?", (sid,))

    next_qi = qi + 1
    lines = [
        "CHTOR E! (+1 miavor)" if ok else "SKHАL!",
        "",
    ]
    for i, opt in enumerate(qdata["opts"]):
        if i == correct:
            pfx = "[V]"
        elif i == chosen and not ok:
            pfx = "[X]"
        else:
            pfx = "   "
        lines.append(f"{pfx} {LT[i]}) {opt}")

    if not ok:
        lines.append(f"\nChtort pataskhane: {LT[correct]}) {qdata['opts'][correct]}")

    lines.append(f"\n{bar(next_qi, TOTAL)} {next_qi}/{TOTAL}")

    btn = ("Tesnel ardyunkner", "results") if next_qi >= TOTAL else \
          (f"Hajord harts ({next_qi+1}/{TOTAL})", "next")

    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(btn[0], callback_data=btn[1])]
        ])
    )

async def show_results(q, uid, sid):
    with db() as c:
        s = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        c.execute(
            "UPDATE sessions SET finished=1, finished_at=? WHERE id=?",
            (datetime.now().isoformat(), sid)
        )
        rows = c.execute(
            "SELECT q_index, is_correct FROM answers WHERE session_id=?", (sid,)
        ).fetchall()

    score = s["score"]
    pct = round(score / TOTAL * 100)

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

    lines = [
        "TEST AVARTVATС E!",
        "─" * 30,
        f"Yndhanour ardyunk: {score}/{TOTAL} ({pct}%)",
        f"Gnahatakan: {grade(pct)}",
        "─" * 30,
        "Yst lekcijaner:",
        "",
    ]

    for lt, (c_ok, c_tot) in lec_stats.items():
        p = round(c_ok / c_tot * 100) if c_tot else 0
        short = lt.split("—")[-1].strip() if "—" in lt else lt
        lines.append(f"{short}")
        lines.append(f"{bar(c_ok, c_tot)} {c_ok}/{c_tot} ({p}%)")
        lines.append("")

    lines.append("─" * 30)
    if pct >= 90:
        lines.append("Fantastik ardyunk!")
    elif pct >= 75:
        lines.append("Lav ardyunk! Nyuty himnakanоum tirapetum e.")
    elif pct >= 55:
        lines.append("Bavar. Vor temanerе krknel e petk.")
    else:
        lines.append("Voch bav. Lekcijanerе krknelay anhrаjhesht e.")

    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Krkni Porbel", callback_data="retry")]
        ])
    )

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        users_n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        fin_n   = c.execute("SELECT COUNT(*) FROM sessions WHERE finished=1").fetchone()[0]
        avg_row = c.execute(
            "SELECT AVG(CAST(score AS REAL)/60*100) FROM sessions WHERE finished=1"
        ).fetchone()[0]
        top = c.execute("""
            SELECT u.full_name, s.score,
                   ROUND(CAST(s.score AS REAL)/60*100) as pct
            FROM sessions s JOIN users u ON s.tg_id=u.tg_id
            WHERE s.finished=1
            ORDER BY s.score DESC LIMIT 10
        """).fetchall()

    lines = [
        "VICHAKAGRUTYUNNER",
        f"Grantsvats usanogner: {users_n}",
        f"Avartats testner: {fin_n}",
        f"Mittlakan ardyunk: {round(avg_row or 0)}%",
        "",
        "TOP 10",
        "",
    ]
    for i, r in enumerate(top, 1):
        lines.append(f"{i}. {r['full_name']} — {r['score']}/60 ({int(r['pct'])}%)")

    await update.message.reply_text("\n".join(lines))

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ogtag /start hramane test surpeldu hamar.")

def load_token():
    t = os.environ.get("BOT_TOKEN", "").strip()
    if t:
        return t
    env = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env):
        for line in open(env):
            line = line.strip()
            if line.startswith("BOT_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

def main():
    token = load_token()
    if not token:
        print("BOT_TOKEN chi gtnvel!")
        print("Steghtsets .env fail — BOT_TOKEN=your_token_here")
        return
    init_db()
    log.info("DB OK | %d harts", TOTAL)
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
