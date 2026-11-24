import asyncio
import logging
import secrets

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from chat import ChatError
from protocol import receive_message
from rooms import Player, Room, RoomError, RoomStore, Settings
from rounds import MAX_ANSWER_LENGTH, Round


logger = logging.getLogger(__name__)
MAX_ADMISSION_ATTEMPTS = 16
SEND_TIMEOUT = 5


def log_internal_error(operation: str, error: BaseException) -> None:
    # Exception messages and tracebacks can contain payload data or credentials.
    logger.error("internal_error operation=%s exception=%s", operation, type(error).__name__)


async def close_socket(socket: WebSocket, code: int) -> None:
    if socket.application_state is WebSocketState.DISCONNECTED:
        return
    try:
        await asyncio.wait_for(socket.close(code=code), timeout=SEND_TIMEOUT)
    except (WebSocketDisconnect, OSError, asyncio.TimeoutError):
        pass
    except Exception as error:
        log_internal_error("socket_close", error)


class Client:
    def __init__(self) -> None:
        self.messages: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=32)
        self.overloaded = False
        self.chat_subscribed = False

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
                await close_socket(socket, 1013)
                return
            try:
                await asyncio.wait_for(socket.send_json(message), timeout=SEND_TIMEOUT)
            except asyncio.TimeoutError:
                await close_socket(socket, 1013)
                return


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
        self.round_timers: dict[int, asyncio.TimerHandle] = {}

    def admit(self, message: dict) -> tuple[Room, Player, str]:
        match message["type"]:
            case "create" if message.keys() in ({"type"}, {"type", "settings"}):
                settings = Settings.parse(message["settings"]) if "settings" in message else Settings()
                token = secrets.token_urlsafe(24)
                room, player = self.rooms.create(settings, token=token)
            case "join" if message.keys() == {"type", "code"}:
                token = secrets.token_urlsafe(24)
                room, player = self.rooms.join(message["code"], token=token)
            case "resume" if message.keys() == {"type", "code", "token"}:
                token = message["token"]
                room, player = self.rooms.resume(message["code"], token)
            case _:
                raise RoomError("invalid_message", "First create, join, or rejoin a room.")
        return room, player, token

    async def identify(self, socket: WebSocket) -> tuple[Room, Player, str]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        for _ in range(MAX_ADMISSION_ATTEMPTS):
            try:
                message = await asyncio.wait_for(receive_message(socket), deadline - loop.time())
                return self.admit(message)
            except RoomError as error:
                await asyncio.wait_for(socket.send_json(error_message(error)), deadline - loop.time())
        raise asyncio.TimeoutError

    def broadcast(self, room: Room) -> None:
        if self.rooms.rooms.get(room.code) is not room:
            return
        message = {"type": "state", "room": room.snapshot()}
        for player in room.players:
            client = self.clients.get(player.id)
            if client is not None:
                client.send(message)

    def cancel_room_timer(self, room: Room) -> None:
        timer = self.round_timers.pop(id(room), None)
        if timer is not None:
            timer.cancel()

    def schedule_deadline(self, room: Room, current_round: Round, *, retry: bool = False) -> None:
        loop = asyncio.get_running_loop()
        self.cancel_room_timer(room)
        phase = room.phase
        clock = current_round.review if phase == "review" else current_round
        delay = 1 if retry else clock.seconds_remaining()
        self.round_timers[id(room)] = loop.call_later(
            delay, self.on_deadline, room, current_round, retry, phase
        )

    def start_ready_round(self, room: Room) -> None:
        current_round = room.start_if_ready()
        if current_round is not None:
            self.schedule_deadline(room, current_round)

    def on_deadline(self, room: Room, current_round: Round, retry: bool = False, phase: str = "active") -> None:
        if room.phase != phase or room.round is not current_round:
            return
        self.cancel_room_timer(room)
        if self.rooms.rooms.get(room.code) is not room:
            return
        try:
            if not self.expire_deadline_if_due(room):
                # The event loop can fire a scheduled callback slightly early.
                self.schedule_deadline(room, current_round)
        except Exception as error:
            log_internal_error("review_deadline" if phase == "review" else "round_deadline", error)
            if not retry and room.phase == phase and room.round is current_round:
                self.schedule_deadline(room, current_round, retry=True)

    def finish_round(self, room: Room, reason: str) -> bool:
        if not room.finish_round(reason):
            return False
        if room.phase == "review":
            self.schedule_deadline(room, room.round)
        else:
            self.cancel_room_timer(room)
        self.broadcast(room)
        return True

    def finish_review(self, room: Room) -> bool:
        if not room.finish_review():
            return False
        self.cancel_room_timer(room)
        self.broadcast(room)
        return True

    def expire_deadline_if_due(self, room: Room) -> bool:
        if room.phase == "review":
            return self.finish_review(room)
        return self.expire_round_if_due(room)

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
        if room.phase in ("review", "results", "finished"):
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

    def apply_chat(self, client: Client, room: Room, player: Player, message: dict) -> None:
        subscribing = message["type"] == "chat_subscribe"
        expected_keys = {"type"} if subscribing else {"type", "text"}
        if message.keys() != expected_keys:
            raise ChatError("invalid_chat", "Send only the fields required for this chat action.")
        if len(room.players) != 2:
            raise ChatError("chat_unavailable", "Chat opens when both player seats have been claimed.")

        if subscribing:
            if not client.chat_subscribed:
                # Queue the history and subscribe together before another command can publish.
                client.send({"type": "chat_history", "messages": room.chat.history()})
                client.chat_subscribed = True
            return
        if not client.chat_subscribed:
            raise ChatError("chat_not_subscribed", "Subscribe to room chat before sending a message.")

        entry = room.chat.publish(player.id, message["text"])
        if entry is not None:
            event = {"type": "chat_message", "message": entry}
            for seat in room.players:
                recipient = self.clients.get(seat.id)
                if recipient is not None and recipient.chat_subscribed:
                    recipient.send(event)

    async def read_commands(self, socket: WebSocket, client: Client, room: Room, player: Player) -> None:
        while True:
            try:
                message = await receive_message(socket)
                if self.clients.get(player.id) is not client or self.rooms.rooms.get(room.code) is not room:
                    return
                command = message["type"]
                is_chat = command in ("chat_subscribe", "chat_send")
                expired_before_command = not is_chat and self.expire_deadline_if_due(room)
                match command:
                    case "chat_subscribe" | "chat_send":
                        self.apply_chat(client, room, player, message)
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
                    case "ready" | "end_match" if message.keys() == {"type", "round_number"}:
                        self.apply_results_action(room, player, message, expired_before_command)
                    case "review_answer" if message.keys() == {"type", "round_number", "player_id", "category", "decision"}:
                        self.apply_review_answer(room, player, message, expired_before_command)
                    case _:
                        raise RoomError("invalid_message", "That action is not supported.")
            except RoomError as error:
                client.send(error_message(error))
            except ChatError as error:
                client.send({"type": "chat_error", "code": error.code, "message": str(error)})
            # A buffered receive can complete immediately. Give timers and other seats a turn.
            await asyncio.sleep(0)

    def apply_review_answer(self, room: Room, player: Player, message: dict, expired_before_command: bool) -> None:
        number = require_round_number(message)
        target, category, decision = message["player_id"], message["category"], message["decision"]
        if not isinstance(target, str) or not isinstance(category, str) or decision not in ("accept", "reject"):
            raise RoomError("invalid_message", "Send a player, category, and an accept or reject decision.")
        if expired_before_command and room.phase == "review":
            raise RoomError("review_required", "Wait for the review to begin before voting.")
        if room.review_answer(player, number, target, category, decision) and not self.finish_review(room):
            self.broadcast(room)

    def apply_results_action(self, room: Room, player: Player, message: dict, expired_before_command: bool) -> None:
        number = require_round_number(message)
        if expired_before_command and room.phase == "results" and room.round is not None and number == room.round.number:
            raise RoomError("results_required", "Wait for the round to end before using this action.")

        if message["type"] == "ready":
            if not room.mark_ready(player, number):
                return
            self.start_ready_round(room)
        elif not room.end_match(number):
            return
        self.broadcast(room)

    async def run_client(self, socket: WebSocket, client: Client, room: Room, player: Player) -> None:
        tasks = (
            asyncio.create_task(self.read_commands(socket, client, room, player)),
            asyncio.create_task(client.write_messages(socket)),
        )
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        failed = False
        for operation, outcome in zip(("socket_read", "socket_write"), outcomes):
            if isinstance(outcome, Exception) and not isinstance(outcome, (WebSocketDisconnect, OSError)):
                log_internal_error(operation, outcome)
                failed = True
        if failed:
            await close_socket(socket, 1011)

    async def connect(self, socket: WebSocket) -> None:
        try:
            origins = socket.headers.getlist("origin")
            hosts = socket.headers.getlist("host")
            scheme = "https" if socket.url.scheme == "wss" else "http"
            if len(hosts) != 1 or (origins and origins != [f"{scheme}://{hosts[0]}"]):
                await close_socket(socket, 1008)
                return

            await socket.accept()
            try:
                room, player, token = await self.identify(socket)
            except asyncio.TimeoutError:
                await close_socket(socket, 1008)
                return

            client = None
            try:
                client = Client()
                self.clients[player.id] = client
                client.send({"type": "session", "code": room.code, "player_id": player.id, "token": token})
                del token
                if not self.expire_deadline_if_due(room):
                    self.start_ready_round(room)
                    if room.phase == "active" and room.round is not None:
                        client.send({
                            "type": "drafts",
                            "round_number": room.round.number,
                            "answers": room.round.drafts[player.id].copy(),
                        })
                    self.broadcast(room)
                await self.run_client(socket, client, room, player)
            finally:
                if self.clients.get(player.id) is client:
                    self.clients.pop(player.id, None)
                    if self.rooms.disconnect(room, player):
                        self.broadcast(room)
        except (WebSocketDisconnect, OSError):
            pass
        except Exception as error:
            log_internal_error("socket_lifecycle", error)
            await close_socket(socket, 1011)

    async def prune_empty_rooms(self) -> None:
        while True:
            await asyncio.sleep(30)
            try:
                for room in self.rooms.prune():
                    self.cancel_room_timer(room)
            except Exception as error:
                log_internal_error("room_cleanup", error)
