# -*- coding: utf-8 -*-
import os
import logging
import sqlite3
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from questions import ALL_QUESTIONS, LECTURES

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    level=logging.INFO
)

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


def active_session(tg_id):
    with db() as c:
        return c.execute(
            """
            SELECT *
            FROM sessions
            WHERE tg_id=? AND finished=0
            ORDER BY id DESC
            LIMIT 1
            """,
            (tg_id,)
        ).fetchone()


def answered_count(session_id):
    with db() as c:
        return c.execute(
            "SELECT COUNT(*) FROM answers WHERE session_id=?",
            (session_id,)
        ).fetchone()[0]


TOTAL = len(ALL_QUESTIONS)

LT = ["Ա", "Բ", "Գ", "Դ"]


def bar(done, total, width=10):
    filled = int(done / total * width)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def grade(percent):
    if percent >= 90:
        return "Գերազանց (90%+)"

    if percent >= 75:
        return "Լավ (75%+)"

    if percent >= 55:
        return "Բավարար (55%+)"

    return "Անբավարար"


def q_text(qi):
    q = ALL_QUESTIONS[qi]

    return (
        f"Լեկցիա՝ {q['lecture']}\n"
        f"{'=' * 30}\n"
        f"Հարց {qi + 1}/{TOTAL}\n\n"
        f"{q['q']}"
    )


def q_keyboard(qi):
    q = ALL_QUESTIONS[qi]

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{LT[i]}) {opt}",
                callback_data=f"a:{qi}:{i}"
            )
        ]
        for i, opt in enumerate(q["opts"])
    ])


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):

    u = update.effective_user

    with db() as c:
        c.execute(
            """
            INSERT OR REPLACE INTO users
            VALUES(?,?,?,?)
            """,
            (
                u.id,
                u.username or "",
                u.full_name or "",
                datetime.now().isoformat()
            )
        )

    sess = active_session(u.id)

    name = u.full_name or "Ուսանող"

    if sess:
        done = answered_count(sess["id"])

        if done < TOTAL:
            await update.message.reply_text(
                f"Բարի վերադարձ, {name}!\n\n"
                f"Դուք ունեք չավարտված թեստ։\n"
                f"{bar(done, TOTAL)} {done}/{TOTAL} պատասխանված",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "Շարունակել",
                            callback_data="cont"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "Սկսել նորից",
                            callback_data="restart"
                        )
                    ]
                ])
            )
            return

    await update.message.reply_text(
        f"Բարի գալուստ, {name}!\n\n"
        f"Մարքեթինգային հաղորդակցություններ\n"
        f"{'=' * 30}\n"
        f"6 լեկցիա | 60 հարց | 4 տարբերակ\n\n"
        f"Յուրաքանչյուր հարցի համար ընտրեք ճիշտ պատասխանը։\n"
        f"Վերջում կստանաք գնահատական։\n\n"
        f"Պատրա՞ստ եք",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "Սկսել թեստը",
                    callback_data="begin"
                )
            ]
        ])
    )


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

    elif data == "restart":

        with db() as c:
            c.execute(
                """
                UPDATE sessions
                SET finished=1,
                    finished_at=?
                WHERE tg_id=? AND finished=0
                """,
                (
                    datetime.now().isoformat(),
                    uid
                )
            )

        await new_session(q, uid)

    elif data.startswith("a:"):

        _, qi_s, chosen_s = data.split(":")

        await on_answer(
            q,
            uid,
            int(qi_s),
            int(chosen_s)
        )

    elif data == "next":

        s = active_session(uid)

        if not s:
            return

        qi = answered_count(s["id"])

        if qi >= TOTAL:
            await show_results(q, uid, s["id"])
        else:
            await send_q(q, qi)

    elif data == "results":

        s = active_session(uid)

        if not s:
            with db() as c:
                s = c.execute(
                    """
                    SELECT *
                    FROM sessions
                    WHERE tg_id=?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (uid,)
                ).fetchone()

        if s:
            await show_results(q, uid, s["id"])


async def new_session(q, uid):

    with db() as c:
        c.execute(
            """
            INSERT INTO sessions(tg_id, started_at)
            VALUES(?,?)
            """,
            (
                uid,
                datetime.now().isoformat()
            )
        )

    await send_q(q, 0)


async def send_q(q, qi):

    await q.edit_message_text(
        q_text(qi),
        reply_markup=q_keyboard(qi)
    )


async def on_answer(q, uid, qi, chosen):

    s = active_session(uid)

    if not s:
        await q.edit_message_text(
            "Սեսիա չի գտնվել։ Օգտագործեք /start"
        )
        return

    sid = s["id"]

    with db() as c:

        exists = c.execute(
            """
            SELECT 1
            FROM answers
            WHERE session_id=? AND q_index=?
            """,
            (sid, qi)
        ).fetchone()

        if exists:

            qi2 = answered_count(sid)

            if qi2 >= TOTAL:
                await show_results(q, uid, sid)
            else:
                await send_q(q, qi2)

            return

    qdata = ALL_QUESTIONS[qi]

    correct = qdata["correct"]

    ok = chosen == correct

    with db() as c:

        c.execute(
            """
            INSERT INTO answers(
                session_id,
                q_index,
                chosen,
                correct,
                is_correct
            )
            VALUES(?,?,?,?,?)
            """,
            (
                sid,
                qi,
                chosen,
                correct,
                int(ok)
            )
        )

        if ok:
            c.execute(
                "UPDATE sessions SET score=score+1 WHERE id=?",
                (sid,)
            )

    next_qi = qi + 1

    lines = [
        "ՃԻՇՏ Է! (+1 միավոր)" if ok else "ՍԽԱԼ!",
        "",
    ]

    for i, opt in enumerate(qdata["opts"]):

        if i == correct:
            prefix = "[✓]"

        elif i == chosen and not ok:
            prefix = "[✗]"

        else:
            prefix = "   "

        lines.append(f"{prefix} {LT[i]}) {opt}")

    if not ok:
        lines.append(
            f"\nՃիշտ պատասխանը՝ "
            f"{LT[correct]}) "
            f"{qdata['opts'][correct]}"
        )

    lines.append(
        f"\n{bar(next_qi, TOTAL)} {next_qi}/{TOTAL}"
    )

    button = (
        ("Տեսնել արդյունքները", "results")
        if next_qi >= TOTAL
        else
        (f"Հաջորդ հարց ({next_qi + 1}/{TOTAL})", "next")
    )

    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    button[0],
                    callback_data=button[1]
                )
            ]
        ])
    )


async def show_results(q, uid, sid):

    with db() as c:

        s = c.execute(
            "SELECT * FROM sessions WHERE id=?",
            (sid,)
        ).fetchone()

        c.execute(
            """
            UPDATE sessions
            SET finished=1,
                finished_at=?
            WHERE id=?
            """,
            (
                datetime.now().isoformat(),
                sid
            )
        )

        rows = c.execute(
            """
            SELECT q_index, is_correct
            FROM answers
            WHERE session_id=?
            """,
            (sid,)
        ).fetchall()

    score = s["score"]

    percent = round(score / TOTAL * 100)

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
        "ԹԵՍՏԸ ԱՎԱՐՏՎԱԾ Է!",
        "=" * 30,
        f"Ընդհանուր արդյունք՝ {score}/{TOTAL} ({percent}%)",
        f"Գնահատական՝ {grade(percent)}",
        "=" * 30,
        "Արդյունքներ ըստ լեկցիաների",
        "",
    ]

    for lt, (ok_count, total_count) in lec_stats.items():

        p = round(ok_count / total_count * 100) if total_count else 0

        short = (
            lt.split("-")[-1].strip()
            if "-" in lt
            else lt
        )

        lines.append(short)
        lines.append(
            f"{bar(ok_count, total_count)} "
            f"{ok_count}/{total_count} ({p}%)"
        )
        lines.append("")

    lines.append("=" * 30)

    if percent >= 90:
        lines.append("Ֆանտաստիկ արդյունք!")

    elif percent >= 75:
        lines.append(
            "Լավ արդյունք։ "
            "Նյութին հիմնականում տիրապետում եք։"
        )

    elif percent >= 55:
        lines.append(
            "Բավարար արդյունք։ "
            "Որոշ թեմաներ կրկնելու կարիք կա։"
        )

    else:
        lines.append(
            "Անբավարար արդյունք։ "
            "Խորհուրդ է տրվում կրկնել լեկցիաները։"
        )

    await q.edit_message_text(
        "\n".join(lines)
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):

    with db() as c:

        users_n = c.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        fin_n = c.execute(
            """
            SELECT COUNT(*)
            FROM sessions
            WHERE finished=1
            """
        ).fetchone()[0]

        avg_row = c.execute(
            """
            SELECT AVG(CAST(score AS REAL)/60*100)
            FROM sessions
            WHERE finished=1
            """
        ).fetchone()[0]

        top = c.execute("""
            SELECT
                u.full_name,
                s.score,
                ROUND(CAST(s.score AS REAL)/60*100) as pct
            FROM sessions s
            JOIN users u
                ON s.tg_id=u.tg_id
            WHERE s.finished=1
            ORDER BY s.score DESC
            LIMIT 10
        """).fetchall()

    lines = [
        "ՎԻՃԱԿԱԳՐՈՒԹՅՈՒՆ",
        f"Գրանցված ուսանողներ՝ {users_n}",
        f"Ավարտված թեստեր՝ {fin_n}",
        f"Միջին արդյունք՝ {round(avg_row or 0)}%",
        "",
        "ԹՈՓ 10",
        "",
    ]

    for i, r in enumerate(top, 1):
        lines.append(
            f"{i}. {r['full_name']} - "
            f"{r['score']}/60 "
            f"({int(r['pct'])}%)"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "Օգտագործեք /start հրամանը թեստը սկսելու համար։"
    )


def load_token():

    token = os.environ.get("BOT_TOKEN", "").strip()

    if token:
        return token

    env_path = os.path.join(
        os.path.dirname(__file__),
        ".env"
    )

    if os.path.exists(env_path):

        with open(env_path, encoding="utf-8") as f:

            for line in f:

                line = line.strip()

                if line.startswith("BOT_TOKEN="):

                    return (
                        line
                        .split("=", 1)[1]
                        .strip()
                        .strip('"')
                        .strip("'")
                    )

    return ""


def main():

    token = load_token()

    if not token:

        print("BOT_TOKEN-ը չի գտնվել!")
        print("Ստեղծեք .env ֆայլ")
        print("BOT_TOKEN=your_token_here")

        return

    init_db()

    log.info("DB OK | %d հարց", TOTAL)

    app = Application.builder().token(token).build()

    app.add_handler(
        CommandHandler("start", cmd_start)
    )

    app.add_handler(
        CommandHandler("stats", cmd_stats)
    )

    app.add_handler(
        CallbackQueryHandler(on_callback)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            on_text
        )
    )

    log.info("Բոտը մեկնարկում է...")

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
