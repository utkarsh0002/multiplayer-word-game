import json
import unittest

from fastapi import WebSocketDisconnect

from protocol import MAX_JSON_DEPTH, MAX_MESSAGE_BYTES, receive_message
from rooms import RoomError


class MessageSocket:
    def __init__(self, event):
        self.event = event

    async def receive(self):
        return self.event


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def parse(self, text):
        return await receive_message(MessageSocket({"type": "websocket.receive", "text": text}))

    async def assert_invalid(self, text):
        with self.assertRaises(RoomError) as error:
            await self.parse(text)
        self.assertEqual(error.exception.code, "invalid_message")

    async def test_accepts_unicode_up_to_utf8_byte_limit(self):
        text = json.dumps({"type": "draft", "answer": "é"}, ensure_ascii=False)
        max_text = text + " " * (MAX_MESSAGE_BYTES - len(text.encode("utf-8")))
        self.assertEqual(await self.parse(max_text), {"type": "draft", "answer": "é"})
        self.assertLessEqual(len(max_text + " "), MAX_MESSAGE_BYTES)
        await self.assert_invalid(max_text + " ")

    async def test_rejects_invalid_frames_and_json_envelopes(self):
        for text in (None, b"{}", "[]", "null", "1", "{}", '{"type":false}', '{"type":'):
            with self.subTest(text=text):
                await self.assert_invalid(text)

    async def test_rejects_duplicate_keys_and_nonfinite_numbers(self):
        invalid = (
            '{"type":"create","type":"join"}',
            '{"type":"create","settings":{"rounds":1,"rounds":2}}',
            '{"type":"ready","round_number":NaN}',
            '{"type":"ready","round_number":Infinity}',
            '{"type":"ready","round_number":-Infinity}',
            '{"type":"ready","round_number":1e999}',
        )
        for text in invalid:
            with self.subTest(text=text):
                await self.assert_invalid(text)

    async def test_rejects_unpaired_surrogates_but_accepts_a_unicode_pair(self):
        for text in (
            '{"type":"draft","answer":"\ud800"}',
            r'{"type":"draft","answer":"\ud800"}',
            r'{"type":"create","settings":{"\udfff":1}}',
        ):
            with self.subTest(text=text):
                await self.assert_invalid(text)
        message = await self.parse(r'{"type":"draft","answer":"\ud83d\ude00"}')
        self.assertEqual(message["answer"], "\U0001f600")

    async def test_rejects_json_beyond_depth_limit(self):
        for depth in (MAX_JSON_DEPTH, 1200):
            with self.subTest(depth=depth):
                text = '{"type":"create","settings":' + "[" * depth + "0" + "]" * depth + "}"
                await self.assert_invalid(text)

    async def test_preserves_websocket_disconnect(self):
        socket = MessageSocket({"type": "websocket.disconnect", "code": 1001})
        with self.assertRaises(WebSocketDisconnect) as error:
            await receive_message(socket)
        self.assertEqual(error.exception.code, 1001)
