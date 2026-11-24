import json
from math import isfinite

from fastapi import WebSocket, WebSocketDisconnect

from rooms import RoomError


MAX_MESSAGE_BYTES = 4096
MAX_JSON_DEPTH = 32


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("Non-finite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _validate_json_tree(message: dict) -> None:
    pending: list[tuple[object, int]] = [(message, 1)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, str):
            # JSON escapes can decode into unpaired surrogates in otherwise valid text.
            value.encode("utf-8")
        elif isinstance(value, dict):
            if depth > MAX_JSON_DEPTH:
                raise ValueError("JSON nesting is too deep")
            for key, child in value.items():
                key.encode("utf-8")
                pending.append((child, depth + 1))
        elif isinstance(value, list):
            if depth > MAX_JSON_DEPTH:
                raise ValueError("JSON nesting is too deep")
            pending.extend((child, depth + 1) for child in value)


async def receive_message(socket: WebSocket) -> dict:
    event = await socket.receive()
    if event["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(event.get("code", 1000))

    text = event.get("text")
    size_error = f"Send a JSON text message of at most {MAX_MESSAGE_BYTES} bytes."
    if not isinstance(text, str) or len(text) > MAX_MESSAGE_BYTES:
        raise RoomError("invalid_message", size_error)
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise RoomError("invalid_message", "The message must be UTF-8 text.") from None
    if size > MAX_MESSAGE_BYTES:
        raise RoomError("invalid_message", size_error)

    try:
        message = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant, parse_float=_finite_float
        )
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            raise RoomError("invalid_message", "Each message needs a type.")
        _validate_json_tree(message)
    except (ValueError, RecursionError):
        # Suppress parser details because validation errors should not carry client data.
        raise RoomError("invalid_message", "The message is not valid JSON.") from None
    return message
