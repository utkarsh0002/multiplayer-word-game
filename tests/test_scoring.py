import unittest
from unittest.mock import patch

from websockets.asyncio.client import connect

import test_lobby
from scoring import score_answers


class ScoreAnswersTests(unittest.TestCase):
    def test_validation_uses_trimmed_case_insensitive_category_words(self):
        cases = [
            ("animals", " \tMoUsE \n", "valid", 10),
            ("animals", "  \t\n", "blank", 0),
            ("animals", "cat", "invalid", 0),
            ("animals", "mouseraptor", "invalid", 0),
            ("food", "mouse", "invalid", 0),
            ("food", " mAnGo ", "valid", 10),
            ("countries", " MEXICO  ", "valid", 10),
            ("cities", " madrid ", "valid", 10),
            ("sports", "Marathon", "valid", 10),
        ]
        for category, answer, status, points in cases:
            with self.subTest(category=category, answer=answer):
                submitted = {"host": {category: answer}, "guest": {category: ""}}
                scored, totals = score_answers("M", (category,), submitted)
                self.assertEqual(scored["host"][category], {"status": status, "points": points})
                self.assertEqual(scored["guest"][category], {"status": "blank", "points": 0})
                self.assertEqual(totals, {"host": points, "guest": 0})
                self.assertEqual(submitted["host"][category], answer)

    def test_only_matching_valid_answers_are_duplicates(self):
        cases = [
            (" Mouse ", "mOuSe", "duplicate", 5),
            ("Mouse", "Moose", "valid", 10),
            ("Cat", "CAT", "invalid", 0),
            ("mouseraptor", "MOUSERAPTOR", "invalid", 0),
            ("", "  ", "blank", 0),
        ]
        for first, second, status, points in cases:
            with self.subTest(first=first, second=second):
                submitted = {"host": {"animals": first}, "guest": {"animals": second}}
                scored, totals = score_answers("M", ("animals",), submitted)
                expected = {"animals": {"status": status, "points": points}}
                self.assertEqual(scored, {"host": expected, "guest": expected})
                self.assertEqual(totals, {"host": points, "guest": points})

    def test_duplicate_checks_are_isolated_to_one_category(self):
        submitted = {
            "host": {"animals": "Turkey", "countries": ""},
            "guest": {"animals": "", "countries": "Turkey"},
        }
        scored, totals = score_answers("T", ("animals", "countries"), submitted)
        self.assertEqual(scored["host"]["animals"], {"status": "valid", "points": 10})
        self.assertEqual(scored["guest"]["countries"], {"status": "valid", "points": 10})
        self.assertEqual(totals, {"host": 10, "guest": 10})


class ScoringWebSocketTests(test_lobby.WebSocketTestCase):
    async def open_round(self, host, guest):
        host_session, lobby = await self.admit(host, "create")
        self.assertNotIn("match_scores", lobby)
        guest_session, _ = await self.admit(guest, "join", code=host_session["code"])
        await self.state(host)
        await self.send(host, "start")
        active = await self.state(host)
        self.assertEqual(active, await self.state(guest))
        self.assertEqual(active["phase"], "active")
        self.assertIsNone(active["round"]["answer_results"])
        self.assertIsNone(active["round"]["round_scores"])
        self.assertNotIn("match_scores", active)
        return host_session, guest_session

    async def save_drafts(self, client, drafts):
        for category, answer in drafts.items():
            await self.send(client, "draft", round_number=1, category=category, answer=answer)
            saved = await self.receive(client, "draft_saved")
            self.assertEqual(saved["category"], category)

    async def shared_result(self, host, guest, reason):
        result = await self.state(host)
        self.assertEqual(result, await self.state(guest))
        self.assertEqual(result["phase"], "results")
        self.assertEqual(result["round"]["ended_by"], reason)
        return result

    async def test_done_broadcasts_scores_and_resume_preserves_single_credit(self):
        with patch("rounds.choose_letter", return_value="M"):
            async with connect(self.url) as host:
                async with connect(self.url) as guest:
                    host_session, guest_session = await self.open_round(host, guest)
                    host_id, guest_id = host_session["player_id"], guest_session["player_id"]
                    await self.save_drafts(host, {"animals": " \tMouse ", "food": "Mango", "countries": "Mexico"})
                    await self.save_drafts(guest, {"animals": "mOuSe", "food": "melon", "countries": "Madrid", "cities": "Madrid"})

                    await self.send(guest, "done", round_number=1, points=100)
                    await self.error(guest, "invalid_message")
                    await self.send(guest, "done", round_number=1)
                    result = await self.shared_result(host, guest, "done")

                    self.assertEqual(result["round"]["submitted_answers"][host_id]["animals"], "Mouse")
                    self.assertEqual(result["round"]["submitted_answers"][guest_id]["animals"], "mOuSe")
                    self.assertEqual(result["round"]["answer_results"], {
                        host_id: {
                            "animals": {"status": "duplicate", "points": 5},
                            "countries": {"status": "valid", "points": 10},
                            "food": {"status": "valid", "points": 10},
                            "sports": {"status": "blank", "points": 0},
                            "cities": {"status": "blank", "points": 0},
                        },
                        guest_id: {
                            "animals": {"status": "duplicate", "points": 5},
                            "countries": {"status": "invalid", "points": 0},
                            "food": {"status": "valid", "points": 10},
                            "sports": {"status": "blank", "points": 0},
                            "cities": {"status": "valid", "points": 10},
                        },
                    })
                    expected_totals = {host_id: 25, guest_id: 25}
                    self.assertEqual(result["round"]["round_scores"], expected_totals)
                    self.assertEqual(result["match_scores"], expected_totals)

                    await self.send(guest, "draft", round_number=1, category="animals", answer="Moose")
                    await self.send(guest, "done", round_number=1)
                    await self.send(guest, "unsupported")
                    await self.error(guest, "invalid_message")
                    await self.send(host, "unsupported")
                    await self.error(host, "invalid_message")

                disconnected = await self.state(host)
                self.assertEqual(disconnected["match_scores"], expected_totals)
                async with connect(self.url) as resumed:
                    _, restored = await self.admit(
                        resumed, "resume", code=guest_session["code"], token=guest_session["token"]
                    )
                    self.assertEqual(restored, await self.state(host))
                    self.assertEqual(restored["round"], result["round"])
                    self.assertEqual(restored["match_scores"], expected_totals)

    async def test_done_at_deadline_submits_and_scores_the_timeout_once(self):
        with (
            patch("rounds.choose_letter", return_value="M"),
            patch("rounds.monotonic", return_value=100) as clock,
        ):
            async with connect(self.url) as host, connect(self.url) as guest:
                host_session, guest_session = await self.open_round(host, guest)
                host_id, guest_id = host_session["player_id"], guest_session["player_id"]
                await self.save_drafts(host, {"animals": "Mole"})
                await self.save_drafts(guest, {"food": "Melon", "sports": "marathon"})
                clock.return_value = 160
                await self.send(host, "done", round_number=1)
                result = await self.shared_result(host, guest, "timeout")

                expected_totals = {host_id: 10, guest_id: 20}
                self.assertEqual(result["round"]["round_scores"], expected_totals)
                self.assertEqual(result["match_scores"], expected_totals)
                await self.send(guest, "done", round_number=1)
                await self.send(guest, "unsupported")
                await self.error(guest, "invalid_message")
                await self.send(host, "unsupported")
                await self.error(host, "invalid_message")
