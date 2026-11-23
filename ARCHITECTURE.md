# Word Rally architecture

This document describes the implementation through Milestone 5. See [GAME_DESIGN.md](GAME_DESIGN.md) for game rules and [PROGRESS.md](PROGRESS.md) for milestone status.

## Runtime and module boundaries

Word Rally runs in one Uvicorn process with a FastAPI backend and a browser frontend built with HTML, CSS, and vanilla JavaScript. Rooms, player sessions, and rounds live in process memory. A process restart or development reload clears them. The single-process design puts room mutations on one event loop.

| File | Responsibility |
| --- | --- |
| [main.py](main.py) | Creates the FastAPI app, one `RoomStore`, and one `Lobby`. Serves the HTML at `GET /`, assets under `/static`, the `GET /health` response `{"status":"ok"}`, and the `/ws` WebSocket endpoint. The FastAPI lifespan starts room cleanup and cancels cleanup and round timers at shutdown. |
| [rooms.py](rooms.py) | Validates settings and manages player identity, connection state, the in-memory room store, and host permissions. Owns round progression, Ready choices, score accumulation, match completion, and the room snapshot. |
| [rounds.py](rounds.py) | Generates the letter, captures the round's timing, stores per-player drafts, checks draft format and limits, and stores frozen answers and scoring results. |
| [scoring.py](scoring.py) | Compares trimmed, case-folded answers against the letter and category list. Returns per-answer statuses and points and per-player round totals. |
| [word_lists.py](word_lists.py) | Holds the editable starter word lists. Its keys supply the room's category options. The server uses normalized sets for exact lookup. |
| [lobby.py](lobby.py) | Validates and routes WebSocket messages, binds sockets to seats, schedules deadline callbacks, and queues room snapshots and private messages for delivery. |
| [static/index.html](static/index.html), [static/styles.css](static/styles.css), [static/app.js](static/app.js) | Define and render the home, lobby, active round, submitted answers, and final results views. The browser sends actions and renders the latest server state. |

## Rooms and player identity

`RoomStore` generates a six-character code from `ABCDEFGHJKMNPQRSTUVWXYZ23456789` and retries on collisions with stored rooms. The creator occupies the first seat as `Player 1` and is the host. The first guest occupies the second seat as `Player 2`. New guests can join only in `lobby` and only while the second seat is unclaimed. The host can edit settings and start while in `lobby`, and starting requires both seats to be connected.

The server generates a UUID-based ID and a private reconnect token for each seat. `create` or `join` allocates a seat. `resume` uses the room code and token to reclaim an existing seat throughout the match, including on the final results screen. The server rejects a second live socket for the same seat. After admission, the handler uses the player bound to the socket for host checks, draft ownership, and Ready choices. Shared snapshots carry player IDs and connection flags. The individual `session` response carries the token, which the browser keeps in tab session storage.

A disconnect updates the seat's connection flag and broadcasts to the remaining connection. The seat stays reserved with its accumulated score and any current Ready choice. An active round's deadline continues to run. A room becomes eligible for removal after every seat has been disconnected for 600 seconds. The store checks for expired rooms during creation and lookup, and the app runs a sweep every 30 seconds.

## Match and round lifecycle

1. The host's initial `start` changes `lobby` to `active` and creates round 1 from the room's selected categories and duration. Subsequent rounds increment the round number, draw a letter independently from `A` through `Z`, and start with blank drafts, a new deadline, and fresh round scores. Both clients receive the same round metadata.
2. `Lobby` schedules a deadline callback with the event loop's `call_later()` method. Timing decisions use a monotonic server clock. The round snapshot also exposes Unix timestamps and the remaining time. The browser uses `remaining_ms` and `performance.now()` for its countdown display.
3. Each accepted `draft` replaces that seat's saved answer for one configured category. It stays private until submission. An active resume restores the caller's own drafts before the first shared room snapshot.
4. `Done` and timeout call the same completion path. The handler checks for expiration before applying a parsed command. A Done received at or after the deadline resolves as a timeout. If the timer callback wakes early, it schedules the remaining delay again.
5. The first completion synchronously copies and trims both players' drafts, scores the frozen answers, and adds each round total to its player's match score inside `Room.finish_round()`. The server records `done` or `timeout`. It moves from `active` to `results`, or directly to `finished` once the configured final round in Fixed mode has been scored. `Lobby` cancels the timer and broadcasts the completed state. The phase guard and synchronous transition prevent resubmission and repeated scoring.
6. During `results`, the server records Ready for the requesting seat and starts the next round only when both seats are ready and connected. A reconnect can satisfy the connection condition without either player pressing Ready again. The same synchronous transition clears Ready and starts the new round, so duplicate or simultaneous messages cannot create extra rounds.
7. In Endless mode, the server accepts `end_match` during `results` from either seat. The browser prompts for confirmation before sending it. If End Match and the last Ready are both submitted, the first applicable action processed determines the next phase. In `finished`, the server reports both match scores and the winner's ID or a draw, and retains the last round's scored breakdown.

## WebSocket protocol

Connect to `/ws` on the same host. If the request has an `Origin` header, it must match the scheme and host derived from the socket request. The application accepts JSON text objects up to 4,096 UTF-8 bytes with a string `type` field. Client messages use the exact fields shown below. Before admission the server allows retries within one 10-second identification window.

### Client messages

| Stage | `type` | Other fields | Effect |
| --- | --- | --- | --- |
| Admission | `create` | Optional `settings` | Creates a room and claims its host seat. Omit `settings` to use defaults. |
| Admission | `join` | `code` | Claims the unoccupied guest seat in a lobby. |
| Admission | `resume` | `code`, `token` | Reclaims a disconnected seat using its private token. The token must have 32 URL-safe characters. |
| Identified | `update_settings` | `settings` | Replaces room settings when requested by the host in `lobby`. |
| Identified | `start` | None | Starts the first round when requested by the host in `lobby` with both players connected. |
| Identified | `draft` | `round_number`, `category`, `answer` | Saves an answer in the caller's slot. The category must be selected for the current round. The answer can be blank and must be a string of at most 80 characters that encodes as UTF-8. |
| Identified | `done` | `round_number` | Ends the active round if the deadline has yet to pass. Either player can send it with any number of blank answers. |
| Identified | `ready` | `round_number` | Marks the requesting seat ready during the identified round's `results`. Starts the next round when both seats are ready and connected. |
| Identified | `end_match` | `round_number` | Finishes an Endless match from the identified round's `results`. Either seat can request it. |

Room codes accept lowercase and surrounding whitespace. Settings carry `categories`, `duration`, `mode`, and `rounds`. Categories are a nonempty array of distinct entries from `animals`, `countries`, `food`, `sports`, and `cities`. Duration is an integer from 15 to 180 seconds. `fixed` uses an integer round count from 1 to 20. `endless` requires `rounds: null`. Defaults are all five categories, 60 seconds, and five fixed rounds.

`round_number` must be a positive integer. During `active`, `draft` and `done` must identify the current round or the server returns `stale_round`. In `results` and `finished`, those packets are ignored after shape and number validation. `ready` and `end_match` apply only to the current round's `results`. The server ignores repeated Ready choices, earlier-round Ready or End Match packets, and Ready or End Match packets for the final round after completion. A future round number returns `stale_round`. An otherwise applicable Ready or End Match request outside `results` returns `results_required`, and a request to end a Fixed match between rounds returns `endless_only`.

### Server messages

| `type` | Other fields | Audience and timing |
| --- | --- | --- |
| `session` | `code`, `player_id`, `token` | Private. First message after a successful admission. |
| `drafts` | `round_number`, `answers` keyed by category | Private. Sent after `session` and before the initial `state` if the room is active after resume. If reconnect triggered a new round, the drafts belong to that round. |
| `state` | `room` | Shared with connected room members after admission, disconnect, a settings change, a Ready change, round start, round scoring, or match completion. Results include scored answers and totals from the server. |
| `draft_saved` | `round_number`, `category` | Private acknowledgment for each accepted `draft`. The browser currently uses the active resume message to restore inputs. |
| `error` | `code`, `message` | Private response to the requesting connection. |

The `room` object contains `code`, `phase`, `settings`, and `players`. The `players` array has one entry per allocated seat with `id`, `label`, `host`, and `connected`. During `lobby` the `round` key is absent. During `results` and `finished`, `room.match_scores` maps player IDs to integer cumulative totals. During `results` only, `room.ready` maps player IDs to booleans. During `finished` only, `room.final_result` contains `winner_id` (a player ID or `null` for a draw) and the integer `rounds_played`. During `active`, `results`, and `finished`, `room.round` contains these fields.

| Field | Value |
| --- | --- |
| `number`, `letter`, `categories` | The round number, an uppercase A–Z letter, and the selected category array. |
| `started_at`, `deadline` | Integer Unix timestamps in milliseconds. |
| `remaining_ms` | Remaining monotonic time in milliseconds rounded up while active, then zero in `results` or `finished`. |
| `ended_by` | `null` while active, then `done` or `timeout`. |
| `submitted_answers` | `null` while active, then an object keyed by player ID whose values map each category to a trimmed string. A blank entry is an empty string. |
| `answer_results` | `null` while active, then an object keyed by player ID whose values map each category to `{status, points}`. Status is `blank`, `invalid`, `valid`, or `duplicate`. Points are integers. |
| `round_scores` | `null` while active, then an object mapping player IDs to integer round totals. |

An invalid packet returns `invalid_message`. Admission can also return `invalid_code`, `room_not_found`, `room_full`, `room_started`, `invalid_session`, or `duplicate_connection`. Settings validation returns `invalid_settings`. Host and phase checks can return `host_only`, `room_started`, or `players_required`. Round and match actions can return `round_inactive`, `stale_round`, `invalid_draft`, `results_required`, or `endless_only`.

After admission, each connection uses a FIFO queue of up to 32 outgoing messages and separate reader and writer tasks. On an identification timeout the server closes the connection with code `1008`. An overloaded send queue closes that connection with code `1013` so its seat can be resumed.

## Verification

The `unittest` suite exercises the app through local Uvicorn servers on ephemeral ports and real WebSocket clients. It also checks room cleanup with a controlled clock. Round and scoring tests cover timing, submissions, validation, and score broadcasts. Match tests cover Ready, progression, cumulative scores, completion, and reconnects. See [test_lobby.py](tests/test_lobby.py), [test_rounds.py](tests/test_rounds.py), [test_scoring.py](tests/test_scoring.py), and [test_match.py](tests/test_match.py).
