import re
import secrets
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from time import monotonic
from uuid import uuid4

from rounds import Round
from word_lists import WORD_LISTS


CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CATEGORIES = tuple(WORD_LISTS)
EMPTY_ROOM_GRACE = 600
MAX_ROOMS = 1024
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32}")


def token_hash(token: str) -> bytes:
    return sha256(token.encode("ascii")).digest()


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
    community_word_approval: bool = False

    @classmethod
    def parse(cls, data: object) -> "Settings":
        required = {"categories", "duration", "mode", "rounds"}
        if not isinstance(data, dict) or data.keys() not in (required, required | {"community_word_approval"}):
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

        approval = data.get("community_word_approval", False)
        if type(approval) is not bool:
            raise RoomError("invalid_settings", "Community word approval must be true or false.")
        return cls(tuple(categories), duration, mode, rounds, approval)

    def snapshot(self) -> dict:
        settings = asdict(self)
        if not self.community_word_approval:
            # Keep the settings shape unchanged for rooms using strict validation.
            del settings["community_word_approval"]
        return settings


@dataclass
class Player:
    label: str
    token_hash: bytes = field(repr=False)
    id: str = field(default_factory=lambda: uuid4().hex)
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
            "settings": self.settings.snapshot(),
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
        """Freeze the active round and either begin review or award scores."""
        if self.phase != "active" or self.round is None:
            return False
        if self.settings.community_word_approval and self.round.begin_review(reason):
            self.phase = "review"
        else:
            self._score_round(self.round, reason)
        return True

    def review_answer(self, reviewer: Player, number: int, target: str, category: str, decision: str) -> bool:
        current_round = self.round
        if current_round is not None and number != current_round.number:
            raise RoomError("stale_round", "That action is for a different round.")
        review = current_round.review if current_round is not None else None
        if review is not None and self.phase in ("results", "finished"):
            return False
        if self.phase != "review" or review is None:
            raise RoomError("review_required", "There is no round waiting for review.")
        if target == reviewer.id:
            raise RoomError("review_forbidden", "You can only review the other player's answers.")
        entry = review.entries.get((target, category))
        if entry is None:
            raise RoomError("invalid_review", "That answer is not up for review.")
        if entry.status != "pending":
            return False
        entry.status = "accepted" if decision == "accept" else "rejected"
        return True

    def finish_review(self) -> bool:
        current_round = self.round
        if self.phase != "review" or current_round is None or current_round.review is None:
            return False
        review, reason = current_round.review, current_round.ended_by
        if reason is None or (review.has_pending() and review.seconds_remaining() > 0):
            return False
        review.reject_undecided()
        self._score_round(current_round, reason)
        return True

    def _score_round(self, current_round: Round, reason: str) -> None:
        round_scores = current_round.submit(reason)
        for player in self.players:
            player.match_score += round_scores[player.id]
        reached_limit = self.settings.mode == "fixed" and current_round.number == self.settings.rounds
        self.phase = "finished" if reached_limit else "results"


class RoomStore:
    """Changes are synchronous and run on one server event loop."""

    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def create(self, settings: Settings, *, token: str | None = None) -> tuple[Room, Player]:
        self.prune()
        if len(self.rooms) >= MAX_ROOMS:
            raise RoomError("room_capacity", "The server has reached its room limit. Try again later.")
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
            if code not in self.rooms:
                break
        host = Player("Player 1", token_hash(token if token is not None else secrets.token_urlsafe(24)))
        room = Room(code, settings, [host])
        self.rooms[code] = room
        return room, host

    def find(self, code: object) -> Room:
        if not isinstance(code, str) or not code.isascii():
            raise RoomError("invalid_code", "Enter the six-character room code.")
        code = code.strip().upper()
        if len(code) != 6 or any(character not in CODE_ALPHABET for character in code):
            raise RoomError("invalid_code", "Enter the six-character room code.")
        room = self.rooms.get(code)
        if room is not None and self._expired(room, monotonic()):
            del self.rooms[code]
            room = None
        if room is None:
            raise RoomError("room_not_found", "That room could not be found.")
        return room

    def join(self, code: object, *, token: str | None = None) -> tuple[Room, Player]:
        room = self.find(code)
        if room.phase != "lobby":
            raise RoomError("room_started", "This room has already started.")
        if len(room.players) == 2:
            raise RoomError("room_full", "This room already has two players.")
        guest = Player("Player 2", token_hash(token if token is not None else secrets.token_urlsafe(24)))
        room.players.append(guest)
        room.empty_since = None
        return room, guest

    def resume(self, code: object, token: object) -> tuple[Room, Player]:
        room = self.find(code)
        if isinstance(token, str) and TOKEN_PATTERN.fullmatch(token):
            supplied_hash = token_hash(token)
            for player in room.players:
                if secrets.compare_digest(player.token_hash, supplied_hash):
                    if player.connected:
                        raise RoomError("duplicate_connection", "This player is already connected in another window.")
                    player.connected = True
                    room.empty_since = None
                    return room, player
        raise RoomError("invalid_session", "This browser session could not rejoin the room.")

    def disconnect(self, room: Room, player: Player) -> bool:
        if self.rooms.get(room.code) is not room or not player.connected:
            return False
        if not any(seat is player for seat in room.players):
            return False
        player.connected = False
        if not any(player.connected for player in room.players):
            room.empty_since = monotonic()
        return True

    @staticmethod
    def _expired(room: Room, now: float) -> bool:
        return (
            room.empty_since is not None
            and now - room.empty_since >= EMPTY_ROOM_GRACE
            and not any(player.connected for player in room.players)
        )

    def prune(self) -> list[Room]:
        now = monotonic()
        expired = [room for room in self.rooms.values() if self._expired(room, now)]
        for room in expired:
            del self.rooms[room.code]
        return expired
