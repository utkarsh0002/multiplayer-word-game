from unittest.mock import patch

from websockets.asyncio.client import connect

from rounds import Round
from test_lobby import WebSocketTestCase


SETTINGS = {"categories": ["animals", "food"], "duration": 15, "mode": "fixed", "rounds": 1}


class RoundTests(WebSocketTestCase):
    async def open_round(self, host, guest):
        host_session, _ = await self.admit(host, "create", settings=SETTINGS)
        guest_session, _ = await self.admit(guest, "join", code=host_session["code"])
        await self.state(host)
        await self.send(host, "start")
        host_room = await self.state(host)
        self.assertEqual(host_room, await self.state(guest))
        self.assertEqual(host_room["phase"], "active")
        return host_session, guest_session, host_room["round"]

    async def draft(self, client, category, answer):
        await self.send(client, "draft", round_number=1, category=category, answer=answer)
        reply = await self.receive(client, "draft_saved")
        self.assertEqual(reply["round_number"], 1)
        self.assertEqual(reply["category"], category)

    async def results(self, host, guest, reason):
        result = await self.state(host)
        self.assertEqual(result, await self.state(guest))
        self.assertEqual(result["phase"], "finished")
        self.assertEqual(result["final_result"]["rounds_played"], 1)
        self.assertNotIn("ready", result)
        self.assertEqual(result["round"]["ended_by"], reason)
        return result["round"]

    async def test_start_shares_the_letter_categories_and_server_deadline(self):
        with patch("rounds.choose_letter", return_value="M"):
            async with connect(self.url) as host, connect(self.url) as guest:
                _, _, active = await self.open_round(host, guest)

        self.assertEqual(active["number"], 1)
        self.assertEqual(active["letter"], "M")
        self.assertEqual(active["categories"], SETTINGS["categories"])
        self.assertEqual(active["deadline"] - active["started_at"], 15_000)
        self.assertGreater(active["remaining_ms"], 0)
        self.assertLessEqual(active["remaining_ms"], 15_000)
        self.assertIsNone(active["ended_by"])
        self.assertIsNone(active["submitted_answers"])

    async def test_draft_commands_require_active_round_and_are_validated(self):
        async with connect(self.url) as waiting:
            await self.admit(waiting, "create")
            await self.send(waiting, "draft", round_number=1, category="animals", answer="Mouse")
            await self.error(waiting, "round_inactive")

        async with connect(self.url) as host, connect(self.url) as guest:
            await self.open_round(host, guest)
            await self.send(guest, "draft", round_number=2, category="animals", answer="Mouse")
            await self.error(guest, "stale_round")
            await self.send(guest, "draft", round_number=1, category="sports", answer="Tennis")
            await self.error(guest, "invalid_draft")
            await self.send(guest, "draft", round_number=1, category="animals", answer="x" * 81)
            await self.error(guest, "invalid_draft")
            await self.send(guest, "draft", round_number=1, category="animals", answer=["Mouse"])
            await self.error(guest, "invalid_draft")
            await self.send(guest, "draft", round_number=1, category="animals", answer="Mouse", player_id="host")
            await self.error(guest, "invalid_message")

    async def test_active_reconnect_restores_only_the_players_own_drafts(self):
        async with connect(self.url) as host:
            async with connect(self.url) as guest:
                host_session, guest_session, _ = await self.open_round(host, guest)
                await self.draft(host, "animals", "Host secret")
                await self.draft(guest, "food", "Guest secret")
                await self.send(host, "unsupported")
                await self.error(host, "invalid_message")

            disconnected = await self.state(host)
            self.assertFalse(disconnected["players"][1]["connected"])
            self.assertIsNone(disconnected["round"]["submitted_answers"])
            async with connect(self.url) as resumed:
                await self.send(resumed, "resume", code=host_session["code"], token=guest_session["token"])
                session = await self.receive(resumed, "session")
                private = await self.receive(resumed, "drafts")
                self.assertEqual(session["player_id"], guest_session["player_id"])
                self.assertEqual(private["round_number"], 1)
                self.assertEqual(private["answers"].get("food"), "Guest secret")
                self.assertNotIn("Host secret", private["answers"].values())
                guest_room = await self.state(resumed)
                self.assertEqual(guest_room, await self.state(host))
                self.assertIsNone(guest_room["round"]["submitted_answers"])

    async def test_guest_can_end_round_with_every_field_blank(self):
        async with connect(self.url) as host, connect(self.url) as guest:
            host_session, guest_session, _ = await self.open_round(host, guest)
            await self.send(guest, "done", round_number=1)
            result = await self.results(host, guest, "done")

        blank = {"animals": "", "food": ""}
        self.assertEqual(result["submitted_answers"], {
            host_session["player_id"]: blank,
            guest_session["player_id"]: blank,
        })

    async def test_done_freezes_both_latest_drafts_and_ignores_late_actions(self):
        async with connect(self.url) as host:
            async with connect(self.url) as guest:
                host_session, guest_session, active = await self.open_round(host, guest)
                await self.draft(host, "food", "Mango")
                await self.draft(guest, "animals", "Mouse")
                await self.draft(guest, "animals", "  Moose  ")
                await self.send(host, "done", round_number=1)
                result = await self.results(host, guest, "done")
                self.assertEqual(result["letter"], active["letter"])
                self.assertEqual(result["submitted_answers"], {
                    host_session["player_id"]: {"animals": "", "food": "Mango"},
                    guest_session["player_id"]: {"animals": "Moose", "food": ""},
                })

                await self.send(guest, "draft", round_number=1, category="animals", answer="Moth")
                await self.send(guest, "done", round_number=1)
                await self.send(guest, "unsupported")
                await self.error(guest, "invalid_message")
                await self.send(host, "unsupported")
                await self.error(host, "invalid_message")

            self.assertEqual((await self.state(host))["round"], result)
            async with connect(self.url) as resumed:
                _, room = await self.admit(resumed, "resume", code=guest_session["code"], token=guest_session["token"])
                self.assertEqual(room["round"], result)

    async def test_server_timer_submits_both_drafts_without_a_client_message(self):
        begin = Round.begin

        def begin_short_round(categories, duration_seconds, player_ids):
            return begin(categories, 2, player_ids)

        with patch.object(Round, "begin", side_effect=begin_short_round):
            async with connect(self.url) as host, connect(self.url) as guest:
                host_session, guest_session, _ = await self.open_round(host, guest)
                await self.draft(host, "animals", "Mole")
                await self.draft(guest, "food", "Melon")
                result = await self.results(host, guest, "timeout")

        self.assertEqual(result["submitted_answers"], {
            host_session["player_id"]: {"animals": "Mole", "food": ""},
            guest_session["player_id"]: {"animals": "", "food": "Melon"},
        })

    async def test_done_at_the_deadline_resolves_as_timeout_once(self):
        with patch("rounds.monotonic", return_value=100) as clock:
            async with connect(self.url) as host, connect(self.url) as guest:
                await self.open_round(host, guest)
                await self.draft(guest, "animals", "Moose")
                clock.return_value = 115
                await self.send(host, "done", round_number=1)
                await self.results(host, guest, "timeout")
                await self.send(guest, "done", round_number=1)
                await self.send(guest, "unsupported")
                await self.error(guest, "invalid_message")
