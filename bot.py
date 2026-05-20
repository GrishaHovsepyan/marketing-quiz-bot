# -*- coding: utf-8 -*-
import os
import logging
import sqlite3
import asyncio
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)

from questions import ALL_QUESTIONS, LECTURES

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)

log = logging.getLogger(__name__)

DB = "quiz.db"


# ---------------- DB ----------------
def db():
    return sqlite3.connect(DB, check_same_thread=False)


def init_db():
    with db() as c:
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
            finished INTEGER DEFAULT 0
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


# ---------------- HELPERS ----------------
TOTAL = len(ALL_QUESTIONS)
LT = ["Ա", "Բ", "Գ", "Դ"]


def active_session(tg_id):
    with db() as c:
        return c.execute(
            "SELECT * FROM sessions WHERE tg_id=? AND finished=0 ORDER BY id DESC LIMIT 1",
            (tg_id,)
        ).fetchone()


def answered_count(sid):
    with db() as c:
        return c.execute(
            "SELECT COUNT(*) FROM answers WHERE session_id=?",
            (sid,)
        ).fetchone()[0]


def bar(done, total, w=10):
    f = int(done / total * w)
    return "[" + "█" * f + "░" * (w - f) + "]"


def grade(pct):
    if pct >= 90:
        return "Գերազանց"
    if pct >= 75:
        return "Լավ"
    if pct >= 55:
        return "Բավարար"
    return "Անբավարար"


def q_text(qi):
    q = ALL_QUESTIONS[qi]
    return (
        f"📘 Լեկցիա՝ {q['lecture']}\n"
        f"{'=' * 30}\n"
        f"❓ Հարց {qi + 1}/{TOTAL}\n\n"
        f"{q['q']}"
    )


def q_keyboard(qi):
    q = ALL_QUESTIONS[qi]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{LT[i]}) {opt}", callback_data=f"a:{qi}:{i}")]
        for i, opt in enumerate(q["opts"])
    ])


# ---------------- START ----------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user

    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO users VALUES(?,?,?,?)",
            (u.id, u.username or "", u.full_name or "", datetime.now().isoformat())
        )

    sess = active_session(u.id)
    name = u.full_name or "Ուսանող"

    if sess:
        done = answered_count(sess["id"])
        if done < TOTAL:
            await update.message.reply_text(
                f"👋 Բարի վերադարձ, {name}\n"
                f"{bar(done, TOTAL)} {done}/{TOTAL}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Շարունակել", callback_data="cont")]
                ])
            )
            return

    await update.message.reply_text(
        f"🎓 Բարի գալուստ, {name}\n\n"
        f"Սկսենք թեստը",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Սկսել", callback_data="begin")]
        ])
    )


# ---------------- CALLBACK ----------------
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id
    data = q.data

    if data == "begin":
        await new_session(q, uid)

    elif data == "cont":
        s = active_session(uid)
        if s:
            qi = answered_count(s["id"])
            await send_q(q, qi)

    elif data.startswith("a:"):
        _, qi, chosen = data.split(":")
        await on_answer(q, uid, int(qi), int(chosen))


# ---------------- SESSION ----------------
async def new_session(q, uid):
    with db() as c:
        c.execute(
            "INSERT INTO sessions(tg_id, started_at) VALUES(?,?)",
            (uid, datetime.now().isoformat())
        )

    await send_q(q, 0)


async def send_q(q, qi):
    await q.edit_message_text(
        q_text(qi),
        reply_markup=q_keyboard(qi)
    )


# ---------------- ANSWER ----------------
async def on_answer(q, uid, qi, chosen):
    s = active_session(uid)
    if not s:
        await q.edit_message_text("Session not found. /start")
        return

    sid = s["id"]

    with db() as c:
        exists = c.execute(
            "SELECT 1 FROM answers WHERE session_id=? AND q_index=?",
            (sid, qi)
        ).fetchone()

        if exists:
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
        "✅ ՃԻՇՏ" if ok else "❌ ՍԽԱԼ",
        ""
    ]

    for i, opt in enumerate(qdata["opts"]):
        if i == correct:
            p = "✔"
        elif i == chosen:
            p = "✖"
        else:
            p = "•"
        lines.append(f"{p} {LT[i]}) {opt}")

    if not ok:
        lines.append(f"\nՃիշտ՝ {LT[correct]}) {qdata['opts'][correct]}")

    await q.edit_message_text("\n".join(lines))

    # ⏳ auto next (SAFE)
    await asyncio.sleep(1.5)

    if next_qi >= TOTAL:
        await show_results(q, uid, sid)
    else:
        await send_q(q, next_qi)


# ---------------- RESULTS ----------------
async def show_results(q, uid, sid):

    with db() as c:
        s = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()

        c.execute(
            "UPDATE sessions SET finished=1, finished_at=? WHERE id=?",
            (datetime.now().isoformat(), sid)
        )

    score = s["score"]
    pct = round(score / TOTAL * 100)

    text = [
        "🏁 ԱՎԱՐՏ",
        "=" * 30,
        f"Score: {score}/{TOTAL} ({pct}%)",
        f"Grade: {grade(pct)}"
    ]

    await q.edit_message_text("\n".join(text))


# ---------------- RUN ----------------
def load_token():
    return os.environ.get("BOT_TOKEN", "")


def main():
    token = load_token()
    if not token:
        print("BOT_TOKEN missing")
        return

    init_db()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
