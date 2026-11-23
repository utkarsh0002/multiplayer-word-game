import asyncio
import json
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect

from rooms import Player, Room, RoomError, RoomStore, Settings
from rounds import MAX_ANSWER_LENGTH, Round


class Client:
    def __init__(self) -> None:
        self.messages: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=32)
        self.overloaded = False

    def send(self, message: dict) -> None:
        if self.overloaded:
            return
        if self.messages.full():
            self.overloaded = True
            while not self.messages.empty():
                self.messages.get_nowait()
            self.messages.put_nowait(None)
            return
        self.messages.put_nowait(message)

    async def write_messages(self, socket: WebSocket) -> None:
        while True:
            message = await self.messages.get()
            if message is None:
                await socket.close(code=1013)
                return
            await socket.send_json(message)


async def receive_message(socket: WebSocket) -> dict:
    event = await socket.receive()
    if event["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(event.get("code", 1000))

    text = event.get("text")
    if text is None or len(text.encode("utf-8")) > 4096:
        raise RoomError("invalid_message", "Send a JSON text message of at most 4096 bytes.")
    try:
        message = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise RoomError("invalid_message", "The message is not valid JSON.") from error
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise RoomError("invalid_message", "Each message needs a type.")
    return message


def error_message(error: RoomError) -> dict:
    return {"type": "error", "code": error.code, "message": str(error)}


def require_round_number(message: dict) -> int:
    number = message["round_number"]
    if type(number) is not int or number < 1:
        raise RoomError("invalid_message", "Send a positive round number.")
    return number


class Lobby:
    def __init__(self, rooms: RoomStore) -> None:
        self.rooms = rooms
        self.clients: dict[str, Client] = {}
        self.round_timers: dict[str, asyncio.TimerHandle] = {}

    def admit(self, message: dict) -> tuple[Room, Player]:
        match message["type"]:
            case "create" if message.keys() == {"type"}:
                return self.rooms.create(Settings())
            case "create" if message.keys() == {"type", "settings"}:
                return self.rooms.create(Settings.parse(message["settings"]))
            case "join" if message.keys() == {"type", "code"}:
                return self.rooms.join(message["code"])
            case "resume" if message.keys() == {"type", "code", "token"}:
                return self.rooms.resume(message["code"], message["token"])
            case _:
                raise RoomError("invalid_message", "First create, join, or rejoin a room.")

    async def identify(self, socket: WebSocket) -> tuple[Room, Player]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        while True:
            try:
                message = await asyncio.wait_for(receive_message(socket), deadline - loop.time())
                return self.admit(message)
            except RoomError as error:
                await socket.send_json(error_message(error))

    def broadcast(self, room: Room) -> None:
        message = {"type": "state", "room": room.snapshot()}
        for player in room.players:
            client = self.clients.get(player.id)
            if client is not None:
                client.send(message)

    def schedule_deadline(self, room: Room, current_round: Round) -> None:
        loop = asyncio.get_running_loop()
        self.round_timers[room.code] = loop.call_later(
            current_round.seconds_remaining(), self.on_deadline, room, current_round
        )

    def start_ready_round(self, room: Room) -> None:
        current_round = room.start_if_ready()
        if current_round is not None:
            self.schedule_deadline(room, current_round)

    def on_deadline(self, room: Room, current_round: Round) -> None:
        if room.phase != "active" or room.round is not current_round:
            return
        if not self.expire_round_if_due(room):
            # The event loop can fire a scheduled callback slightly early.
            self.schedule_deadline(room, current_round)

    def finish_round(self, room: Room, reason: str) -> bool:
        if not room.finish_round(reason):
            return False
        timer = self.round_timers.pop(room.code, None)
        if timer is not None:
            timer.cancel()
        self.broadcast(room)
        return True

    def expire_round_if_due(self, room: Room) -> bool:
        return (
            room.phase == "active" and room.round is not None and room.round.seconds_remaining() == 0
            and self.finish_round(room, "timeout")
        )

    def cancel_round_timers(self) -> None:
        for timer in self.round_timers.values():
            timer.cancel()
        self.round_timers.clear()

    def apply_round_action(self, client: Client, room: Room, player: Player, message: dict) -> None:
        number = require_round_number(message)
        if room.phase in ("results", "finished"):
            return
        current_round = room.round
        if room.phase != "active" or current_round is None:
            raise RoomError("round_inactive", "There is no active round.")
        if number != current_round.number:
            raise RoomError("stale_round", "That action is for a different round.")
        if message["type"] == "done":
            self.finish_round(room, "done")
            return
        category = message["category"]
        if not current_round.save_draft(player.id, category, message["answer"]):
            raise RoomError("invalid_draft", f"Choose a round category and enter up to {MAX_ANSWER_LENGTH} characters.")
        client.send({"type": "draft_saved", "round_number": number, "category": category})

    async def read_commands(self, socket: WebSocket, client: Client, room: Room, player: Player) -> None:
        while True:
            try:
                message = await receive_message(socket)
                self.expire_round_if_due(room)
                match message["type"]:
                    case "start" if message.keys() == {"type"}:
                        current_round = room.start(player)
                        self.schedule_deadline(room, current_round)
                        self.broadcast(room)
                    case "update_settings" if message.keys() == {"type", "settings"}:
                        room.update_settings(player, message["settings"])
                        self.broadcast(room)
                    case "draft" if message.keys() == {"type", "round_number", "category", "answer"}:
                        self.apply_round_action(client, room, player, message)
                    case "done" if message.keys() == {"type", "round_number"}:
                        self.apply_round_action(client, room, player, message)
                    case "ready" if message.keys() == {"type", "round_number"}:
                        if room.mark_ready(player, require_round_number(message)):
                            self.start_ready_round(room)
                            self.broadcast(room)
                    case "end_match" if message.keys() == {"type", "round_number"}:
                        if room.end_match(require_round_number(message)):
                            self.broadcast(room)
                    case _:
                        raise RoomError("invalid_message", "That action is not supported.")
            except RoomError as error:
                client.send(error_message(error))

    async def connect(self, socket: WebSocket) -> None:
        origin = socket.headers.get("origin")
        scheme = "https" if socket.url.scheme == "wss" else "http"
        if origin is not None and origin != f"{scheme}://{socket.headers.get('host')}":
            await socket.close(code=1008)
            return

        await socket.accept()
        try:
            room, player = await self.identify(socket)
            client = Client()
            self.clients[player.id] = client
            try:
                client.send({"type": "session", "code": room.code, "player_id": player.id, "token": player.token})
                if not self.expire_round_if_due(room):
                    self.start_ready_round(room)
                    if room.phase == "active" and room.round is not None:
                        client.send({
                            "type": "drafts",
                            "round_number": room.round.number,
                            "answers": room.round.drafts[player.id].copy(),
                        })
                    self.broadcast(room)
                reader = asyncio.create_task(self.read_commands(socket, client, room, player))
                writer = asyncio.create_task(client.write_messages(socket))
                try:
                    done, _ = await asyncio.wait((reader, writer), return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    reader.cancel()
                    writer.cancel()
                    await asyncio.gather(reader, writer, return_exceptions=True)
            finally:
                del self.clients[player.id]
                self.rooms.disconnect(room, player)
                self.broadcast(room)
        except asyncio.TimeoutError:
            with suppress(WebSocketDisconnect, OSError):
                await socket.close(code=1008)
        except (WebSocketDisconnect, OSError):
            pass

    async def prune_empty_rooms(self) -> None:
        while True:
            await asyncio.sleep(30)
            self.rooms.prune()
