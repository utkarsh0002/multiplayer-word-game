# Word Rally progress

**Milestone 5 is complete.** Two players can play a scored match with multiple rounds in either Fixed or Endless mode.

For the complete game rules, see [GAME_DESIGN.md](GAME_DESIGN.md). For the current implementation and message protocol, see [ARCHITECTURE.md](ARCHITECTURE.md). Local setup and a two-window walkthrough are in [README.md](README.md).

## Completed milestones

| Milestone | Implemented |
| --- | --- |
| 1 · Project foundation | FastAPI and Uvicorn, a responsive HTML/CSS/JavaScript homepage, a browser server-status check, `GET /health`, and local run instructions. |
| 2 · Rooms and lobby | Create and join by unique six-character room code, copy code, two reserved seats, host-only settings and Start, synchronized WebSocket lobby state, and connection/reconnect handling. The server validates categories, timer, mode, and fixed round count. |
| 3 · First playable round | The host starts round 1 with a shared random A–Z letter and configured categories. Each player gets a countdown, one field per category, and Done. The server accepts private drafts and restores them to their owner on active-round resume. Either Done or the server deadline freezes both players' latest drafts once. Both players see the same submitted answers. |
| 4 · Validation and scoring | The server checks frozen answers against the assigned letter and local category lists, classifies answers, awards 10 points for valid unique answers, 5 each for valid duplicates, and 0 for blank or invalid answers. Both browsers see each answer's status and points plus both players' round and cumulative match totals. |
| 5 · Multiple rounds and match end | Both connected players choose Ready to advance after a round. The server starts each new round once, resets round data, and carries match totals forward. Fixed matches finish automatically at the configured round count, and either player can end an Endless match from between-round results. Both browsers show final scores and the winner or draw. Reconnects retain the current match state and the player's seat. |

The suite covers lobby behavior, capacity, permissions, reconnects, cleanup, shared round metadata, draft isolation, early Done, autonomous timeout, validation, and scoring. Match coverage includes Ready synchronization, fresh rounds, cumulative scores, and completion in both modes. Run the full suite from the project root using the command in [README.md](README.md).

## Pending milestones

| Milestone | Remaining work |
| --- | --- |
| 6 · Hardening | Expand regression coverage for the completed match flow, improve disconnect and input handling where needed, verify mobile and desktop layouts and error states, and refresh setup and limitation documentation. |

## Known limitations

- The built-in lists are starter data. A correctly spelled word can still be marked Invalid if its exact normalized spelling is absent from the category list. Some categories have gaps for rare starting letters.
- The room retains the current or most recently scored round. Earlier rounds contribute to cumulative scores and have no separate history view.
- The app uses a single backend process and stores rooms and sessions in memory. Restarting or reloading the backend clears them.
- A disconnect leaves the seat reserved for its token and the round timer keeps running. At between-round results, play waits for both seats to be ready and connected. A room becomes eligible for cleanup after ten minutes with every player disconnected. Browser recovery after a reload depends on the joining tab retaining its session token.
- The project provides local run instructions. Internet hosting is outside the current project setup.
