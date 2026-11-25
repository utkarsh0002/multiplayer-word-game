# Word Rally architecture

This document includes the approved Milestone 6B frontend fixes. See [GAME_DESIGN.md](GAME_DESIGN.md) for game rules and [PROGRESS.md](PROGRESS.md) for milestone status.

## Runtime and module boundaries

Word Rally runs in one Uvicorn process with a FastAPI backend and a browser frontend built with HTML, CSS, and vanilla JavaScript. Rooms, player sessions, rounds, and room chat live in process memory. A process restart or development reload clears them. The single-process design puts room mutations on one event loop.

| File | Responsibility |
| --- | --- |
| [main.py](main.py) | Creates the FastAPI app, one `RoomStore`, and one `Lobby`. Serves the HTML at `GET /`, assets under `/static`, the `GET /health` response `{"status":"ok"}`, and the `/ws` WebSocket endpoint. The FastAPI lifespan starts room cleanup and cancels cleanup and round timers at shutdown. |
| [rooms.py](rooms.py) | Validates settings and manages player identity, connection state, the in-memory room store, and host permissions. Owns round progression, Ready choices, score accumulation, match completion, and the room snapshot. |
| [rounds.py](rounds.py) | Generates the letter, captures the round's timing, stores per-player drafts, checks draft format and limits, and stores frozen answers, optional review state, and scoring results. |
| [review.py](review.py) | Finds eligible unlisted answers and tracks candidate decisions against one shared deadline. |
| [scoring.py](scoring.py) | Compares trimmed, case-folded answers against the letter and category list, accounts for accepted community answers, and calculates statuses and points from final validity. |
| [word_lists.py](word_lists.py) | Holds the editable starter word lists. Its keys supply the room's category options. The server uses normalized sets for exact lookup. |
| [lobby.py](lobby.py) | Validates and routes WebSocket messages, binds sockets to seats, schedules round and review deadline callbacks, and queues room snapshots and private messages for delivery. |
| [chat.py](chat.py) | Keeps each room's latest 50 chat messages, validates text, assigns sequence numbers, and tracks each seat's send rate. |
| [protocol.py](protocol.py) | Parses bounded UTF-8 JSON envelopes and rejects ambiguous or malformed input before command handling. |
| [static/index.html](static/index.html), [static/styles.css](static/styles.css), [static/app.js](static/app.js) | Define and render the home, lobby, active round, review, and results views. The browser sends actions and renders the latest server state. |

## Rooms and player identity

`RoomStore` generates a six-character code from `ABCDEFGHJKMNPQRSTUVWXYZ23456789` and retries on collisions with stored rooms. The creator occupies the first seat as `Player 1` and is the host. The first guest occupies the second seat as `Player 2`. New guests can join only in `lobby` and only while the second seat is unclaimed. The host can edit settings and start while in `lobby`, and starting requires both seats to be connected.

The server gives each seat a UUID-based ID and a private token generated from 24 random bytes. The room store retains only its SHA-256 digest. `create` or `join` allocates a seat. `resume` searches the named room and compares digests in constant time. It can reclaim the same seat throughout the match, including on the final results screen. The server rejects a second live socket for the same seat. Commands use the currently bound player for host checks, draft ownership, and Ready choices. Shared snapshots carry player IDs and connection flags. The private `session` response carries the token, which the browser keeps in tab session storage.

A disconnect updates the seat's connection flag and broadcasts to the remaining connection. Teardown releases only its own socket binding, and repeated or stale disconnects leave the idle clock unchanged. The seat keeps its score and current Ready choice. Round and review deadlines continue, and reconnecting during review restores the shared candidate decisions. A room expires after all seats have been disconnected for 600 seconds. Creation sweeps expired rooms, lookup checks the requested room, and the app sweeps every 30 seconds and cancels timers for rooms it removes. Creation can retain up to 1,024 rooms and refuses further requests until a room expires.

## Frontend connection and lobby actions

The home screen distinguishes Create and Join progress from the HTTP health check. An established room shows whether its WebSocket is connected, reconnecting, or waiting for manual retry. Local disconnection suppresses readiness messages from the last room snapshot. A terminal `room_not_found` or `invalid_session` response to resume clears the saved session and returns the player to room entry with the server error.

Start stays pending until a state leaves the lobby, an error arrives, or the socket closes. Save settings stays pending while the lobby still shows different settings. The browser compares the requested values against the normalized settings in the server snapshot, including the optional Community Word Approval flag. An unrelated connection update cannot complete either action.

## Match and round lifecycle

1. The host's initial `start` changes `lobby` to `active` and creates round 1 from the room's selected categories and duration. Subsequent rounds increment the round number, draw a letter independently from `A` through `Z`, and start with blank drafts, a new deadline, and fresh round scores. Both clients receive the same round metadata.
2. `Lobby` schedules a deadline callback with the event loop's `call_later()` method. Timing decisions use a monotonic server clock. Timer ownership follows the room object, and each callback checks the current phase, round, and store membership before acting. The browser uses server-supplied `remaining_ms` and `performance.now()` for countdown displays. Within the same room, round, and timed phase, a snapshot can keep or shorten the displayed deadline. A new timed phase or round starts a fresh display.
3. Each accepted `draft` replaces that seat's saved answer for one configured category. It stays private until submission. An active resume restores the caller's own drafts before the first shared room snapshot.
4. `Done` and timeout call the same completion path. The handler checks for expiration before applying a parsed game command. A Done received at or after the deadline resolves as a timeout. A Ready or End Match command that encounters the active round and triggers its expiration must be sent again from results. If the timer callback wakes early, it schedules the remaining delay again.
5. The first completion synchronously copies and trims both players' drafts and records `done` or `timeout`. When Community Word Approval is enabled and at least one frozen answer qualifies, the room enters `review`. Otherwise it scores the frozen submissions immediately.
6. Review replaces the play timer with one 15-second deadline shared by both seats. Only the opposing seat can decide each candidate, and the first valid decision is final. When every decision is final, or the timer marks all undecided entries timed out, the server scores exactly once from final validity and updates cumulative totals. The room moves to `results`, or to `finished` after the configured final round in Fixed mode. All transitions run synchronously on the same event loop. After an acknowledged keyboard vote removes the focused control, the browser moves focus to the next available review decision or the review heading. If the socket closes before the vote result arrives, the resumed snapshot for the same room and review round determines which control to focus. Moving focus out of the review controls cancels that restoration.
7. During `results`, the server records Ready for the requesting seat and starts the next round only when both seats are ready and connected. A reconnect can satisfy the connection condition without either player pressing Ready again. The same synchronous transition clears Ready and starts the new round, so duplicate or simultaneous messages cannot create extra rounds.
8. In Endless mode, the server accepts `end_match` during `results` from either seat. The browser prompts for confirmation before sending it. If End Match and the last Ready are both submitted, the first applicable action processed determines the next phase. In `finished`, the server reports both match scores and the winner's ID or a draw, and retains the last round's scored breakdown, including any review decisions.

## WebSocket protocol

Connect to `/ws` on the same host. The server requires a single `Host` header and accepts at most one `Origin` header. If present, Origin must match the request's HTTP(S) scheme and host. The application accepts JSON text objects up to 4,096 UTF-8 bytes with a string `type` field and at most 32 nested containers. Duplicate keys, non-finite numbers, and invalid Unicode are rejected. Client messages use the exact fields shown below. Admission allows up to 16 attempts within one 10-second window covering reads and writes.

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
| Identified | `review_answer` | `round_number`, `player_id`, `category`, `decision` | Decides one opponent candidate in the current `review`. `player_id` is the seat that submitted the word, and `decision` is `accept` or `reject`. |
| Identified | `ready` | `round_number` | Marks the requesting seat ready during the identified round's `results`. Starts the next round when both seats are ready and connected. |
| Identified | `end_match` | `round_number` | Finishes an Endless match from the identified round's `results`. Either seat can request it. |
| Identified | `chat_subscribe` | None | Subscribes the socket after both seats are claimed and returns an ordered history snapshot. Repeat subscriptions on the same socket have no effect. |
| Identified | `chat_send` | `text` | Appends one message for the bound seat after it subscribes. |

Room codes accept lowercase and surrounding whitespace. Settings carry `categories`, `duration`, `mode`, and `rounds`, with an optional boolean `community_word_approval`. Categories are a nonempty array of distinct entries from `animals`, `countries`, `food`, `sports`, and `cities`. Duration is an integer from 15 to 180 seconds. `fixed` uses an integer round count from 1 to 20. `endless` requires `rounds: null`. Defaults are all five categories, 60 seconds, five fixed rounds, and `community_word_approval: false`. The room snapshot includes `community_word_approval` only when enabled and retains the earlier settings shape when disabled.

`round_number` must be a positive integer. During `active`, `draft` and `done` must identify the current round or the server returns `stale_round`. In `review`, `results`, and `finished`, those packets are ignored after shape and number validation. `ready` and `end_match` apply only to the current round's `results`. The server ignores repeated Ready choices, earlier-round Ready or End Match packets, and Ready or End Match packets for the final round after completion. A future round number returns `stale_round`. An otherwise applicable Ready or End Match request outside `results` returns `results_required`, and a request to end a Fixed match between rounds returns `endless_only`.

`review_answer` requires the current round's `review` phase. Invalid decisions or packet shapes return `invalid_message`, a wrong round returns `stale_round`, and requests made before review return `review_required`. An own-seat target returns `review_forbidden`, and a target without that review candidate returns `invalid_review`. Repeated decisions and late requests for a completed review are ignored. The server checks time before applying a decision so undecided entries time out even if the deadline callback is waiting to run.

### Server messages

| `type` | Other fields | Audience and timing |
| --- | --- | --- |
| `session` | `code`, `player_id`, `token` | Private. First message after a successful admission. |
| `drafts` | `round_number`, `answers` keyed by category | Private. Sent after `session` and before the initial `state` if the room is active after resume. If reconnect triggered a new round, the drafts belong to that round. |
| `state` | `room` | Shared with connected room members after admission, disconnect, changes to settings or Ready, round and review transitions, review decisions, scoring, or match completion. Results include scored answers and totals from the server. |
| `draft_saved` | `round_number`, `category` | Private acknowledgment for each accepted `draft`. The browser currently uses the active resume message to restore inputs. |
| `error` | `code`, `message` | Private response to the requesting connection. |
| `chat_history` | `messages` | Sent once to the subscribing socket. The array contains retained messages, oldest first, and may be empty. |
| `chat_message` | `message` | One newly accepted entry, delivered to subscribed sockets in the room, including the sender. |
| `chat_error` | `code`, `message` | Safe chat command error sent to the requesting socket. |

The `room` object contains `code`, `phase`, `settings`, and `players`. The `players` array has one entry per allocated seat with `id`, `label`, `host`, and `connected`. During `lobby` the `round` key is absent. During `results` and `finished`, `room.match_scores` maps player IDs to integer cumulative totals. During `results` only, `room.ready` maps player IDs to booleans. During `finished` only, `room.final_result` contains `winner_id` (a player ID or `null` for a draw) and the integer `rounds_played`. During `active`, `review`, `results`, and `finished`, `room.round` contains these fields.

| Field | Value |
| --- | --- |
| `number`, `letter`, `categories` | The round number, an uppercase A–Z letter, and the selected category array. |
| `started_at`, `deadline` | Integer Unix timestamps in milliseconds. |
| `remaining_ms` | Remaining play time in milliseconds rounded up while active, then zero. |
| `ended_by` | `null` while active, then `done` or `timeout`. |
| `submitted_answers` | `null` during play and review, then an object keyed by player ID whose values map each category to a trimmed string. A blank entry is an empty string. |
| `answer_results` | `null` during play and review, then an object keyed by player ID whose values map each category to `{status, points}`. Status is `blank`, `invalid`, `valid`, or `duplicate`. Points are integers. |
| `round_scores` | `null` during play and review, then an object mapping player IDs to integer round totals. |
| `review` | Present only for rounds with review candidates. Contains `deadline` in Unix milliseconds, `remaining_ms` until review closes (zero in results), and `entries` with `player_id`, `category`, trimmed `answer`, and `status`. Status is `pending`, `accepted`, `rejected`, or `timed_out`. During review only these candidates are exposed. |

A malformed envelope or unsupported command returns `invalid_message`. Admission can also return `invalid_code`, `room_not_found`, `room_full`, `room_started`, `invalid_session`, `duplicate_connection`, or `room_capacity`. Settings validation returns `invalid_settings`. Host and phase checks can return `host_only`, `room_started`, or `players_required`. Round and match actions can return `round_inactive`, `stale_round`, `invalid_draft`, `results_required`, or `endless_only`.

After admission, each connection uses a FIFO queue of up to 32 outgoing messages and separate reader and writer tasks. The reader yields between buffered messages so deadlines and other connections can run. Each send has a five-second timeout. The server uses close code `1008` when admission limits are reached, `1013` when the send queue fills or a send times out, and `1011` for unexpected connection failures. Cleanup observes both tasks and releases only that socket's binding.

Unexpected failures are logged as `internal_error` with a fixed operation name and exception class. Application logs omit raw messages, tokens, answers, exception text, and tracebacks. A deadline failure gets one delayed retry if the same live round remains in the relevant phase. Cleanup failures are contained so later sweeps can run.

## Room chat

Each `Room` owns a `RoomChat` for its entire lifetime. The chat buffer retains the newest 50 messages and drops the oldest when the limit is reached. Connection teardown keeps that buffer and the rate limit for each seat. Normal room expiration removes the chat with the room. A reused code identifies a new room with empty history. Restarting the process also clears all chat.

After receiving its initial `state`, an admitted socket can request `chat_subscribe` once both seats are claimed. The server enqueues the history and registers the socket's subscription synchronously, so any concurrent message appears either in the snapshot or as a live message behind it in the same FIFO. A reconnecting seat subscribes on its new socket. Legacy sockets receive their usual game stream without chat broadcasts. The same rules apply in every room phase, including after match completion and when a claimed seat is offline.

The browser subscribes once on each admitted socket as soon as its room snapshot shows both seats claimed. A history snapshot replaces the visible list. Live entries are ordered and deduplicated by sequence. The panel retains the visible history during a temporary connection loss and disables sending until the new socket receives history. It clears local chat on leaving or confirmed terminal session loss. The browser renders labels and text with DOM text nodes and uses a separate error element for chat. The browser waits up to five seconds for the server to echo an outgoing message. It retains uncertain attempts so a later echo or the first history snapshot after resume can confirm delivery. A confirmation clears the submitted draft if the player has left it as it was. The client matches echoes by seat, text, and sequence, and counts each echo once across reconnects. A rejection can narrow the possible match for a later echo because replies arrive in send order. The client reports a rejection as advisory when several attempts could have caused it. Identical retries can remain uncertain when the available replies cannot distinguish them. Chat confirmation applies to the composer only.

Each entry has `seq`, `player_id`, and `text`. Sequence numbers start at one and increase within a room. The server derives the sender and room from the live socket binding. Chat payloads exclude session tokens and chat is separate from room snapshots, game actions, and deadline handling.

The existing bounded JSON parser runs before chat command validation. The server rejects line breaks, trims surrounding whitespace, and rejects empty text or more than 500 UTF-8 bytes. Chat errors use `invalid_chat` for the wrong fields or invalid text, `chat_unavailable` while the second seat is unclaimed, `chat_not_subscribed` for sends before subscribing, and `chat_rate_limited` when the seat has exhausted its send allowance. Each seat can send up to three messages at once and regains one send every two seconds. Repeated rate limit errors produce at most one reply per seat every two seconds. Admission and parser errors use the existing `error` message. Application logs omit chat content and credentials.

## Verification

The `unittest` suite exercises the app through local Uvicorn servers on ephemeral ports and real WebSocket clients. It checks timing, submission, scoring, Ready, and match completion. The hardening tests cover strict input, room and token isolation, private drafts, exception recovery, close behavior, room expiration and reused codes, and competing state transitions. See [backend integration tests](tests/test_backend_hardening.py), [parser tests](tests/test_protocol.py), and [room lifecycle tests](tests/test_room_lifecycle.py).
