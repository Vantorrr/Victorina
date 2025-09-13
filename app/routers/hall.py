from __future__ import annotations

from fastapi import APIRouter, WebSocket, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.websocket_manager import WebSocketManager


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
ws_manager = WebSocketManager()


@router.get("/hall", response_class=HTMLResponse)
async def hall_page(request: Request):
    return templates.TemplateResponse("hall.html", {"request": request})


@router.websocket("/ws/hall")
async def hall_ws(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Держим соединение; сообщений от клиента не ждём
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        ws_manager.disconnect(websocket)


async def broadcast_to_hall(message: dict):
    await ws_manager.broadcast_json(message)


