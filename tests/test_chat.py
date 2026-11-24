import asyncio
from unittest.mock import patch

from websockets.asyncio.client import connect

from rooms import EMPTY_ROOM_GRACE
from test_lobby import WebSocketTestCase, app


class ChatTests(WebSocketTestCase):
    async def open_room(self, host, guest, **fields):
        first, _ = await self.admit(host, "create", **fields)
        second, joined = await self.admit(guest, "join", code=first["code"])
        self.assertEqual(await self.state(host), joined)
        return first, second

    async def subscribe(self, client):
        await self.send(client, "chat_subscribe")
        return (await self.receive(client, "chat_history"))["messages"]

    async def chat_error(self, client, code):
        message = await self.receive(client, "chat_error")
        self.assertEqual(set(message), {"type", "code", "message"})
        self.assertEqual(message["code"], code)
        self.assertTrue(message["message"])

    async def post(self, sender, text, recipient=None):
        await self.send(sender, "chat_send", text=text)
        sent = (await self.receive(sender, "chat_message"))["message"]
        if recipient is not None:
            self.assertEqual(sent, (await self.receive(recipient, "chat_message"))["message"])
        return sent

    async def assert_no_chat(self, client):
        # A known response also detects unexpected packets already queued for this socket.
        await self.send(client, "unsupported")
        await self.error(client, "invalid_message")

    async def shared_state(self, host, guest, phase):
        state = await self.state(host)
        self.assertEqual(state, await self.state(guest))
        self.assertEqual(state["phase"], phase)
        return state

    async def test_chat_requires_admission_two_claimed_seats_and_subscription(self):
        async with connect(self.url) as host, connect(self.url) as guest:
            await self.send(host, "chat_subscribe")
            await self.error(host, "invalid_message")
            session, _ = await self.admit(host, "create")
            await self.send(host, "chat_subscribe")
            await self.chat_error(host, "chat_unavailable")
            await self.send(host, "chat_send", text="Too soon")
            await self.chat_error(host, "chat_unavailable")

            _, joined = await self.admit(guest, "join", code=session["code"])
            self.assertEqual(await self.state(host), joined)
            await self.send(host, "chat_send", text="Subscribe first")
            await self.chat_error(host, "chat_not_subscribed")
            self.assertEqual(await self.subscribe(host), [])

    async def test_both_seats_exchange_messages_and_duplicate_subscribe_is_ignored(self):
        async with connect(self.url) as host, connect(self.url) as guest:
            first, second = await self.open_room(host, guest)
            self.assertEqual(await self.subscribe(host), [])
            self.assertEqual(await self.subscribe(guest), [])

            self.assertEqual(await self.post(host, "  Good round!  ", guest), {
                "seq": 1, "player_id": first["player_id"], "text": "Good round!",
            })
            self.assertEqual(await self.post(guest, "Thanks!", host), {
                "seq": 2, "player_id": second["player_id"], "text": "Thanks!",
            })
            await self.send(host, "chat_subscribe")
            await self.assert_no_chat(host)

    async def test_chat_is_isolated_from_other_rooms_and_unadmitted_sockets(self):
        async with (
            connect(self.url) as host,
            connect(self.url) as guest,
            connect(self.url) as other_host,
            connect(self.url) as other_guest,
            connect(self.url) as unadmitted,
        ):
            first, _ = await self.open_room(host, guest)
            other, _ = await self.open_room(other_host, other_guest)
            await self.subscribe(host)
            await self.subscribe(guest)
            await self.post(host, "First room only", guest)

            self.assertEqual(await self.subscribe(other_host), [])
            self.assertEqual(await self.subscribe(other_guest), [])
            await self.post(host, "Still private", guest)
            await self.assert_no_chat(other_host)
            await self.assert_no_chat(other_guest)
            self.assertEqual((await self.post(other_host, "Other room only", other_guest))["player_id"], other["player_id"])
            await self.assert_no_chat(host)

            await self.send(unadmitted, "join", code=first["code"])
            await self.error(unadmitted, "room_full")
            await self.send(unadmitted, "resume", code=first["code"], token=other["token"])
            await self.error(unadmitted, "invalid_session")
            await self.send(unadmitted, "resume", code=first["code"], token=first["token"])
            await self.error(unadmitted, "duplicate_connection")
            await self.send(unadmitted, "chat_subscribe")
            await self.error(unadmitted, "invalid_message")

    async def test_identity_and_destination_fields_are_rejected(self):
        async with connect(self.url) as host, connect(self.url) as guest:
            first, second = await self.open_room(host, guest)
            await self.subscribe(host)
            for kind, fields in (
                ("chat_subscribe", {"code": first["code"]}),
                ("chat_send", {"text": "Spoofed", "player_id": second["player_id"]}),
                ("chat_send", {"text": "Redirected", "room_id": first["code"]}),
                ("chat_send", {"text": "Credential field", "token": second["token"]}),
            ):
                with self.subTest(kind=kind, fields=tuple(fields)):
                    await self.send(host, kind, **fields)
                    await self.chat_error(host, "invalid_chat")
            self.assertEqual(await self.subscribe(guest), [])
            self.assertEqual(await self.post(host, "Authentic", guest), {
                "seq": 1, "player_id": first["player_id"], "text": "Authentic",
            })

    async def test_chat_text_validation_and_utf8_byte_boundary(self):
        async with connect(self.url) as host, connect(self.url) as guest:
            await self.open_room(host, guest)
            await self.subscribe(host)
            for invalid in ("", " \t ", "a\nb", "a\rb", "a\u2028b", "x" * 501, "é" * 251, None, 5, ["a"]):
                with self.subTest(invalid=repr(invalid)[:40]):
                    await self.send(host, "chat_send", text=invalid)
                    await self.chat_error(host, "invalid_chat")
            await self.send(host, "chat_send")
            await self.chat_error(host, "invalid_chat")
            for malformed in ('{"type":"chat_send","text":', '{"type":"chat_send","text":"first","text":"second"}'):
                await host.send(malformed)
                await self.error(host, "invalid_message")

            self.assertEqual(await self.subscribe(guest), [])
            boundary = "é" * 250
            accepted = await self.post(host, f" {boundary} ", guest)
            self.assertEqual((accepted["seq"], accepted["text"]), (1, boundary))

    async def test_rate_limit_and_error_throttle_persist_across_resume(self):
        with patch("chat.monotonic", return_value=100) as clock:
            async with connect(self.url) as guest:
                async with connect(self.url) as host:
                    first, _ = await self.open_room(host, guest)
                    await self.subscribe(host)
                    await self.subscribe(guest)
                    for number in range(1, 4):
                        self.assertEqual((await self.post(host, str(number), guest))["seq"], number)
                    await self.send(host, "chat_send", text="Excess")
                    await self.chat_error(host, "chat_rate_limited")
                    await self.send(host, "chat_send", text="Repeated excess")
                    await self.assert_no_chat(host)

                self.assertFalse((await self.state(guest))["players"][0]["connected"])
                clock.return_value = 101.999
                async with connect(self.url) as resumed:
                    session, _ = await self.admit(resumed, "resume", code=first["code"], token=first["token"])
                    self.assertEqual(session, first)
                    await self.state(guest)
                    self.assertEqual([entry["seq"] for entry in await self.subscribe(resumed)], [1, 2, 3])
                    await self.send(resumed, "chat_send", text="Still too soon")
                    await self.assert_no_chat(resumed)
                    self.assertEqual((await self.post(guest, "Separate seat budget", resumed))["seq"], 4)

                    clock.return_value = 102
                    self.assertEqual((await self.post(resumed, "One token refilled", guest))["seq"], 5)
                    await self.send(resumed, "chat_send", text="Only one was refilled")
                    await self.chat_error(resumed, "chat_rate_limited")
                    await self.assert_no_chat(guest)

    async def test_history_keeps_latest_fifty_and_preserves_sequence(self):
        with patch("chat.monotonic", return_value=100) as clock:
            async with connect(self.url) as host, connect(self.url) as guest:
                await self.open_room(host, guest)
                await self.subscribe(host)
                for number in range(1, 54):
                    clock.return_value += 2
                    self.assertEqual((await self.post(host, f"Message {number}"))["seq"], number)

                history = await self.subscribe(guest)
                self.assertEqual([entry["seq"] for entry in history], list(range(4, 54)))
                self.assertEqual([entry["text"] for entry in history], [f"Message {i}" for i in range(4, 54)])
                clock.return_value += 2
                self.assertEqual((await self.post(host, "Another message", guest))["seq"], 54)

    async def test_reconnecting_seat_recovers_history_including_offline_messages(self):
        async with connect(self.url) as host:
            async with connect(self.url) as guest:
                _, second = await self.open_room(host, guest)
                await self.subscribe(host)
                await self.subscribe(guest)
                first = await self.post(guest, "See you soon", host)

            self.assertFalse((await self.state(host))["players"][1]["connected"])
            offline = await self.post(host, "Sent while you were away")
            async with connect(self.url) as resumed:
                session, _ = await self.admit(resumed, "resume", code=second["code"], token=second["token"])
                self.assertEqual(session, second)
                await self.state(host)
                self.assertEqual(await self.subscribe(resumed), [first, offline])
                self.assertEqual(await self.post(resumed, "I'm back", host), {
                    "seq": 3, "player_id": second["player_id"], "text": "I'm back",
                })

    async def test_concurrent_subscribe_and_posts_have_one_ordered_delivery_per_message(self):
        async with connect(self.url) as host, connect(self.url) as guest:
            first, second = await self.open_room(host, guest)
            await self.subscribe(host)
            await asyncio.gather(
                self.send(guest, "chat_subscribe"),
                self.send(host, "chat_send", text="During subscription"),
            )
            sent = (await self.receive(host, "chat_message"))["message"]
            received = (await self.receive(guest, "chat_history"))["messages"]
            if not received:
                received.append((await self.receive(guest, "chat_message"))["message"])
            self.assertEqual(received, [sent])
            self.assertEqual(sent["seq"], 1)

            await asyncio.gather(
                self.send(host, "chat_send", text="From the host"),
                self.send(guest, "chat_send", text="From the guest"),
            )
            host_messages = [(await self.receive(host, "chat_message"))["message"] for _ in range(2)]
            guest_messages = [(await self.receive(guest, "chat_message"))["message"] for _ in range(2)]
            self.assertEqual(host_messages, guest_messages)
            self.assertEqual([entry["seq"] for entry in host_messages], [2, 3])
            self.assertCountEqual(
                [(entry["player_id"], entry["text"]) for entry in host_messages],
                [(first["player_id"], "From the host"), (second["player_id"], "From the guest")],
            )
            await self.assert_no_chat(guest)

    async def test_expiration_and_room_code_reuse_clear_history(self):
        with patch("rooms.monotonic", return_value=100) as clock:
            async with connect(self.url) as host, connect(self.url) as guest:
                first, _ = await self.open_room(host, guest)
                await self.subscribe(host)
                await self.post(host, "Old room message")
                room = app.state.rooms.rooms[first["code"]]

            async def wait_until_empty():
                while room.empty_since is None:
                    await asyncio.sleep(0)

            await asyncio.wait_for(wait_until_empty(), timeout=3)
            clock.return_value = room.empty_since + EMPTY_ROOM_GRACE
            async with connect(self.url) as host, connect(self.url) as guest:
                await self.send(host, "resume", code=first["code"], token=first["token"])
                await self.error(host, "room_not_found")
                with patch("rooms.secrets.choice", side_effect=first["code"]):
                    new, _ = await self.open_room(host, guest)
                self.assertEqual(new["code"], first["code"])
                self.assertEqual(await self.subscribe(host), [])
                self.assertEqual(await self.subscribe(guest), [])
                self.assertEqual((await self.post(host, "New room message", guest))["seq"], 1)

    async def test_unsubscribed_legacy_socket_gets_only_game_packets(self):
        async with connect(self.url) as host, connect(self.url) as guest:
            await self.open_room(host, guest)
            await self.subscribe(host)
            await self.post(host, "Lobby chat")
            await self.send(host, "start")
            active = await self.shared_state(host, guest, "active")
            await self.post(host, "Active chat")
            await self.send(guest, "draft", round_number=1, category="animals", answer="Private draft")
            self.assertEqual((await self.receive(guest, "draft_saved"))["round_number"], 1)
            self.assertNotIn("chat", active)
            await self.send(host, "done", round_number=1)
            await self.shared_state(host, guest, "results")

    async def test_chat_keeps_sequence_across_game_phases_without_affecting_gameplay(self):
        settings = {"categories": ["animals"], "duration": 180, "mode": "fixed", "rounds": 2,
                    "community_word_approval": True}
        with patch("rounds.choose_letter", return_value="M"):
            async with connect(self.url) as host, connect(self.url) as guest:
                first, second = await self.open_room(host, guest, settings=settings)
                await self.subscribe(host)
                await self.subscribe(guest)
                self.assertEqual((await self.post(host, "Lobby", guest))["seq"], 1)

                await self.send(host, "start")
                active = await self.shared_state(host, guest, "active")
                deadline = active["round"]["deadline"]
                self.assertEqual((await self.post(guest, "Round one", host))["seq"], 2)
                await self.send(host, "draft", round_number=1, category="animals", answer="Moonbadger")
                await self.receive(host, "draft_saved")
                await self.send(guest, "done", round_number=1)
                review = await self.shared_state(host, guest, "review")
                self.assertEqual(review["round"]["deadline"], deadline)
                review_deadline = review["round"]["review"]["deadline"]
                self.assertEqual((await self.post(host, "Review", guest))["seq"], 3)
                await self.send(guest, "review_answer", round_number=1, player_id=first["player_id"],
                                category="animals", decision="accept")
                results = await self.shared_state(host, guest, "results")
                scores = {first["player_id"]: 10, second["player_id"]: 0}
                self.assertEqual(results["match_scores"], scores)
                self.assertEqual(results["round"]["review"]["deadline"], review_deadline)
                self.assertEqual(results["round"]["submitted_answers"][first["player_id"]]["animals"], "Moonbadger")

                await self.send(host, "ready", round_number=1)
                ready = await self.shared_state(host, guest, "results")
                self.assertEqual(ready["ready"], {first["player_id"]: True, second["player_id"]: False})
                self.assertEqual((await self.post(guest, "Between rounds", host))["seq"], 4)
                await self.send(guest, "ready", round_number=1)
                next_round = await self.shared_state(host, guest, "active")
                self.assertEqual(next_round["round"]["number"], 2)
                await self.send(host, "done", round_number=2)
                finished = await self.shared_state(host, guest, "finished")
                self.assertEqual(finished["match_scores"], scores)
                self.assertEqual((await self.post(host, "Final results", guest))["seq"], 5)
                await self.assert_no_chat(guest)

    async def test_chat_does_not_run_an_overdue_game_deadline(self):
        # Freeze the round clock separately so chat arrives after the logical
        # deadline but before the scheduled timer callback has fired.
        with patch("rounds.monotonic", return_value=100) as clock:
            async with connect(self.url) as host, connect(self.url) as guest:
                first, _ = await self.open_room(host, guest)
                await self.subscribe(host)
                await self.subscribe(guest)
                await self.send(host, "start")
                active = await self.shared_state(host, guest, "active")
                room = app.state.rooms.rooms[first["code"]]
                timer = app.state.lobby.round_timers[id(room)]
                clock.return_value = 160

                await self.post(host, "At the deadline", guest)
                self.assertEqual(room.phase, "active")
                self.assertIs(app.state.lobby.round_timers[id(room)], timer)
                await self.send(guest, "done", round_number=1)
                results = await self.shared_state(host, guest, "results")
                self.assertEqual(results["round"]["ended_by"], "timeout")
                self.assertEqual(results["round"]["deadline"], active["round"]["deadline"])
