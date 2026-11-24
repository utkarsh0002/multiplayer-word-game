import asyncio
import json
import unittest
from hashlib import sha256
from unittest.mock import patch
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from rooms import EMPTY_ROOM_GRACE, Room, RoomError, RoomStore, Settings
import test_lobby


class BackendHardeningTests(test_lobby.WebSocketTestCase):
    async def test_malformed_json_is_rejected_during_admission_and_play(self):
        async with connect(self.url) as host:
            malformed_admission = (
                '{"type":"create","type":"create"}',
                ('{"type":"create","settings":{"categories":["animals"],'
                 '"duration":60,"duration":45,"mode":"fixed","rounds":2}}'),
                '{"type":"create","settings":{"categories":["animals"],"duration":NaN,"mode":"fixed","rounds":2}}',
                '{"type":"create","settings":{"categories":["animals"],"duration":1e999,"mode":"fixed","rounds":2}}',
                '{"type":"join","code":"\\ud800"}',
            )
            for message in malformed_admission:
                await host.send(message)
                await self.error(host, "invalid_message")

            host_session, _ = await self.admit(host, "create")
            async with connect(self.url) as guest:
                await self.admit(guest, "join", code=host_session["code"])
                await self.state(host)
                await self.send(host, "start")
                started = await self.state(host)
                self.assertEqual(started, await self.state(guest))

                await guest.send('{"type":"draft","round_number":1,"category":"animals","answer":"\\ud800"}')
                await self.error(guest, "invalid_message")
                await self.send(guest, "draft", round_number=1, category="animals", answer="Recoverable")
                await self.receive(guest, "draft_saved")
                await self.send(guest, "done", round_number=1)
                results = await self.state(guest)
                self.assertEqual(results, await self.state(host))
                self.assertEqual(results["phase"], "results")

    async def test_origin_check_rejects_wrong_origin_and_keeps_same_origin(self):
        with self.assertRaises(InvalidStatus) as refused:
            async with connect(self.url, origin="https://word-rally-test.invalid"):
                self.fail("A different browser origin was accepted")
        self.assertEqual(refused.exception.response.status_code, 403)

        origin = f"http://{urlsplit(self.url).netloc}"
        async with connect(self.url, origin=origin) as client:
            _, state = await self.admit(client, "create")
            self.assertEqual(state["phase"], "lobby")

    async def test_repeated_admission_errors_close_only_the_pending_socket(self):
        async with connect(self.url) as pending:
            for _ in range(15):
                await self.send(pending, "unsupported")
                await self.error(pending, "invalid_message")
            await self.send(pending, "unsupported")
            await asyncio.wait_for(pending.wait_closed(), timeout=3)
            self.assertEqual(pending.close_code, 1008)

        async with connect(self.url) as healthy:
            _, state = await self.admit(healthy, "create")
            self.assertEqual(state["phase"], "lobby")

    async def test_room_binding_and_resume_keep_drafts_and_credentials_private(self):
        async with connect(self.url) as host, connect(self.url) as other_room:
            host_session, _ = await self.admit(host, "create")
            other_session, _ = await self.admit(other_room, "create")
            code = host_session["code"]
            async with connect(self.url) as guest:
                guest_session, _ = await self.admit(guest, "join", code=code)
                await self.state(host)
                await self.send(host, "start")
                active = await self.state(host)
                self.assertEqual(active, await self.state(guest))
                await self.send(host, "draft", round_number=1, category="animals", answer="Host private draft")
                await self.receive(host, "draft_saved")
                await self.send(guest, "draft", round_number=1, category="animals", answer="Guest private draft")
                await self.receive(guest, "draft_saved")

            offline = await self.state(host)
            self.assertFalse(offline["players"][1]["connected"])
            snapshot = json.dumps(offline)
            for private_text in (host_session["token"], guest_session["token"], "Host private draft", "Guest private draft"):
                self.assertNotIn(private_text, snapshot)

            stored = test_lobby.app.state.rooms.rooms[code]
            self.assertNotIn(host_session["token"], vars(stored.host).values())
            self.assertNotIn(guest_session["token"], vars(stored.players[1]).values())
            self.assertEqual(stored.host.token_hash, sha256(host_session["token"].encode("ascii")).digest())
            self.assertNotIn("token_hash", snapshot)

            async with connect(self.url) as resumed:
                await self.send(resumed, "resume", code=code, token=other_session["token"])
                await self.error(resumed, "invalid_session")
                await self.send(resumed, "resume", code=code, token=guest_session["token"])
                self.assertEqual(await self.receive(resumed, "session"), guest_session)
                drafts = await self.receive(resumed, "drafts")
                self.assertEqual(drafts["answers"]["animals"], "Guest private draft")
                self.assertNotIn("Host private draft", drafts["answers"].values())
                self.assertEqual(await self.state(resumed), await self.state(host))

    async def test_handler_failure_is_sanitized_and_the_same_seat_can_resume(self):
        settings = {"categories": ["animals"], "duration": 45, "mode": "fixed", "rounds": 2}
        async with connect(self.url) as host, connect(self.url) as guest:
            session, initial = await self.admit(host, "create")
            await self.admit(guest, "join", code=session["code"])
            await self.state(host)

            marker = f"unexpected-detail-{session['token']}"
            with self.assertLogs("lobby", level="WARNING") as logs:
                with patch.object(Room, "update_settings", side_effect=RuntimeError(marker)):
                    await self.send(host, "update_settings", settings=settings)
                    await asyncio.wait_for(host.wait_closed(), timeout=3)
                    self.assertEqual(host.close_code, 1011)

            lines = "\n".join(logs.output)
            self.assertIn("RuntimeError", lines)
            self.assertNotIn(marker, lines)
            self.assertNotIn(session["token"], lines)
            offline = await self.state(guest)
            self.assertFalse(offline["players"][0]["connected"])
            self.assertEqual(offline["settings"], initial["settings"])

            async with connect(self.url) as restored:
                restored_session, state = await self.admit(
                    restored, "resume", code=session["code"], token=session["token"]
                )
                self.assertEqual(restored_session, session)
                self.assertTrue(state["players"][0]["connected"])
                self.assertEqual(state, await self.state(guest))
                await self.send(restored, "update_settings", settings=settings)
                updated = await self.state(restored)
                self.assertEqual(updated["settings"], settings)
                self.assertEqual(updated, await self.state(guest))

    async def test_deadline_failure_retries_the_same_round_and_scores_once(self):
        finish = Room.finish_round
        attempted_rounds = []
        marker = "private-test-detail-from-timeout"

        def fail_first_timeout(room, reason):
            attempted_rounds.append(room.round)
            if len(attempted_rounds) == 1:
                raise RuntimeError(marker)
            return finish(room, reason)

        async with connect(self.url) as host, connect(self.url) as guest:
            host_session, _ = await self.admit(host, "create")
            guest_session, _ = await self.admit(guest, "join", code=host_session["code"])
            await self.state(host)
            with (
                self.assertLogs("lobby", level="WARNING") as logs,
                patch("rounds.choose_letter", return_value="A"),
                patch("rounds.monotonic", return_value=100) as clock,
                patch.object(Room, "finish_round", autospec=True, side_effect=fail_first_timeout),
            ):
                await self.send(host, "start")
                await self.state(host)
                await self.state(guest)
                await self.send(host, "draft", round_number=1, category="animals", answer="Ant")
                await self.receive(host, "draft_saved")

                room = test_lobby.app.state.rooms.rooms[host_session["code"]]
                clock.return_value = room.round.deadline_monotonic
                # Deliver the first deadline callback after the draft has been saved.
                # Recovery must schedule its own retry without a client message.
                test_lobby.app.state.lobby.on_deadline(room, room.round)
                scored = await self.state(host)
                self.assertEqual(scored, await self.state(guest))

            self.assertEqual(len(attempted_rounds), 2)
            self.assertIs(attempted_rounds[0], attempted_rounds[1])
            self.assertEqual(scored["phase"], "results")
            self.assertEqual(scored["round"]["ended_by"], "timeout")
            self.assertEqual(scored["match_scores"], {host_session["player_id"]: 10, guest_session["player_id"]: 0})
            lines = "\n".join(logs.output)
            self.assertIn("internal_error operation=round_deadline exception=RuntimeError", lines)
            self.assertNotIn(marker, lines)
            await self.send(host, "unsupported")
            await self.error(host, "invalid_message")

    async def wait_until_disconnected(self, room):
        while room.empty_since is None:
            await asyncio.sleep(0)

    async def test_resume_refreshes_idle_window_but_expired_seat_cannot_return(self):
        with patch("rooms.monotonic", return_value=100) as clock:
            async with connect(self.url) as host:
                session, _ = await self.admit(host, "create")
                room = test_lobby.app.state.rooms.rooms[session["code"]]
            await asyncio.wait_for(self.wait_until_disconnected(room), timeout=3)

            clock.return_value = room.empty_since + EMPTY_ROOM_GRACE - 1
            async with connect(self.url) as resumed:
                restored, _ = await self.admit(resumed, "resume", code=session["code"], token=session["token"])
                self.assertEqual(restored, session)
                self.assertIsNone(room.empty_since)
            await asyncio.wait_for(self.wait_until_disconnected(room), timeout=3)

            clock.return_value = room.empty_since + EMPTY_ROOM_GRACE
            async with connect(self.url) as expired:
                await self.send(expired, "resume", code=session["code"], token=session["token"])
                await self.error(expired, "room_not_found")
            self.assertNotIn(session["code"], test_lobby.app.state.rooms.rooms)

    async def test_simultaneous_done_submits_and_adds_scores_once(self):
        with patch("rounds.choose_letter", return_value="A"):
            async with connect(self.url) as host, connect(self.url) as guest:
                host_session, _ = await self.admit(host, "create")
                guest_session, _ = await self.admit(guest, "join", code=host_session["code"])
                await self.state(host)
                await self.send(host, "start")
                await self.state(host)
                await self.state(guest)
                await self.send(host, "draft", round_number=1, category="animals", answer="Ant")
                await self.receive(host, "draft_saved")

                await asyncio.gather(
                    self.send(host, "done", round_number=1), self.send(guest, "done", round_number=1)
                )
                scored = await self.state(host)
                self.assertEqual(scored, await self.state(guest))
                self.assertEqual(scored["phase"], "results")
                self.assertEqual(scored["round"]["ended_by"], "done")
                self.assertEqual(
                    scored["match_scores"], {host_session["player_id"]: 10, guest_session["player_id"]: 0}
                )
                for client in (host, guest):
                    # A later error on the same connection proves no second result was sent first.
                    await self.send(client, "unsupported")
                    await self.error(client, "invalid_message")

    async def test_results_only_commands_cannot_skip_results_at_the_deadline(self):
        settings = {"categories": ["animals"], "duration": 60, "mode": "endless", "rounds": None}
        for command in ("ready", "end_match"):
            with self.subTest(command=command), patch("rounds.monotonic", return_value=100) as clock:
                async with connect(self.url) as host, connect(self.url) as guest:
                    session, _ = await self.admit(host, "create", settings=settings)
                    await self.admit(guest, "join", code=session["code"])
                    await self.state(host)
                    await self.send(host, "start")
                    await self.state(host)
                    await self.state(guest)

                    clock.return_value = 160
                    await self.send(host, command, round_number=1)
                    results = await self.state(host)
                    self.assertEqual(results, await self.state(guest))
                    self.assertEqual(results["phase"], "results")
                    self.assertEqual(results["round"]["ended_by"], "timeout")
                    self.assertFalse(any(results["ready"].values()))
                    await self.error(host, "results_required")

                    await self.send(host, command, round_number=1)
                    accepted = await self.state(host)
                    self.assertEqual(accepted, await self.state(guest))
                    if command == "ready":
                        self.assertTrue(accepted["ready"][session["player_id"]])
                    else:
                        self.assertEqual(accepted["phase"], "finished")

    async def test_last_ready_and_end_match_select_only_one_transition(self):
        settings = {"categories": ["animals"], "duration": 60, "mode": "endless", "rounds": None}
        async with connect(self.url) as host, connect(self.url) as guest:
            session, _ = await self.admit(host, "create", settings=settings)
            await self.admit(guest, "join", code=session["code"])
            await self.state(host)
            await self.send(host, "start")
            await self.state(host)
            await self.state(guest)
            await self.send(host, "done", round_number=1)
            await self.state(host)
            await self.state(guest)
            await self.send(host, "ready", round_number=1)
            await self.state(host)
            await self.state(guest)

            await asyncio.gather(
                self.send(host, "end_match", round_number=1), self.send(guest, "ready", round_number=1)
            )
            winner_of_race = await self.state(host)
            self.assertEqual(winner_of_race, await self.state(guest))
            if winner_of_race["phase"] == "finished":
                self.assertEqual(winner_of_race["final_result"], {"winner_id": None, "rounds_played": 1})
            else:
                self.assertEqual(winner_of_race["phase"], "active")
                self.assertEqual(winner_of_race["round"]["number"], 2)
                self.assertNotIn("final_result", winner_of_race)

            for client in (host, guest):
                await self.send(client, "unsupported")
                await self.error(client, "invalid_message")


class RoomStoreHardeningTests(unittest.TestCase):
    def test_duplicate_or_foreign_disconnect_does_not_change_idle_since(self):
        store = RoomStore()
        room, host = store.create(Settings())
        _, other_host = store.create(Settings())

        with patch("rooms.monotonic", return_value=100) as clock:
            self.assertFalse(store.disconnect(room, other_host))
            self.assertTrue(other_host.connected)
            self.assertTrue(store.disconnect(room, host))
            self.assertEqual(room.empty_since, 100)

            clock.return_value = 110
            self.assertFalse(store.disconnect(room, host))
            self.assertEqual(room.empty_since, 100)

            clock.return_value = 100 + EMPTY_ROOM_GRACE
            store.prune()
            self.assertNotIn(room.code, store.rooms)

    def test_room_capacity_recovers_when_an_empty_room_expires(self):
        store = RoomStore()
        with patch("rooms.MAX_ROOMS", 1), patch("rooms.monotonic", return_value=100) as clock:
            room, host = store.create(Settings())
            with self.assertRaises(RoomError) as capacity:
                store.create(Settings())
            self.assertEqual(capacity.exception.code, "room_capacity")
            self.assertIs(store.find(room.code), room)

            store.disconnect(room, host)
            clock.return_value = 100 + EMPTY_ROOM_GRACE
            new_room, _ = store.create(Settings())
            self.assertIsNot(new_room, room)
            self.assertEqual(list(store.rooms.values()), [new_room])
