from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.bot import build_application, run_polling
from app.routers import admin as admin_router
from app.routers import hall as hall_router


app = FastAPI(title="Викторина")


# Роуты
app.include_router(hall_router.router)
app.include_router(admin_router.router)


# Статика и шаблоны
BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    # seed admin if provided
    import os
    from app.db import get_connection
    seed_admin_id = int(os.getenv("SEED_ADMIN_ID", "0")) or None
    if seed_admin_id:
        with get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO admins(telegram_user_id) VALUES (?)", (seed_admin_id,))
            conn.commit()
    # Старт Telegram-бота (если есть токен)
    tg_app = build_application()
    if tg_app is not None:
        loop = asyncio.get_event_loop()
        app.state._tg_task = loop.create_task(run_polling(tg_app))
        app.state.tg_app = tg_app


@app.on_event("shutdown")
async def on_shutdown() -> None:
    tg_task = getattr(app.state, "_tg_task", None)
    if tg_task is not None:
        tg_task.cancel()
        with contextlib.suppress(Exception):
            await tg_task


@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/admin")


