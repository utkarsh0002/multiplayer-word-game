import asyncio
import json
import socket
import unittest
from unittest.mock import patch

import uvicorn
from websockets.asyncio.client import connect

from main import app
from rooms import EMPTY_ROOM_GRACE, RoomStore, Settings


class RoomCleanupTests(unittest.TestCase):
    def test_room_expires_only_after_every_player_disconnects(self):
        store = RoomStore()
        room, host = store.create(Settings())
        _, guest = store.join(room.code)

        with patch("rooms.monotonic", return_value=0):
            store.disconnect(room, guest)
        with patch("rooms.monotonic", return_value=EMPTY_ROOM_GRACE):
            store.prune()
            self.assertIn(room.code, store.rooms)
            store.disconnect(room, host)
        with patch("rooms.monotonic", return_value=2 * EMPTY_ROOM_GRACE - 1):
            store.prune()
            self.assertIn(room.code, store.rooms)
        with patch("rooms.monotonic", return_value=2 * EMPTY_ROOM_GRACE):
            store.prune()
            self.assertNotIn(room.code, store.rooms)


class WebSocketTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.listener = socket.socket()
        self.addCleanup(self.listener.close)
        self.listener.bind(("127.0.0.1", 0))
        port = self.listener.getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/ws"
        config = uvicorn.Config(app, log_level="critical")
        self.server = uvicorn.Server(config)
        self.server_task = asyncio.create_task(self.server.serve(sockets=[self.listener]))
        self.addAsyncCleanup(self.stop_server)
        await asyncio.wait_for(self.wait_for_server(), timeout=5)

    async def wait_for_server(self):
        while not self.server.started:
            if self.server_task.done():
                await self.server_task
                self.fail("Uvicorn exited before startup completed")
            await asyncio.sleep(0.01)

    async def stop_server(self):
        self.server.should_exit = True
        await asyncio.wait_for(self.server_task, timeout=5)

    async def send(self, client, message_type, **fields):
        await client.send(json.dumps({"type": message_type, **fields}))

    async def receive(self, client, expected_type):
        message = await self.receive_json(client)
        self.assertEqual(message.get("type"), expected_type, message)
        return message

    async def receive_json(self, client):
        raw = await asyncio.wait_for(client.recv(), timeout=3)
        return json.loads(raw)

    async def state(self, client):
        return (await self.receive(client, "state"))["room"]

    async def admit(self, client, action, **fields):
        await self.send(client, action, **fields)
        session = await self.receive(client, "session")
        return session, await self.state(client)

    async def error(self, client, code):
        message = await self.receive(client, "error")
        self.assertEqual(message["code"], code, message)
        self.assertTrue(message["message"])


class LobbyTests(WebSocketTestCase):
    async def test_create_assigns_distinct_human_readable_codes(self):
        async with connect(self.url) as first, connect(self.url) as second:
            first_session, room = await self.admit(first, "create")
            second_session, _ = await self.admit(second, "create")

            self.assertRegex(first_session["code"], r"^[A-HJ-KM-NP-Z2-9]{6}$")
            self.assertNotEqual(first_session["code"], second_session["code"])
            self.assertEqual(room["code"], first_session["code"])
            self.assertEqual(room["phase"], "lobby")
            self.assertEqual(len(room["players"]), 1)
            host = room["players"][0]
            self.assertEqual(host["id"], first_session["player_id"])
            self.assertTrue(host["host"])
            self.assertTrue(host["connected"])

    async def test_join_syncs_both_clients_without_sharing_tokens(self):
        async with connect(self.url) as host, connect(self.url) as guest:
            host_session, _ = await self.admit(host, "create")
            room_code = f" {host_session['code'].lower()} "
            guest_session, guest_room = await self.admit(guest, "join", code=room_code)

            self.assertEqual(await self.state(host), guest_room)
            self.assertNotEqual(host_session["token"], guest_session["token"])
            public_json = json.dumps(guest_room)
            self.assertNotIn(host_session["token"], public_json)
            self.assertNotIn(guest_session["token"], public_json)
            self.assertEqual(len(guest_room["players"]), 2)
            players = {player["id"]: player for player in guest_room["players"]}
            self.assertTrue(players[host_session["player_id"]]["host"])
            self.assertFalse(players[guest_session["player_id"]]["host"])
            self.assertTrue(all(player["connected"] for player in players.values()))
            self.assertEqual({player["label"] for player in players.values()}, {"Player 1", "Player 2"})

    async def test_invalid_unknown_and_full_room_codes_are_rejected(self):
        async with connect(self.url) as host, connect(self.url) as guest:
            host_session, _ = await self.admit(host, "create")
            code = host_session["code"]
            await self.admit(guest, "join", code=code)
            await self.state(host)

            async with connect(self.url) as extra:
                await self.send(extra, "join", code="BAD!")
                await self.error(extra, "invalid_code")
                unknown_code = "AAAAAA" if code != "AAAAAA" else "BBBBBB"
                await self.send(extra, "join", code=unknown_code)
                await self.error(extra, "room_not_found")
                await self.send(extra, "join", code=code)
                await self.error(extra, "room_full")

    async def test_simultaneous_joins_cannot_take_the_same_seat(self):
        async with connect(self.url) as host, connect(self.url) as first, connect(self.url) as second:
            host_session, _ = await self.admit(host, "create")
            code = host_session["code"]
            await asyncio.gather(self.send(first, "join", code=code), self.send(second, "join", code=code))
            replies = await asyncio.gather(self.receive_json(first), self.receive_json(second))

            self.assertCountEqual([reply["type"] for reply in replies], ["session", "error"])
            for client, reply in zip((first, second), replies):
                if reply["type"] == "error":
                    self.assertEqual(reply["code"], "room_full")
                else:
                    self.assertEqual(await self.state(client), await self.state(host))

    async def test_malformed_messages_and_invalid_settings_are_recoverable(self):
        async with connect(self.url) as host:
            await host.send("{")
            await self.error(host, "invalid_message")
            await host.send("null")
            await self.error(host, "invalid_message")
            await host.send(b"binary")
            await self.error(host, "invalid_message")
            await host.send(" " * 4097)
            await self.error(host, "invalid_message")
            settings = {"categories": ["animals"], "duration": True, "mode": "fixed", "rounds": 5}
            await self.send(host, "create", settings=settings)
            await self.error(host, "invalid_settings")

            await self.admit(host, "create")
            await self.send(host, "start", player_id="pretend-host")
            await self.error(host, "invalid_message")
            await self.send(host, "update_settings", settings=settings)
            await self.error(host, "invalid_settings")

    async def test_only_host_can_change_settings_and_start_with_two_connected_players(self):
        settings = {"categories": ["countries"], "duration": 45, "mode": "endless", "rounds": None}
        async with connect(self.url) as host, connect(self.url) as guest:
            host_session, _ = await self.admit(host, "create")
            code = host_session["code"]
            await self.send(host, "start")
            await self.error(host, "players_required")

            await self.admit(guest, "join", code=code)
            await self.state(host)
            await self.send(guest, "start")
            await self.error(guest, "host_only")
            await self.send(guest, "update_settings", settings=settings)
            await self.error(guest, "host_only")

            await self.send(host, "update_settings", settings=settings)
            host_room = await self.state(host)
            self.assertEqual(host_room["settings"], settings)
            self.assertEqual(host_room, await self.state(guest))

            await self.send(host, "start")
            active_room = await self.state(host)
            self.assertEqual(active_room["phase"], "active")
            self.assertEqual(active_room, await self.state(guest))
            async with connect(self.url) as extra:
                await self.send(extra, "join", code=code)
                await self.error(extra, "room_started")

    async def test_duplicate_rejection_disconnect_and_resume_keep_the_same_seat(self):
        async with connect(self.url) as host:
            host_session, _ = await self.admit(host, "create")
            code = host_session["code"]
            async with connect(self.url) as guest:
                guest_session, _ = await self.admit(guest, "join", code=code)
                await self.state(host)
                async with connect(self.url) as extra:
                    await self.send(extra, "resume", code=code, token=guest_session["token"])
                    await self.error(extra, "duplicate_connection")

            disconnected_room = await self.state(host)
            self.assertEqual([player["connected"] for player in disconnected_room["players"]], [True, False])
            await self.send(host, "start")
            await self.error(host, "players_required")

            async with connect(self.url) as reconnected:
                await self.send(reconnected, "resume", code=code, token="x" * len(guest_session["token"]))
                await self.error(reconnected, "invalid_session")
                new_session, room = await self.admit(reconnected, "resume", code=code, token=guest_session["token"])
                self.assertEqual(new_session["player_id"], guest_session["player_id"])
                self.assertEqual(await self.state(host), room)
                self.assertTrue(all(player["connected"] for player in room["players"]))

    async def test_active_room_keeps_disconnected_seat_available_for_resume(self):
        async with connect(self.url) as host:
            host_session, _ = await self.admit(host, "create")
            code = host_session["code"]
            async with connect(self.url) as guest:
                guest_session, _ = await self.admit(guest, "join", code=code)
                await self.state(host)
                await self.send(host, "start")
                await self.state(host)
                await self.state(guest)

            self.assertEqual((await self.state(host))["phase"], "active")
            async with connect(self.url) as reconnected:
                await self.send(reconnected, "resume", code=code, token=guest_session["token"])
                session = await self.receive(reconnected, "session")
                self.assertEqual(session["player_id"], guest_session["player_id"])
                await self.receive(reconnected, "drafts")
                room = await self.state(reconnected)
                self.assertEqual(room["phase"], "active")
                self.assertEqual(await self.state(host), room)
