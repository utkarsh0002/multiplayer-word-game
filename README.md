# Word Rally

Word Rally is a two-player browser word game with Fixed and Endless matches. Both players enter answers privately and then see both sets of answers, their statuses, and their round and cumulative match scores. Milestone 5 adds multiple rounds and final results.

## Run locally

Use Python 3.10 or newer. In PowerShell, open the project directory. If you already have a `.venv` from Milestone 1, you can reuse it.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --ws-max-size 4096
```

On macOS or Linux, use `./.venv/bin/python` in place of `.\.venv\Scripts\python.exe`. The development server listens at [http://127.0.0.1:8000/](http://127.0.0.1:8000/). If port 8000 is already in use, add `--port 8765` and open [http://127.0.0.1:8765/](http://127.0.0.1:8765/). Stop it with `Ctrl+C` when finished. `/health` still returns `{"status":"ok"}`.

## Play a match

1. Open the local address in one browser window. Choose **Create Room**, select settings, and copy the room code from the lobby. Choose **Fixed rounds** with two rounds to exercise both progression and automatic completion.
2. Open the same address directly in a private window or a different browser. Choose **Join Room** and enter the code. Both lobbies should show the same code and settings, with both players connected. The guest should see a waiting message where the host has **Start**.
3. Before clicking **Start**, open a third independent tab from the address bar. Try to join with the same code. It should report that the room already has two players.
4. Update settings in the host lobby and check that the guest sees the new values. Reload the guest page to verify that the same player slot reconnects.
5. Click **Start**. Both windows should show **Round 1**, the same starting letter, and one answer field per selected category.
6. Enter an answer from [the category list](word_lists.py) that starts with the round letter. Leave the other player's field blank and press **Done**. Both results should show **Valid** and 10 points for the answer, and **Blank** and 0 points for the empty field. If both players submit the same valid answer, both should see **Duplicate** and 5 points each.
7. Choose **Ready** in one window. Both windows stay on results until the other player chooses **Ready**. Round 2 should start with blank answer fields and a fresh timer. Reloading a window reclaims its seat and current match state if the tab still has its session token.
8. Play the last round. A Fixed match should advance to final results when the final round ends, showing both cumulative scores, the winner or draw, and the last round's scored answers. Choose **Create New Room** to play again.
9. To try Endless mode, create and join a new Endless room. From between-round results, either player can choose **End Match**. Cancel to stay on results, or confirm to show the final scores on both screens.
10. For the timer path, create a room with a 15-second timer. Start, enter an answer, and let the timer expire. The scored round details should show **Time ran out** and the same submitted answers in both windows.

## Run the backend tests

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

The tests include local Uvicorn servers and real WebSocket connections. They cover lobby behavior, private drafts, Done and timeout, answer validation and scoring, Ready, multiple rounds, match completion, and reconnects.

## How the server runs a match

- `main.py` serves the home page, health check, and `/ws` endpoint. The `websockets` dependency lets Uvicorn accept WebSocket connections.
- `rooms.py` owns in-memory room state and phase transitions. From `results`, both players must be ready and connected to start the next round. A Fixed match reaches `finished` as soon as the last round is scored. An Endless match reaches `finished` when either player ends it from `results`. Transitions run without an `await` on one event loop, so competing requests cannot submit a round or start its successor twice.
- `rounds.py` tracks the round and freezes a trimmed, case-preserving copy of the drafts on submission. `scoring.py` case-folds those answers for comparison, then checks the round letter and the lists in `word_lists.py`. The room adds each player's round score to their match total once.
- `lobby.py` associates each WebSocket with its server-assigned player and schedules one timer per active room. It checks the deadline before it accepts an action, even if the timer callback is still waiting to run. Resume restores only the returning player's drafts. The deadline keeps running through disconnects.
- `static/app.js` sends drafts as the player types. Its countdown displays the server's remaining time. The browser renders Ready choices, scores, and the final outcome from the shared server state.

Rooms and sessions are lost when the backend process restarts, including during a code reload. Run a single backend process. A disconnected player's seat stays reserved with its score and current Ready choice. The active timer continues, and between-round progression waits for both seats to reconnect. A room is eligible for cleanup after every player has been disconnected for ten minutes.

The built-in word lists are starter data. A plausible answer is marked Invalid when its exact spelling after trimming and case folding is absent from the selected category list.

## WebSocket protocol

Messages are JSON objects with a `type`. Clients identify themselves before sending lobby, round, or match actions.

| Direction | Type | Fields and behavior |
| --- | --- | --- |
| Client → server | `create` | Optional `settings`. Uses defaults if omitted. |
| Client → server | `join` | `code` from the host lobby. |
| Client → server | `resume` | `code` and the tab's private `token`. |
| Client → server | `update_settings` | `settings`. Host only, while in the lobby. |
| Client → server | `start` | Host only, when both players are connected. Starts round 1 and changes `phase` to `active`. |
| Client → server | `draft` | `round_number`, `category`, `answer`. Saves text up to 80 characters for this connection's player. |
| Client → server | `done` | `round_number`. Either player can end the active round, including with blank answers. |
| Client → server | `ready` | `round_number`. Marks the caller ready on the current between-round results. |
| Client → server | `end_match` | `round_number`. Either player can finish an Endless match from the current between-round results. |
| Server → client | `session` | `code`, `player_id`, and `token`. Sent to the identifying connection. |
| Server → client | `state` | `room` with `code`, `phase`, `settings`, and `players`. Each player has `id`, `label`, `host`, and `connected`. A room that has started also has `round`. |
| Server → client | `drafts` | `round_number` and the returning player's `answers`. Private, sent after `session` and before the first active `state` on resume. |
| Server → client | `draft_saved` | `round_number` and `category`. Sent only to the player whose draft was accepted. |
| Server → client | `error` | `code` and a readable `message`. Sent to the requesting client. |

Settings contain `categories` (one or more of `animals`, `countries`, `food`, `sports`, `cities`), `duration` (15–180 seconds), `mode` (`fixed` or `endless`), and `rounds` (1–20 for fixed, `null` for endless). Room codes use six uppercase letters or digits selected from an alphabet that omits easily confused characters. The server also accepts lowercase codes.

In `results` or `finished`, `room.round` includes the submitted answers, their status and points by category, and each player's round total. `room.match_scores` gives the cumulative totals. `room.ready` appears in `results`, and `room.final_result` appears in `finished`. See [ARCHITECTURE.md](ARCHITECTURE.md#websocket-protocol) for the full message contract.
