# Word Rally progress

**Milestone 6A.2 is complete.** Persistent in-room chat is available from the lobby through final results.

For the complete game rules, see [GAME_DESIGN.md](GAME_DESIGN.md). For the current implementation and message protocol, see [ARCHITECTURE.md](ARCHITECTURE.md). Local setup and a two-window walkthrough are in [README.md](README.md).

## Completed milestones

| Milestone | Implemented |
| --- | --- |
| 1 · Project foundation | FastAPI and Uvicorn, a responsive HTML/CSS/JavaScript homepage, a browser server-status check, `GET /health`, and local run instructions. |
| 2 · Rooms and lobby | Create and join by unique six-character room code, copy code, two reserved seats, host-only settings and Start, synchronized WebSocket lobby state, and connection/reconnect handling. The server validates categories, timer, mode, and fixed round count. |
| 3 · First playable round | The host starts round 1 with a shared random A–Z letter and configured categories. Each player gets a countdown, one field per category, and Done. The server accepts private drafts and restores them to their owner on active-round resume. Either Done or the server deadline freezes both players' latest drafts once. Both players see the same submitted answers. |
| 4 · Validation and scoring | The server checks frozen answers against the assigned letter and local category lists, classifies answers, awards 10 points for valid unique answers, 5 each for valid duplicates, and 0 for blank or invalid answers. Both browsers see each answer's status and points plus both players' round and cumulative match totals. |
| 5 · Multiple rounds and match end | Both connected players choose Ready to advance after a round. The server starts each new round once, resets round data, and carries match totals forward. Fixed matches finish automatically at the configured round count, and either player can end an Endless match from between-round results. Both browsers show final scores and the winner or draw. Reconnects retain the current match state and the player's seat. |
| 6A · Backend hardening and reliability | Strict WebSocket JSON and admission limits, digested room credentials, socket-specific cleanup, bounded room capacity, identity-checked timers, and sanitized error handling. Regression tests exercise malformed input, privacy, disconnects, expiry, fault recovery, and competing actions. |
| 6A.1 · Community Word Approval | Unlisted answers that pass eligibility checks enter an optional 15-second review. Players can decide only opponent entries. Pending decisions survive reconnects and time out on the server, and final decisions determine round scoring. The review and results screens display decisions. |
| 6A.2 · Persistent in-room chat | The browser subscribes from the room and restores the server's latest 50 messages on reconnect. The chat panel stays visible across phases and while a seat is disconnected. |

The suite covers lobby behavior, capacity, permissions, reconnects, cleanup, shared round metadata, draft isolation, early Done, autonomous timeout, validation, scoring, and match progression. Community Word Approval has regression coverage for eligibility, first-decision and opponent-only enforcement, timeouts, reconnects, concurrent decisions, and scoring. Run the full suite from the project root using the commands in [README.md](README.md).

## Pending milestones

| Milestone | Remaining work |
| --- | --- |
| 6B · Frontend hardening | Duplicate-session recovery now retains the saved seat. The remaining frontend reliability, accessibility, and layout items are pending. |

## Known limitations

- The built-in lists are starter data. In strict rooms, a correctly spelled unlisted word is invalid. In approval-enabled rooms, an eligible unlisted answer depends on the opposing player's decision before the deadline. Approval applies to that submission only and does not expand a word list.
- The room retains the current or most recently scored round. Earlier rounds contribute to cumulative scores and have no separate history view.
- The app uses a single backend process and stores rooms, sessions, and chat in memory. Restarting or reloading the backend clears them.
- A disconnect leaves the seat reserved for its token and the round timer keeps running. At between-round results, play waits for both seats to be ready and connected. A room becomes eligible for cleanup after ten minutes with every player disconnected. Browser recovery after a reload depends on the joining tab retaining its session token.
- The browser retries the same seat after 1.2, 2.4, and 4.8 seconds. If those attempts fail, **Retry connection** is available and the token is retained. A stale or still-live connection must close before the server can admit the returning tab.
- The local MVP caps rooms and per-socket admission work. It has no per-IP rate limits or public hosting configuration.
