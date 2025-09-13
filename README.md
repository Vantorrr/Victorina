## Викторина — Telegram-бот и экран зала

Минимальный каркас для квиз-бота (Telegram) с веб-"экраном зала" на FastAPI. Сервер и админ в браузере доступны на `http://localhost:8080`, админ — на `http://localhost:8080/admin`.

### Быстрый старт

1) Требования: Python 3.11+

2) Установка:
```bash
cd /Users/pavelgalante/VICTORINA
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3) Переменные окружения:
```bash
export BOT_TOKEN="<TELEGRAM_BOT_TOKEN>"
```

4) Запуск локально:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```
### Деплой на Railway

1) Репозиторий на GitHub.
2) На Railway: New Project → Deploy from Repo.
3) Variables:
   - `BOT_TOKEN` — токен бота
   - `SEED_ADMIN_ID` — Telegram ID первого админа
   - `DATA_DIR` — `/data` (и подключить Railway Volume)
4) Procfile уже добавлен. Web service стартует uvicorn на `${PORT}`.
5) Открыть `https://<railway-app>.up.railway.app/admin`.


Открыть: `http://localhost:8080/hall` (экран зала) и `http://localhost:8080/admin` (админ).

### Структура

```
app/
  __init__.py
  main.py                # FastAPI-приложение, запуск бота на старте
  bot.py                 # Заглушка Telegram-бота (polling)
  db.py                  # Подключение к SQLite и инициализация
  websocket_manager.py   # Broadcast менеджер для WebSocket клиентов
  routers/
    __init__.py
    admin.py             # /admin страница
    hall.py              # /hall и ws канал
  templates/
    admin.html
    hall.html
  static/
    style.css
```

### Дальше

- Добавить схему БД: игры, команды, капитаны, вопросы, ответы, очки
- Реализовать команды ведущего и поток вопросов/таймер/подсчёт очков
- Связать бота с веб-экраном через broadcast событий


