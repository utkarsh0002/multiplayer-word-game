# Word Rally development instructions

## Before coding

1. Read [GAME_DESIGN.md](GAME_DESIGN.md), [ARCHITECTURE.md](ARCHITECTURE.md), and [PROGRESS.md](PROGRESS.md). Use them to identify the expected game behavior, the current implementation, and the milestone in scope.
2. Inspect the relevant source and tests. Verify assumptions against the current implementation, especially room state, message fields, and the phase that accepts each action.
3. Before substantial changes, briefly explain the plan. Implement only the milestone or task the user requested.

## Preserve working behavior

- Keep existing functionality and protocol behavior that the current task does not change. Preserve room codes, player identity and reconnect behavior, host permissions, lobby updates, and server-controlled round completion.
- Extend the existing implementation. Avoid rewriting working features or adding dependencies, services, or abstractions without a concrete need. Keep the code readable for someone learning FastAPI and WebSockets.
- Keep the server authoritative for player identity, phase, time, answers, and results. Validate client messages, keep tokens and active-round drafts private, and preserve exactly-once round completion.
- Bind commands to the admitted socket's current seat. Keep reconnect credentials out of room snapshots and store only their digests in the room store. Log unexpected failures with a fixed operation name and exception class. Keep raw messages, tokens, exception text, and private answers out of application logs.
- Keep Community Word Approval opt-in and preserve strict word-list scoring in rooms that omit or disable it. During review, validate decisions against the current round and server deadline, keep each player's first valid decision for an opponent's answer, and award points once after all decisions are final.

## Tests and documentation

- Keep the regression tests for completed milestones. Add targeted regression coverage when a change could break earlier behavior and add tests for the new behavior. Never remove or weaken tests to get a passing run. Update an existing expectation only when the requested change intentionally changes that behavior.
- For changes to protocol or lifecycle behavior, exercise the ordinary WebSocket flow and relevant malformed input, stale or duplicate actions, disconnects, and concurrent transitions. Use controlled clocks or randomness where it makes the tests deterministic.
- Run the **entire existing test suite** after changes from the project root. Use the project virtual environment and report the actual result, including failures or checks you could not run.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

- Run `node --check static/app.js` after editing the browser code.
- Update the relevant documentation when behavior or architecture changes. Record rules in [GAME_DESIGN.md](GAME_DESIGN.md), current structure and protocol in [ARCHITECTURE.md](ARCHITECTURE.md), milestone status and limitations in [PROGRESS.md](PROGRESS.md), and setup or run changes in [README.md](README.md).
- At milestone handoff, report the files changed, run commands, tests and results, known limitations, and the exact evidence needed for review.
