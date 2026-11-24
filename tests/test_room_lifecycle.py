import unittest
from unittest.mock import patch

from lobby import Lobby
from rooms import EMPTY_ROOM_GRACE, RoomStore, Settings


class RoomTimerTests(unittest.IsolatedAsyncioTestCase):
    async def test_evicted_rooms_callback_cannot_affect_reused_code(self):
        store = RoomStore()
        lobby = Lobby(store)
        self.addCleanup(lobby.cancel_round_timers)

        with patch("rooms.secrets.choice", return_value="A"):
            old, old_host = store.create(Settings())
            _, old_guest = store.join(old.code)
        old_round = old.start(old_host)
        lobby.schedule_deadline(old, old_round)
        with patch("rooms.monotonic", return_value=0) as clock:
            store.disconnect(old, old_host)
            store.disconnect(old, old_guest)
            clock.return_value = EMPTY_ROOM_GRACE
            self.assertEqual(store.prune(), [old])

        with patch("rooms.secrets.choice", return_value="A"):
            new, new_host = store.create(Settings())
            store.join(new.code)
        self.assertEqual(old.code, new.code)
        new_round = new.start(new_host)
        lobby.schedule_deadline(new, new_round)

        lobby.on_deadline(old, old_round)

        self.assertIs(store.find(new.code), new)
        self.assertEqual(new.phase, "active")
        self.assertIs(new.round, new_round)
        self.assertIsNone(new_round.submitted_answers)
        self.assertEqual(old.phase, "active")
        self.assertNotIn(id(old), lobby.round_timers)
        self.assertFalse(lobby.round_timers[id(new)].cancelled())
