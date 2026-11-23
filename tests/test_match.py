import asyncio
from unittest.mock import patch

from websockets.asyncio.client import connect

import test_lobby


class MatchWebSocketTests(test_lobby.WebSocketTestCase):
    async def open_room(self, host, guest, *, mode="fixed", rounds=2):
        settings = {"categories": ["animals", "food"], "duration": 15, "mode": mode, "rounds": rounds}
        host_session, _ = await self.admit(host, "create", settings=settings)
        guest_session, guest_room = await self.admit(guest, "join", code=host_session["code"])
        self.assertEqual(await self.state(host), guest_room)
        return host_session, guest_session

    async def shared_state(self, first, second, phase, round_number):
        room = await self.state(first)
        self.assertEqual(room, await self.state(second))
        self.assertEqual(room["phase"], phase)
        self.assertEqual(room["round"]["number"], round_number)
        return room

    async def start(self, host, guest):
        await self.send(host, "start")
        return await self.shared_state(host, guest, "active", 1)

    async def save_draft(self, client, round_number, category, answer):
        await self.send(client, "draft", round_number=round_number, category=category, answer=answer)
        saved = await self.receive(client, "draft_saved")
        self.assertEqual(saved["round_number"], round_number)
        self.assertEqual(saved["category"], category)

    async def assert_no_update(self, *clients):
        # A known error acts as a message-ordering barrier without a timing-based sleep.
        for client in clients:
            await self.send(client, "unsupported")
            await self.error(client, "invalid_message")

    async def test_simultaneous_ready_starts_one_fresh_round_and_fixed_limit_keeps_totals(self):
        with patch("rounds.choose_letter", side_effect=["M", "B"]) as choose_letter:
            async with connect(self.url) as host, connect(self.url) as guest:
                host_session, guest_session = await self.open_room(host, guest)
                host_id, guest_id = host_session["player_id"], guest_session["player_id"]
                first = await self.start(host, guest)
                self.assertEqual(first["round"]["letter"], "M")
                await self.save_draft(host, 1, "animals", "Mouse")
                await self.save_draft(host, 1, "food", "Melon")
                await self.send(host, "done", round_number=1)
                scored = await self.shared_state(host, guest, "results", 1)
                self.assertEqual(scored["ready"], {host_id: False, guest_id: False})
                self.assertEqual(scored["match_scores"], {host_id: 20, guest_id: 0})

                await asyncio.gather(
                    self.send(host, "ready", round_number=1), self.send(guest, "ready", round_number=1)
                )
                waiting = await self.shared_state(host, guest, "results", 1)
                self.assertEqual(sum(waiting["ready"].values()), 1)
                second = await self.shared_state(host, guest, "active", 2)
                self.assertEqual(second["round"]["letter"], "B")
                self.assertEqual(second["round"]["categories"], first["round"]["categories"])
                self.assertEqual(second["round"]["deadline"] - second["round"]["started_at"], 15_000)
                self.assertGreater(second["round"]["remaining_ms"], 0)
                for key in ("ended_by", "submitted_answers", "answer_results", "round_scores"):
                    self.assertIsNone(second["round"][key])
                self.assertNotIn("ready", second)
                self.assertEqual(choose_letter.call_count, 2)

                await self.send(host, "ready", round_number=1)
                await self.send(guest, "end_match", round_number=1)
                await self.assert_no_update(host, guest)
                await self.send(host, "draft", round_number=1, category="animals", answer="Mouse")
                await self.error(host, "stale_round")
                await self.send(guest, "done", round_number=1)
                await self.error(guest, "stale_round")
                await self.save_draft(guest, 2, "animals", "Bear")
                await self.send(guest, "done", round_number=2)
                finished = await self.shared_state(host, guest, "finished", 2)
                self.assertEqual(finished["round"]["submitted_answers"], {
                    host_id: {"animals": "", "food": ""},
                    guest_id: {"animals": "Bear", "food": ""},
                })
                self.assertEqual(finished["round"]["round_scores"], {host_id: 0, guest_id: 10})
                self.assertEqual(finished["match_scores"], {host_id: 20, guest_id: 10})
                self.assertEqual(finished["final_result"], {"winner_id": host_id, "rounds_played": 2})
                self.assertNotIn("ready", finished)
                await self.send(host, "ready", round_number=2)
                await self.send(guest, "end_match", round_number=2)
                await self.assert_no_update(host, guest)
                self.assertEqual(choose_letter.call_count, 2)

    async def test_ready_survives_disconnect_and_resume_can_start_the_next_round(self):
        with patch("rounds.choose_letter", side_effect=["M", "B"]):
            async with connect(self.url) as host:
                async with connect(self.url) as guest:
                    host_session, guest_session = await self.open_room(host, guest, rounds=3)
                    host_id, guest_id = host_session["player_id"], guest_session["player_id"]
                    await self.start(host, guest)
                    await self.save_draft(host, 1, "animals", "Mouse")
                    await self.send(host, "done", round_number=1)
                    await self.shared_state(host, guest, "results", 1)
                    await self.send(guest, "ready", round_number=1)
                    ready = await self.shared_state(host, guest, "results", 1)
                    self.assertEqual(ready["ready"], {host_id: False, guest_id: True})
                    await self.send(guest, "ready", round_number=1)
                    await self.assert_no_update(guest, host)

                offline = await self.state(host)
                self.assertFalse(offline["players"][1]["connected"])
                self.assertEqual(offline["ready"], {host_id: False, guest_id: True})
                await self.send(host, "ready", round_number=1)
                pending = await self.state(host)
                self.assertEqual(pending["phase"], "results")
                self.assertEqual(pending["ready"], {host_id: True, guest_id: True})
                await self.assert_no_update(host)

                async with connect(self.url) as resumed:
                    await self.send(resumed, "resume", code=host_session["code"], token=guest_session["token"])
                    self.assertEqual(await self.receive(resumed, "session"), guest_session)
                    drafts = await self.receive(resumed, "drafts")
                    self.assertEqual(drafts["round_number"], 2)
                    self.assertEqual(drafts["answers"], {"animals": "", "food": ""})
                    second = await self.shared_state(host, resumed, "active", 2)
                    self.assertEqual(second["round"]["letter"], "B")
                    self.assertNotIn("ready", second)
                    await self.save_draft(host, 2, "animals", "Bear")
                    await self.save_draft(resumed, 2, "food", "Banana")

                disconnected = await self.state(host)
                self.assertEqual(disconnected["phase"], "active")
                self.assertEqual(disconnected["round"]["deadline"], second["round"]["deadline"])
                async with connect(self.url) as resumed_again:
                    await self.send(resumed_again, "resume", code=host_session["code"], token=guest_session["token"])
                    self.assertEqual(await self.receive(resumed_again, "session"), guest_session)
                    drafts = await self.receive(resumed_again, "drafts")
                    self.assertEqual(drafts["round_number"], 2)
                    self.assertEqual(drafts["answers"], {"animals": "", "food": "Banana"})
                    await self.shared_state(host, resumed_again, "active", 2)
                    await self.send(host, "done", round_number=2)
                    scored = await self.shared_state(host, resumed_again, "results", 2)
                    self.assertEqual(scored["ready"], {host_id: False, guest_id: False})
                    self.assertEqual(scored["match_scores"], {host_id: 20, guest_id: 10})

    async def test_one_round_fixed_timeout_finishes_as_draw_and_resume_keeps_final_result(self):
        with patch("rounds.monotonic", return_value=100) as clock:
            async with connect(self.url) as host:
                async with connect(self.url) as guest:
                    host_session, guest_session = await self.open_room(host, guest, rounds=1)
                    host_id, guest_id = host_session["player_id"], guest_session["player_id"]
                    await self.start(host, guest)
                    clock.return_value = 115
                    await self.send(guest, "done", round_number=1)
                    final = await self.shared_state(host, guest, "finished", 1)
                    self.assertEqual(final["round"]["ended_by"], "timeout")
                    self.assertEqual(final["match_scores"], {host_id: 0, guest_id: 0})
                    self.assertEqual(final["final_result"], {"winner_id": None, "rounds_played": 1})
                    await self.send(guest, "ready", round_number=2)
                    await self.error(guest, "stale_round")

                await self.state(host)
                async with connect(self.url) as resumed:
                    session, restored = await self.admit(resumed, "resume", code=host_session["code"], token=guest_session["token"])
                    self.assertEqual(session, guest_session)
                    self.assertEqual(restored, await self.state(host))
                    self.assertEqual(restored, final)

    async def test_either_endless_player_can_end_match_between_rounds(self):
        with patch("rounds.choose_letter", return_value="M"):
            for requester in ("host", "guest"):
                with self.subTest(requester=requester):
                    async with connect(self.url) as host, connect(self.url) as guest:
                        host_session, guest_session = await self.open_room(host, guest, mode="endless", rounds=None)
                        host_id, guest_id = host_session["player_id"], guest_session["player_id"]
                        client = host if requester == "host" else guest
                        await self.send(client, "end_match", round_number=1)
                        await self.error(client, "results_required")
                        await self.start(host, guest)
                        await self.send(client, "end_match", round_number=1)
                        await self.error(client, "results_required")
                        await self.save_draft(guest, 1, "animals", "Mouse")
                        await self.send(host, "done", round_number=1)
                        scored = await self.shared_state(host, guest, "results", 1)
                        self.assertNotIn("final_result", scored)
                        await self.send(client, "end_match", round_number=1)
                        final = await self.shared_state(host, guest, "finished", 1)
                        self.assertEqual(final["round"], scored["round"])
                        self.assertEqual(final["match_scores"], {host_id: 0, guest_id: 10})
                        self.assertEqual(final["final_result"], {"winner_id": guest_id, "rounds_played": 1})
                        await self.send(client, "end_match", round_number=1)
                        await self.assert_no_update(client)

    async def test_match_controls_validate_phase_payload_and_round_number(self):
        async with connect(self.url) as host, connect(self.url) as guest:
            await self.open_room(host, guest)
            await self.send(host, "ready", round_number=1)
            await self.error(host, "results_required")
            await self.start(host, guest)
            await self.send(guest, "ready", round_number=1)
            await self.error(guest, "results_required")
            await self.send(guest, "done", round_number=1)
            await self.shared_state(host, guest, "results", 1)
            for command, fields in (
                ("ready", {}),
                ("ready", {"round_number": True}),
                ("ready", {"round_number": 0}),
                ("ready", {"round_number": 1, "player_id": "other"}),
                ("end_match", {"round_number": 1, "winner_id": "other"}),
            ):
                await self.send(guest, command, **fields)
                await self.error(guest, "invalid_message")
            for command in ("ready", "end_match"):
                await self.send(guest, command, round_number=2)
                await self.error(guest, "stale_round")
            await self.send(guest, "end_match", round_number=1)
            await self.error(guest, "endless_only")
            await self.assert_no_update(host, guest)

    async def test_endless_can_continue_and_end_match_wins_before_last_ready(self):
        # Let two draws of the random letter match to verify a repeated letter is allowed.
        with patch("rounds.choose_letter", side_effect=["M", "M"]) as choose_letter:
            async with connect(self.url) as host, connect(self.url) as guest:
                await self.open_room(host, guest, mode="endless", rounds=None)
                await self.start(host, guest)
                await self.send(host, "done", round_number=1)
                await self.shared_state(host, guest, "results", 1)
                await self.send(host, "ready", round_number=1)
                await self.shared_state(host, guest, "results", 1)
                await self.send(guest, "ready", round_number=1)
                active = await self.shared_state(host, guest, "active", 2)
                self.assertEqual(active["round"]["letter"], "M")
                await self.send(guest, "end_match", round_number=1)
                await self.assert_no_update(host, guest)
                await self.send(host, "done", round_number=2)
                await self.shared_state(host, guest, "results", 2)
                await self.send(host, "ready", round_number=2)
                await self.shared_state(host, guest, "results", 2)
                await self.send(guest, "end_match", round_number=2)
                await self.send(guest, "ready", round_number=2)
                final = await self.shared_state(host, guest, "finished", 2)
                self.assertEqual(final["final_result"], {"winner_id": None, "rounds_played": 2})
                await self.assert_no_update(host, guest)
                self.assertEqual(choose_letter.call_count, 2)
