from __future__ import annotations

from typing import Any, Set

from starlette.websockets import WebSocket, WebSocketState


class WebSocketManager:
    """Простой менеджер подключений для broadcast JSON-сообщений всем клиентам."""

    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)

    async def broadcast_json(self, message: Any) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                if ws.application_state == WebSocketState.CONNECTED:
                    await ws.send_json(message)
                else:
                    dead.append(ws)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


