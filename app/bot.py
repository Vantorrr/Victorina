from __future__ import annotations

import asyncio
import os
from typing import Final

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
    MenuButtonWebApp,
)
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, ConversationHandler, filters
import json

from app.db import get_connection, utc_now_iso


BOT_TOKEN: Final[str | None] = os.getenv("BOT_TOKEN")
ADMIN_USERNAMES: Final[list[str]] = [u.strip().lower() for u in (os.getenv("ADMIN_USERNAMES", "").split(",")) if u.strip()]
SEED_ADMIN_ID: Final[int | None] = int(os.getenv("SEED_ADMIN_ID", "0")) or None
BASE_URL: Final[str] = os.getenv("BASE_URL", "http://localhost:8080")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привет! Я бот викторины.\n\n"
        "• Ведущий: напиши \"Меню\" или /host — открою панель.\n"
        "• Капитан: жми /register после того, как ведущий назначит тебя капитаном.\n\n"
        "Удачной игры! 🎉"
    )


async def newgame(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /newgame Название игры")
        return
    name = " ".join(context.args)
    with get_connection() as conn:
        cur = conn.execute("INSERT INTO games(name, status, current_round) VALUES (?, 'active', 1)", (name,))
        game_id = cur.lastrowid
        conn.execute("INSERT INTO rounds(game_id, number, status) VALUES (?, 1, 'active')", (game_id,))
        conn.commit()
    await update.message.reply_text(
        "🎮 Игра создана!\n\n"
        f"Название: <b>{name}</b>\n"
        f"ID: <code>{game_id}</code>\n\n"
        "Что дальше?\n"
        "1) ➕ Добавь команды (Меню → Добавить команду)\n"
        "2) 👨‍✈️ Капитану: открыть чат с ботом → Start → /register\n"
        "3) 🗂 В админке можно загрузить вопросы/кейсы\n"
        "4) ▶ Когда готов — Запустить вопрос\n",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="Открыть админку", web_app=WebAppInfo(url=f"{BASE_URL}/admin"))]]
        ),
    )


async def addteam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /addteam <Название команды> <@username капитана>")
        return
    team_name = context.args[0]
    captain_username = context.args[1].lstrip('@')
    with get_connection() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO teams(name) VALUES (?)", (team_name,))
        team_id = cur.lastrowid or conn.execute("SELECT id FROM teams WHERE name=?", (team_name,)).fetchone()[0]
        cur = conn.execute("INSERT OR IGNORE INTO captains(username, team_id) VALUES (?, ?)", (captain_username, team_id))
        if cur.rowcount == 0:
            conn.execute("UPDATE captains SET team_id=? WHERE username=?", (team_id, captain_username))
        conn.commit()
    await update.message.reply_text(
        "✅ Команда добавлена!\n\n"
        f"Название: <b>{team_name}</b>\n"
        f"Капитан: @{captain_username}\n\n"
        "Попроси капитана: открыть чат с ботом → нажать Start → отправить /register.\n"
        "После регистрации капитан будет получать вопросы.",
        parse_mode="HTML",
    )


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    username = (user.username or '').lower()
    with get_connection() as conn:
        cur = conn.execute("SELECT id FROM captains WHERE lower(username)=?", (username,))
        row = cur.fetchone()
        if row is None:
            await update.message.reply_text("Вы не назначены капитаном. Обратитесь к ведущему.")
            return
        conn.execute("UPDATE captains SET telegram_user_id=?, chat_id=? WHERE id=?", (user.id, chat.id, row["id"]))
        conn.commit()
    await update.message.reply_text(
        "🎯 Готово! Вы зарегистрированы как капитан своей команды.\n"
        "Когда ведущий запустит вопрос — получите кнопки ответа и таймер ⏱ 60с."
    )


def _build_answer_keyboard(question_id: int, options: list[str], multi: bool) -> InlineKeyboardMarkup:
    buttons = []
    for idx, label in enumerate(options):
        buttons.append([InlineKeyboardButton(text=label, callback_data=json.dumps({"qid": question_id, "opt": idx}))])
    if multi:
        buttons.append([InlineKeyboardButton(text="Готово", callback_data=json.dumps({"qid": question_id, "done": True}))])
    return InlineKeyboardMarkup(buttons)


async def send_question_to_captains(game_id: int, question: dict, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_connection() as conn:
        caps = conn.execute("SELECT telegram_user_id, chat_id, team_id FROM captains WHERE telegram_user_id IS NOT NULL AND chat_id IS NOT NULL").fetchall()
    text = question["text"] + "\n\n" + "\n".join(question["options"]) + ("\n\nВремя ответа: 60 секунд" )
    multi = question.get("type") in ("multi", "case")
    kb = _build_answer_keyboard(question["id"], [chr(65+i) for i in range(len(question["options"]))], multi)
    for c in caps:
        try:
            await context.bot.send_message(chat_id=c["chat_id"], text=text, reply_markup=kb)
        except Exception:
            continue


async def begin_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /q <question_id>")
        return
    qid = int(context.args[0])
    with get_connection() as conn:
        q = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        if not q:
            await update.message.reply_text("Вопрос не найден")
            return
        game = conn.execute("SELECT * FROM games WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
        if not game:
            await update.message.reply_text("Активная игра не найдена")
            return
        conn.execute("UPDATE games SET current_question_id=?, current_question_deadline=datetime('now','+60 seconds') WHERE id=?", (qid, game["id"]))
        conn.commit()
    await send_question_to_captains(game["id"], {"id": q["id"], "text": q["text"], "options": json.loads(q["options_json"]), "type": q.get("type", "single")}, context)
    await update.message.reply_text(
        f"📣 Вопрос <b>{qid}</b> отправлен капитанам! ⏱ 60 сек.\n"
        "Жди ответы команд. По истечении времени нажми ‘Стоп приёма’.",
        parse_mode="HTML",
        reply_markup=_host_keyboard(),
    )


async def end_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_connection() as conn:
        game = conn.execute("SELECT * FROM games WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
        if not game or not game["current_question_id"]:
            await update.message.reply_text("Текущий вопрос не активен")
            return
        conn.execute("UPDATE games SET current_question_deadline=datetime('now') WHERE id=?", (game["id"],))
        conn.commit()
    await update.message.reply_text(
        "⛔ Приём ответов остановлен.\n"
        "✔ Можешь показать результаты в админке или запустить следующий вопрос.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="Открыть результаты", web_app=WebAppInfo(url=f"{BASE_URL}/admin"))]]
        ),
    )


async def on_answer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    await query.answer()
    try:
        data = json.loads(query.data)
    except Exception:
        return
    qid = data.get("qid")
    option_idx = data.get("opt")
    with get_connection() as conn:
        cap = conn.execute("SELECT * FROM captains WHERE telegram_user_id=?", (user.id,)).fetchone()
        if not cap or not cap["team_id"]:
            await query.edit_message_text("Вы не привязаны к команде.")
            return
        game = conn.execute("SELECT * FROM games WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
        if not game or not game["current_question_id"]:
            await query.edit_message_text("Нет активного вопроса.")
            return
        if game["current_question_deadline"] and conn.execute("SELECT datetime(?) < datetime('now')", (game["current_question_deadline"],)).fetchone()[0]:
            await query.edit_message_text("Время ответа истекло.")
            return
        exists = conn.execute("SELECT 1 FROM answers WHERE team_id=? AND question_id=?", (cap["team_id"], qid)).fetchone()
        if exists:
            await query.edit_message_text("Ответ уже зафиксирован от вашей команды.")
            return
        if option_idx is None:
            await query.edit_message_text("Выберите вариант.")
            return
        conn.execute(
            "INSERT INTO answers(game_id, question_id, team_id, captain_user_id, option_index, answered_at) VALUES (?,?,?,?,?,datetime('now'))",
            (game["id"], qid, cap["team_id"], user.id, int(option_idx)),
        )
        conn.commit()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.edit_message_text("Ответ принят. Изменение запрещено.")


# ===== Меню ведущего (кнопки) =====
CHOOSING, NEWGAME_NAME, ADDTEAM_DATA, QUESTION_ID, ADMIN_ADD, ADMIN_DEL = range(6)


def _is_admin(update: Update) -> bool:
    username = (update.effective_user.username or "").lower()
    uid = update.effective_user.id
    with get_connection() as conn:
        row = conn.execute("SELECT 1 FROM admins WHERE telegram_user_id=?", (uid,)).fetchone()
    return bool(row) or (ADMIN_USERNAMES and username in ADMIN_USERNAMES)


def _host_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["Новая игра", "Добавить команду"],
            ["Запустить вопрос", "Стоп приёма"],
            ["Счёт", "Экспорт"],
            ["Админ‑панель", "Экран зала"],
            ["Админы"],
            ["Отмена"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _admins_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["Добавить админа", "Удалить админа"], ["Список админов"], ["Назад"]], resize_keyboard=True
    )


async def host_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await update.message.reply_text("Доступ только для ведущего.")
        return ConversationHandler.END
    await update.message.reply_text(
        "🎛 Меню ведущего\n\n"
        "Шаги запуска: \n"
        "1) ‘Новая игра’ — создать матч\n"
        "2) ‘Добавить команду’ — привязать @капитана\n"
        "3) Капитану: Start → /register\n"
        "4) ‘Запустить вопрос’ — рассылка с таймером 60с\n",
        reply_markup=_host_keyboard()
    )
    return CHOOSING


async def host_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Новая игра":
        await update.message.reply_text("Название игры?", reply_markup=ReplyKeyboardRemove())
        return NEWGAME_NAME
    if text == "Добавить команду":
        await update.message.reply_text("Формат: НазваниеКоманды @username_капитана", reply_markup=ReplyKeyboardRemove())
        return ADDTEAM_DATA
    if text == "Запустить вопрос":
        await update.message.reply_text("Укажи ID вопроса (число):", reply_markup=ReplyKeyboardRemove())
        return QUESTION_ID
    if text == "Стоп приёма":
        await end_question(update, context)
        await update.message.reply_text("Ок.", reply_markup=_host_keyboard())
        return CHOOSING
    if text == "Счёт":
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT t.name AS team,
                       SUM(CASE WHEN q.type='single' AND a.option_index = q.correct_index THEN 1 ELSE 0 END) AS pts
                FROM teams t
                LEFT JOIN answers a ON a.team_id = t.id
                LEFT JOIN questions q ON q.id = a.question_id
                GROUP BY t.name
                ORDER BY pts DESC, team ASC
                """
            ).fetchall()
        lines = [f"{r['team']}: {int(r['pts'] or 0)}" for r in rows]
        await update.message.reply_text("Текущий счёт:\n" + ("\n".join(lines) if lines else "пока пусто"), reply_markup=_host_keyboard())
        return CHOOSING
    if text == "Экспорт":
        await update.message.reply_text(
            "Экспорт CSV:",
            reply_markup=_host_keyboard()
        )
        # Параллельно отправим кнопку-ссылку
        await update.message.reply_text(
            "Открыть экспорт:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(text="Экспорт CSV", url=f"{BASE_URL}/admin/export.csv")]])
        )
        return CHOOSING
    if text == "Админ‑панель":
        await update.message.reply_text("Админ‑панель:", reply_markup=_host_keyboard())
        # Кнопка, открывающая веб внутри Telegram (Web App)
        await update.message.reply_text(
            "Открыть админку во встроенном окне:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(text="Открыть /admin", web_app=WebAppInfo(url=f"{BASE_URL}/admin"))]])
        )
        # Дополнительно обычная ссылка на случай, если клиент без WebApp
        await update.message.reply_text(
            "Если кнопка не открывается, нажми ссылку:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(text="/admin (ссылка)", url=f"{BASE_URL}/admin")]])
        )
        return CHOOSING
    if text == "Экран зала":
        await update.message.reply_text("Экран зала:", reply_markup=_host_keyboard())
        await update.message.reply_text(
            "Открыть экран зала во встроенном окне:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(text="Открыть /hall", web_app=WebAppInfo(url=f"{BASE_URL}/hall"))]])
        )
        await update.message.reply_text(
            "Если кнопка не открывается, нажми ссылку:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(text="/hall (ссылка)", url=f"{BASE_URL}/hall")]])
        )
        return CHOOSING
    if text == "Админы":
        await update.message.reply_text("Управление администраторами:", reply_markup=_admins_keyboard())
        return CHOOSING
    if text == "Добавить админа":
        await update.message.reply_text("Отправь @username или user_id:", reply_markup=ReplyKeyboardRemove())
        return ADMIN_ADD
    if text == "Удалить админа":
        await update.message.reply_text("Отправь @username или user_id:", reply_markup=ReplyKeyboardRemove())
        return ADMIN_DEL
    if text == "Список админов":
        with get_connection() as conn:
            rows = conn.execute("SELECT COALESCE(username,'' ) AS u, telegram_user_id AS id FROM admins ORDER BY u ASC, id ASC").fetchall()
        lines = [f"@{r['u']} (id {r['id']})" if r['u'] else f"id {r['id']}" for r in rows]
        await update.message.reply_text("Админы:\n" + ("\n".join(lines) if lines else "пока пусто"), reply_markup=_admins_keyboard())
        return CHOOSING
    if text == "Назад":
        await update.message.reply_text("Меню:", reply_markup=_host_keyboard())
        return CHOOSING
    if text == "Отмена":
        await update.message.reply_text("Готово.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    await update.message.reply_text("Не понял. Выбери пункт меню.", reply_markup=_host_keyboard())
    return CHOOSING


async def host_newgame_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Введите название.")
        return NEWGAME_NAME
    # Передадим как аргумент и используем существующий обработчик
    context.args = [name]
    await newgame(update, context)
    await update.message.reply_text("Создано.", reply_markup=_host_keyboard())
    return CHOOSING


async def host_addteam_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    parts = raw.split()
    if len(parts) < 2 or not parts[-1].startswith('@'):
        await update.message.reply_text("Нужно: НазваниеКоманды @username", reply_markup=_host_keyboard())
        return CHOOSING
    captain_mention = parts[-1]
    team_name = raw[: raw.rfind(captain_mention)].strip()
    context.args = [team_name, captain_mention]
    await addteam(update, context)
    await update.message.reply_text("Ок.", reply_markup=_host_keyboard())
    return CHOOSING


async def host_question_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = update.message.text.strip()
    if not arg.isdigit():
        await update.message.reply_text("Нужно число.", reply_markup=_host_keyboard())
        return CHOOSING
    context.args = [arg]
    await begin_question(update, context)
    await update.message.reply_text("Отправил.", reply_markup=_host_keyboard())
    return CHOOSING


async def host_admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    username = None
    uid = None
    if raw.startswith('@'):
        username = raw.lstrip('@').lower()
    elif raw.isdigit():
        uid = int(raw)
    with get_connection() as conn:
        if uid is None:
            # попробуем найти по username среди зарегистрированных капитанов (чтобы подхватить user_id)
            cap = conn.execute("SELECT telegram_user_id FROM captains WHERE lower(username)=?", (username or '',)).fetchone()
            uid = cap["telegram_user_id"] if cap and cap["telegram_user_id"] else None
        conn.execute("INSERT OR IGNORE INTO admins(telegram_user_id, username) VALUES (?, ?)", (uid, username))
        if uid is None:
            # добавим строчку с username, uid заполнится позже при первом взаимодействии
            conn.execute("INSERT OR IGNORE INTO admins(username) VALUES (?)", (username,))
        conn.commit()
    await update.message.reply_text("Админ добавлен (если указан только username — id подтянется позже).", reply_markup=_admins_keyboard())
    return CHOOSING


async def host_admin_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw.startswith('@'):
        username = raw.lstrip('@').lower()
        with get_connection() as conn:
            conn.execute("DELETE FROM admins WHERE lower(username)=?", (username,))
            conn.commit()
    elif raw.isdigit():
        with get_connection() as conn:
            conn.execute("DELETE FROM admins WHERE telegram_user_id=?", (int(raw),))
            conn.commit()
    await update.message.reply_text("Готово.", reply_markup=_admins_keyboard())
    return CHOOSING


def build_application() -> Application | None:
    if not BOT_TOKEN:
        return None
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    # Команды бэкап
    app.add_handler(CommandHandler("newgame", newgame))
    app.add_handler(CommandHandler("addteam", addteam))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("q", begin_question))
    app.add_handler(CommandHandler("stop", end_question))
    app.add_handler(CallbackQueryHandler(on_answer_callback))

    # Меню ведущего
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(Меню|меню|ведущий|Ведущий)$"), host_entry), CommandHandler("host", host_entry)],
        states={
            0: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_choose)],
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_newgame_name)],
            2: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_addteam_data)],
            3: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_question_id)],
            4: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_admin_add)],
            5: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_admin_del)],
        },
        fallbacks=[MessageHandler(filters.Regex("^(Отмена|Назад)$"), host_choose)],
    )
    app.add_handler(conv)
    return app


async def run_polling(app: Application) -> None:
    await app.initialize()
    await app.start()
    try:
        await app.updater.start_polling()
        # Работает, пока не отменят
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


