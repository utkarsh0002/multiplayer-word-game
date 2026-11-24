from collections import defaultdict, deque
from dataclasses import dataclass
from time import monotonic


MAX_CHAT_BYTES = 500
MAX_CHAT_MESSAGES = 50
CHAT_BURST = 3
CHAT_REFILL_SECONDS = 2


class ChatError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _SeatRateLimit:
    tokens: float = CHAT_BURST
    updated_at: float | None = None
    next_notice_at: float = float("-inf")

    def allow_send(self, now: float) -> bool:
        if self.updated_at is not None:
            self.tokens = min(CHAT_BURST, self.tokens + (now - self.updated_at) / CHAT_REFILL_SECONDS)
        self.updated_at = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        if now >= self.next_notice_at:
            self.next_notice_at = now + CHAT_REFILL_SECONDS
            raise ChatError("chat_rate_limited", "You are sending messages too quickly. Try again in a moment.")
        return False


class RoomChat:
    def __init__(self) -> None:
        self._messages: deque[dict] = deque(maxlen=MAX_CHAT_MESSAGES)
        self._sequence = 0
        self._rate_limits: defaultdict[str, _SeatRateLimit] = defaultdict(_SeatRateLimit)

    def history(self) -> list[dict]:
        return list(self._messages)

    def publish(self, player_id: str, text: object) -> dict | None:
        if not isinstance(text, str) or text.splitlines() != [text]:
            raise ChatError("invalid_chat", "Enter a single line of chat text.")
        text = text.strip()
        if not text or len(text.encode("utf-8")) > MAX_CHAT_BYTES:
            raise ChatError("invalid_chat", f"Enter a message of up to {MAX_CHAT_BYTES} UTF-8 bytes.")
        if not self._rate_limits[player_id].allow_send(monotonic()):
            return None

        self._sequence += 1
        message = {"seq": self._sequence, "player_id": player_id, "text": text}
        self._messages.append(message)
        return message
