import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from lobby import Lobby
from rooms import RoomStore


STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup = asyncio.create_task(app.state.lobby.prune_empty_rooms())
    try:
        yield
    finally:
        app.state.lobby.cancel_round_timers()
        cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup


app = FastAPI(title="Word Rally", lifespan=lifespan)
app.state.rooms = RoomStore()
app.state.lobby = Lobby(app.state.rooms)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=FileResponse, include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws")
async def lobby_socket(socket: WebSocket) -> None:
    await socket.app.state.lobby.connect(socket)
