import re
import secrets
from dataclasses import asdict, dataclass, field
from time import monotonic
from uuid import uuid4

from rounds import Round
from word_lists import WORD_LISTS


CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CATEGORIES = tuple(WORD_LISTS)
EMPTY_ROOM_GRACE = 600


class RoomError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Settings:
    categories: tuple[str, ...] = CATEGORIES
    duration: int = 60
    mode: str = "fixed"
    rounds: int | None = 5

    @classmethod
    def parse(cls, data: object) -> "Settings":
        if not isinstance(data, dict) or data.keys() != {"categories", "duration", "mode", "rounds"}:
            raise RoomError("invalid_settings", "Send categories, a round timer, a mode, and a round count.")

        categories = data["categories"]
        if (
            not isinstance(categories, list)
            or not 1 <= len(categories) <= len(CATEGORIES)
            or any(not isinstance(category, str) or category not in CATEGORIES for category in categories)
            or len(set(categories)) != len(categories)
        ):
            raise RoomError("invalid_settings", "Choose at least one available category without duplicates.")

        duration = data["duration"]
        if type(duration) is not int or not 15 <= duration <= 180:
            raise RoomError("invalid_settings", "Choose a timer between 15 and 180 seconds.")

        mode, rounds = data["mode"], data["rounds"]
        if mode == "fixed":
            if type(rounds) is not int or not 1 <= rounds <= 20:
                raise RoomError("invalid_settings", "Choose between 1 and 20 rounds.")
        elif mode != "endless" or rounds is not None:
            raise RoomError("invalid_settings", "Choose Fixed rounds or Endless mode.")

        return cls(tuple(categories), duration, mode, rounds)


@dataclass
class Player:
    label: str
    id: str = field(default_factory=lambda: uuid4().hex)
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24), repr=False)
    connected: bool = True
    match_score: int = 0


@dataclass
class Room:
    code: str
    settings: Settings
    players: list[Player]
    phase: str = "lobby"
    empty_since: float | None = None
    round: Round | None = None
    ready_players: set[str] = field(default_factory=set)

    @property
    def host(self) -> Player:
        return self.players[0]

    def snapshot(self) -> dict:
        state = {
            "code": self.code,
            "phase": self.phase,
            "settings": asdict(self.settings),
            "players": [
                {"id": player.id, "label": player.label, "host": player is self.host, "connected": player.connected}
                for player in self.players
            ],
        }
        if self.round is not None:
            state["round"] = self.round.snapshot()
        if self.phase in ("results", "finished"):
            state["match_scores"] = {player.id: player.match_score for player in self.players}
        if self.phase == "results":
            state["ready"] = {player.id: player.id in self.ready_players for player in self.players}
        if self.phase == "finished":
            state["final_result"] = self._final_result()
        return state

    def require_host_in_lobby(self, player: Player) -> None:
        if player is not self.host:
            raise RoomError("host_only", "Only the host can change settings or start the game.")
        if self.phase != "lobby":
            raise RoomError("room_started", "This room has already started.")

    def update_settings(self, player: Player, settings: object) -> None:
        self.require_host_in_lobby(player)
        self.settings = Settings.parse(settings)

    def start(self, player: Player) -> Round:
        self.require_host_in_lobby(player)
        if len(self.players) != 2 or not all(player.connected for player in self.players):
            raise RoomError("players_required", "Both players must be connected to start.")
        return self._begin_round()

    def _begin_round(self) -> Round:
        number = 1 if self.round is None else self.round.number + 1
        self.round = Round.begin(self.settings.categories, self.settings.duration, [player.id for player in self.players])
        self.round.number = number
        self.ready_players.clear()
        self.phase = "active"
        return self.round

    def start_if_ready(self) -> Round | None:
        if self.phase == "results" and all(
            player.connected and player.id in self.ready_players for player in self.players
        ):
            return self._begin_round()
        return None

    def _accepts_results_action(self, number: int) -> bool:
        """Ignore late actions and reject actions that cannot apply yet."""
        if self.round is not None:
            if number < self.round.number:
                return False
            if number > self.round.number:
                raise RoomError("stale_round", "That action is for a different round.")
        if self.phase == "finished":
            return False
        if self.phase != "results":
            raise RoomError("results_required", "Wait for the round to end before using this action.")
        return True

    def mark_ready(self, player: Player, number: int) -> bool:
        if not self._accepts_results_action(number) or player.id in self.ready_players:
            return False
        self.ready_players.add(player.id)
        return True

    def end_match(self, number: int) -> bool:
        if not self._accepts_results_action(number):
            return False
        if self.settings.mode != "endless":
            raise RoomError("endless_only", "Only an Endless match can be ended early.")
        self.ready_players.clear()
        self.phase = "finished"
        return True

    def _final_result(self) -> dict:
        first, second = self.players
        winner_id = None
        if first.match_score != second.match_score:
            winner_id = max(self.players, key=lambda player: player.match_score).id
        return {"winner_id": winner_id, "rounds_played": self.round.number}

    def finish_round(self, reason: str) -> bool:
        """Freeze answers and add points only on the first completion."""
        if self.phase != "active" or self.round is None:
            return False
        round_scores = self.round.submit(reason)
        for player in self.players:
            player.match_score += round_scores[player.id]
        reached_limit = self.settings.mode == "fixed" and self.round.number == self.settings.rounds
        self.phase = "finished" if reached_limit else "results"
        return True


class RoomStore:
    """Changes are synchronous and run on one server event loop."""

    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def create(self, settings: Settings) -> tuple[Room, Player]:
        self.prune()
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
            if code not in self.rooms:
                break
        host = Player("Player 1")
        room = Room(code, settings, [host])
        self.rooms[code] = room
        return room, host

    def find(self, code: object) -> Room:
        if not isinstance(code, str) or not code.isascii():
            raise RoomError("invalid_code", "Enter the six-character room code.")
        code = code.strip().upper()
        if len(code) != 6 or any(character not in CODE_ALPHABET for character in code):
            raise RoomError("invalid_code", "Enter the six-character room code.")
        self.prune()
        room = self.rooms.get(code)
        if room is None:
            raise RoomError("room_not_found", "That room could not be found.")
        return room

    def join(self, code: object) -> tuple[Room, Player]:
        room = self.find(code)
        if room.phase != "lobby":
            raise RoomError("room_started", "This room has already started.")
        if len(room.players) == 2:
            raise RoomError("room_full", "This room already has two players.")
        guest = Player("Player 2")
        room.players.append(guest)
        room.empty_since = None
        return room, guest

    def resume(self, code: object, token: object) -> tuple[Room, Player]:
        room = self.find(code)
        if isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_-]{32}", token):
            for player in room.players:
                if secrets.compare_digest(player.token, token):
                    if player.connected:
                        raise RoomError("duplicate_connection", "This player is already connected in another window.")
                    player.connected = True
                    room.empty_since = None
                    return room, player
        raise RoomError("invalid_session", "This browser session could not rejoin the room.")

    def disconnect(self, room: Room, player: Player) -> None:
        player.connected = False
        if not any(player.connected for player in room.players):
            room.empty_since = monotonic()

    def prune(self) -> None:
        expires_before = monotonic() - EMPTY_ROOM_GRACE
        expired = [code for code, room in self.rooms.items() if room.empty_since is not None and room.empty_since <= expires_before]
        for code in expired:
            del self.rooms[code]
