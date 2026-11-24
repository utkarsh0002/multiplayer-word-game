import asyncio
from unittest.mock import patch

from websockets.asyncio.client import connect

import test_lobby


class CommunityReviewTests(test_lobby.WebSocketTestCase):
    def settings(self, *, enabled=True, rounds=2):
        settings = {"categories": ["animals", "food"], "duration": 15, "mode": "fixed", "rounds": rounds}
        if enabled is not None:
            settings["community_word_approval"] = enabled
        return settings

    async def open_room(self, host, guest, *, enabled=True, rounds=2):
        host_session, lobby = await self.admit(host, "create", settings=self.settings(enabled=enabled, rounds=rounds))
        guest_session, guest_lobby = await self.admit(guest, "join", code=host_session["code"])
        self.assertEqual(await self.state(host), guest_lobby)
        self.assertIs(lobby["settings"].get("community_word_approval", False), bool(enabled))
        if not enabled:
            self.assertNotIn("community_word_approval", lobby["settings"])
        return host_session, guest_session

    async def shared_state(self, host, guest, phase, number=1):
        state = await self.state(host)
        self.assertEqual(state, await self.state(guest))
        self.assertEqual(state["phase"], phase)
        self.assertEqual(state["round"]["number"], number)
        return state

    async def start(self, host, guest):
        await self.send(host, "start")
        return await self.shared_state(host, guest, "active")

    async def draft(self, client, category, answer, *, number=1):
        await self.send(client, "draft", round_number=number, category=category, answer=answer)
        saved = await self.receive(client, "draft_saved")
        self.assertEqual((saved["round_number"], saved["category"]), (number, category))

    async def decide(self, client, player_id, category, decision, *, number=1):
        await self.send(
            client, "review_answer", round_number=number, player_id=player_id, category=category, decision=decision
        )

    def entries(self, state):
        return {
            (entry["player_id"], entry["category"]): (entry["answer"], entry["status"])
            for entry in state["round"]["review"]["entries"]
        }

    async def assert_no_update(self, *clients):
        # A known error orders reads behind the preceding command without a timing-based sleep.
        for client in clients:
            await self.send(client, "unsupported")
            await self.error(client, "invalid_message")

    async def test_omitted_and_explicit_false_use_strict_word_lists(self):
        with patch("rounds.choose_letter", return_value="M"):
            for enabled in (None, False):
                with self.subTest(enabled=enabled):
                    async with connect(self.url) as host, connect(self.url) as guest:
                        first, second = await self.open_room(host, guest, enabled=enabled)
                        first_id, second_id = first["player_id"], second["player_id"]
                        await self.start(host, guest)
                        await self.draft(host, "animals", "Moonbadger")
                        await self.draft(guest, "animals", "moonbadger")
                        await self.draft(guest, "food", "Melon")
                        await self.send(host, "done", round_number=1)
                        results = await self.shared_state(host, guest, "results")
                        self.assertNotIn("review", results["round"])
                        for player_id in (first_id, second_id):
                            self.assertEqual(
                                results["round"]["answer_results"][player_id]["animals"],
                                {"status": "invalid", "points": 0},
                            )
                        self.assertEqual(results["match_scores"], {first_id: 0, second_id: 10})

    async def test_setting_requires_boolean_and_only_the_host_can_change_it_in_lobby(self):
        async with connect(self.url) as host, connect(self.url) as guest:
            for value in (1, "true", None, [], {}):
                invalid = self.settings()
                invalid["community_word_approval"] = value
                await self.send(host, "create", settings=invalid)
                await self.error(host, "invalid_settings")

            await self.open_room(host, guest, enabled=False)
            await self.send(guest, "update_settings", settings=self.settings())
            await self.error(guest, "host_only")
            await self.send(host, "update_settings", settings=self.settings())
            enabled = await self.state(host)
            self.assertEqual(enabled, await self.state(guest))
            self.assertIs(enabled["settings"]["community_word_approval"], True)

            await self.send(host, "update_settings", settings=self.settings(enabled=False))
            disabled = await self.state(host)
            self.assertEqual(disabled, await self.state(guest))
            self.assertNotIn("community_word_approval", disabled["settings"])
            await self.start(host, guest)
            await self.send(host, "update_settings", settings=self.settings())
            await self.error(host, "room_started")

    async def test_known_blank_malformed_and_wrong_letter_answers_skip_review(self):
        with patch("rounds.choose_letter", return_value="M"):
            async with connect(self.url) as host, connect(self.url) as guest:
                first, second = await self.open_room(host, guest)
                first_id, second_id = first["player_id"], second["player_id"]
                await self.start(host, guest)
                await self.draft(host, "animals", "  Mouse  ")
                await self.draft(host, "food", "M<food>")
                await self.draft(guest, "animals", "Dragon")
                await self.draft(guest, "food", "   ")
                await self.send(guest, "done", round_number=1)
                results = await self.shared_state(host, guest, "results")
                self.assertNotIn("review", results["round"])
                self.assertEqual(results["round"]["answer_results"], {
                    first_id: {"animals": {"status": "valid", "points": 10}, "food": {"status": "invalid", "points": 0}},
                    second_id: {"animals": {"status": "invalid", "points": 0}, "food": {"status": "blank", "points": 0}},
                })

    async def test_review_rejects_unauthorized_and_stale_commands_and_preserves_first_decision(self):
        with patch("rounds.choose_letter", return_value="M"), patch("review.monotonic", return_value=100):
            async with connect(self.url) as host, connect(self.url) as guest:
                first, second = await self.open_room(host, guest)
                first_id, second_id = first["player_id"], second["player_id"]
                await self.start(host, guest)
                await self.decide(host, second_id, "food", "accept")
                await self.error(host, "review_required")
                await self.draft(host, "animals", "  Moonbadger  ")
                await self.draft(guest, "food", "Melonberry")
                await self.send(host, "done", round_number=1)
                review = await self.shared_state(host, guest, "review")
                self.assertEqual(review["round"]["review"]["remaining_ms"], 15_000)
                self.assertEqual(self.entries(review), {
                    (first_id, "animals"): ("Moonbadger", "pending"),
                    (second_id, "food"): ("Melonberry", "pending"),
                })
                self.assertNotIn("match_scores", review)
                self.assertNotIn("ready", review)
                for key in ("submitted_answers", "answer_results", "round_scores"):
                    self.assertIsNone(review["round"][key])

                await self.decide(host, first_id, "animals", "accept")
                await self.error(host, "review_forbidden")
                await self.decide(host, "0" * 32, "food", "accept")
                await self.error(host, "invalid_review")
                await self.decide(host, second_id, "animals", "accept")
                await self.error(host, "invalid_review")
                await self.decide(host, second_id, "food", ["accept"])
                await self.error(host, "invalid_message")
                await self.decide(host, second_id, "food", "accept", number=2)
                await self.error(host, "stale_round")
                await self.send(host, "ready", round_number=1)
                await self.error(host, "results_required")
                await self.send(guest, "end_match", round_number=1)
                await self.error(guest, "results_required")

                await self.decide(guest, first_id, "animals", "accept")
                updated = await self.shared_state(host, guest, "review")
                self.assertEqual(self.entries(updated)[first_id, "animals"], ("Moonbadger", "accepted"))
                self.assertNotIn("match_scores", updated)
                self.assertEqual(updated["round"]["review"]["deadline"], review["round"]["review"]["deadline"])
                await self.decide(guest, first_id, "animals", "reject")
                await self.assert_no_update(host, guest)

                await self.decide(host, second_id, "food", "reject")
                results = await self.shared_state(host, guest, "results")
                self.assertEqual(self.entries(results), {
                    (first_id, "animals"): ("Moonbadger", "accepted"),
                    (second_id, "food"): ("Melonberry", "rejected"),
                })
                self.assertEqual(results["round"]["review"]["remaining_ms"], 0)
                self.assertEqual(results["round"]["round_scores"], {first_id: 10, second_id: 0})
                self.assertEqual(results["match_scores"], {first_id: 10, second_id: 0})
                await self.decide(host, second_id, "food", "accept")
                await self.assert_no_update(host, guest)

    async def test_simultaneous_matching_approvals_score_duplicate_once(self):
        with patch("rounds.choose_letter", return_value="M"):
            async with connect(self.url) as host, connect(self.url) as guest:
                first, second = await self.open_room(host, guest, rounds=1)
                first_id, second_id = first["player_id"], second["player_id"]
                await self.start(host, guest)
                await self.draft(host, "animals", "  Moonbadger")
                await self.draft(guest, "animals", "MOONBADGER  ")
                await self.send(host, "done", round_number=1)
                await self.shared_state(host, guest, "review")

                await asyncio.gather(
                    self.decide(host, second_id, "animals", "accept"),
                    self.decide(guest, first_id, "animals", "accept"),
                )
                intermediate = await self.shared_state(host, guest, "review")
                self.assertCountEqual(
                    [status for _, status in self.entries(intermediate).values()], ["accepted", "pending"]
                )
                final = await self.shared_state(host, guest, "finished")
                self.assertEqual(final["final_result"], {"winner_id": None, "rounds_played": 1})
                for player_id in (first_id, second_id):
                    self.assertEqual(
                        final["round"]["answer_results"][player_id]["animals"], {"status": "duplicate", "points": 5}
                    )
                self.assertEqual(final["match_scores"], {first_id: 5, second_id: 5})
                await self.decide(host, second_id, "animals", "accept")
                await self.send(guest, "done", round_number=1)
                await self.assert_no_update(host, guest)

    async def test_matching_answers_score_unique_if_only_one_is_accepted(self):
        with patch("rounds.choose_letter", return_value="M"):
            async with connect(self.url) as host, connect(self.url) as guest:
                first, second = await self.open_room(host, guest)
                first_id, second_id = first["player_id"], second["player_id"]
                await self.start(host, guest)
                await self.draft(host, "animals", "Moonbadger")
                await self.draft(guest, "animals", "moonbadger")
                await self.send(host, "done", round_number=1)
                await self.shared_state(host, guest, "review")
                await self.decide(host, second_id, "animals", "accept")
                await self.shared_state(host, guest, "review")
                await self.decide(guest, first_id, "animals", "reject")
                results = await self.shared_state(host, guest, "results")
                self.assertEqual(
                    results["round"]["answer_results"][second_id]["animals"], {"status": "valid", "points": 10}
                )
                self.assertEqual(results["match_scores"], {first_id: 0, second_id: 10})

    async def test_review_deadline_automatically_rejects_unanswered_without_a_client_message(self):
        with patch("rounds.choose_letter", return_value="M"), patch("review.REVIEW_SECONDS", 1):
            async with connect(self.url) as host, connect(self.url) as guest:
                first, second = await self.open_room(host, guest)
                first_id, second_id = first["player_id"], second["player_id"]
                await self.start(host, guest)
                await self.draft(host, "animals", "Moonbadger")
                await self.draft(host, "food", "Melon")
                await self.send(host, "done", round_number=1)
                await self.shared_state(host, guest, "review")
                results = await self.shared_state(host, guest, "results")
                self.assertEqual(self.entries(results), {(first_id, "animals"): ("Moonbadger", "timed_out")})
                self.assertEqual(results["round"]["answer_results"][first_id]["animals"]["points"], 0)
                self.assertEqual(results["match_scores"], {first_id: 10, second_id: 0})

    async def test_reconnect_preserves_decisions_and_deadline_and_late_votes_cannot_win(self):
        with patch("rounds.choose_letter", return_value="M"), patch("review.monotonic", return_value=100) as clock:
            async with connect(self.url) as host:
                async with connect(self.url) as guest:
                    first, second = await self.open_room(host, guest, rounds=1)
                    first_id, second_id = first["player_id"], second["player_id"]
                    await self.start(host, guest)
                    await self.draft(host, "animals", "Moonbadger")
                    await self.draft(guest, "food", "Melonberry")
                    await self.send(host, "done", round_number=1)
                    initial = await self.shared_state(host, guest, "review")
                    await self.decide(host, second_id, "food", "accept")
                    await self.shared_state(host, guest, "review")

                offline = await self.state(host)
                self.assertEqual(offline["phase"], "review")
                self.assertFalse(offline["players"][1]["connected"])
                clock.return_value = 106
                async with connect(self.url) as resumed:
                    identity, restored = await self.admit(resumed, "resume", code=first["code"], token=second["token"])
                    self.assertEqual(identity, second)
                    self.assertEqual(restored, await self.state(host))
                    self.assertEqual(restored["phase"], "review")
                    self.assertEqual(restored["round"]["review"]["deadline"], initial["round"]["review"]["deadline"])
                    self.assertEqual(restored["round"]["review"]["remaining_ms"], 9_000)
                    self.assertEqual(self.entries(restored), {
                        (first_id, "animals"): ("Moonbadger", "pending"),
                        (second_id, "food"): ("Melonberry", "accepted"),
                    })

                    clock.return_value = 115
                    await self.decide(resumed, first_id, "animals", "accept")
                    final = await self.shared_state(host, resumed, "finished")
                    self.assertEqual(self.entries(final), {
                        (first_id, "animals"): ("Moonbadger", "timed_out"),
                        (second_id, "food"): ("Melonberry", "accepted"),
                    })
                    self.assertEqual(final["match_scores"], {first_id: 0, second_id: 10})
                    self.assertEqual(final["final_result"], {"winner_id": second_id, "rounds_played": 1})
                    await self.assert_no_update(host, resumed)

    async def test_play_timeout_can_enter_review_and_decisions_do_not_carry_into_next_round(self):
        with patch("rounds.choose_letter", return_value="M"), patch("rounds.monotonic", return_value=100) as clock:
            async with connect(self.url) as host, connect(self.url) as guest:
                first, second = await self.open_room(host, guest)
                first_id, second_id = first["player_id"], second["player_id"]
                await self.start(host, guest)
                await self.draft(host, "animals", "Moonbadger")
                clock.return_value = 115
                await self.send(guest, "done", round_number=1)
                submitted = await self.shared_state(host, guest, "review")
                self.assertEqual(submitted["round"]["ended_by"], "timeout")
                await self.decide(guest, first_id, "animals", "accept")
                first_results = await self.shared_state(host, guest, "results")
                self.assertEqual(first_results["match_scores"], {first_id: 10, second_id: 0})
                await self.send(host, "ready", round_number=1)
                await self.shared_state(host, guest, "results")
                await self.send(guest, "ready", round_number=1)
                second_round = await self.shared_state(host, guest, "active", 2)
                self.assertNotIn("review", second_round["round"])
                await self.draft(host, "animals", "Moonbadger", number=2)
                await self.send(host, "done", round_number=2)
                second_review = await self.shared_state(host, guest, "review", 2)
                self.assertEqual(self.entries(second_review), {(first_id, "animals"): ("Moonbadger", "pending")})
                await self.decide(guest, first_id, "animals", "accept", number=1)
                await self.error(guest, "stale_round")
                await self.decide(guest, first_id, "animals", "reject", number=2)
                final = await self.shared_state(host, guest, "finished", 2)
                self.assertEqual(final["round"]["round_scores"], {first_id: 0, second_id: 0})
                self.assertEqual(final["match_scores"], {first_id: 10, second_id: 0})
