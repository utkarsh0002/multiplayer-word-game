# Word Rally game design

Word Rally is a real-time word game for two people playing in desktop or mobile browsers. Both players receive the same starting letter and race to enter one word for each selected category. Either player can end the round early with **Done**. The full game uses category-based scoring and can run for a fixed number of rounds or continue in Endless mode.

The MVP is designed for players on separate devices that can reach the same server. The current setup runs locally. The game uses shareable room codes and private session tokens. Accounts, chat, matchmaking, and a database are outside the MVP scope.

## Implementation status

| Feature | Status after Milestone 6A.1 |
| --- | --- |
| Home, room creation and joining, lobby, and synchronized settings | Implemented |
| Two-player capacity, host controls, connection indicators, and session resume | Implemented |
| Shared letter and categories, private drafts, server countdown, and Done | Implemented for every round |
| Shared results with both players’ submitted answers, statuses, points, round scores, and match scores | Implemented |
| Local category word lists, answer validation, duplicate detection, and scoring | Implemented |
| Ready, additional rounds, mode-specific match endings, and final results | Implemented |
| Backend message, session, cleanup, and concurrency hardening | Implemented in Milestone 6A |
| Optional Community Word Approval | Implemented in Milestone 6A.1 |
| Frontend reliability, mobile and desktop layout, and error-state review | Pending in Milestone 6B |

See [PROGRESS.md](PROGRESS.md) for milestone status and [ARCHITECTURE.md](ARCHITECTURE.md) for the current protocol.

## Rooms, players, and settings

- From Home, one player chooses **Create Room**. The server gives the room a unique six-character code and assigns the creator the host seat, **Player 1**. The lobby displays the code with a copy action. The second player chooses **Join Room**, enters the code, and receives the **Player 2** seat.
- A room has two seats. A third player cannot join, and new joins close when play starts. The server rejects malformed or unknown codes, full rooms, invalid settings, and simultaneous connections claiming the same player session. Code entry accepts lowercase as well as uppercase.
- Both players see the shared room settings and seat connection status. Only the host can change settings in the lobby or choose **Start**. The server accepts Start only when both players are connected.
- A returning browser tab can reclaim its own seat using a private session token. The server preserves the seat, scores, active-round drafts, review decisions, and any Ready choice for the current results. The remaining player sees the connection change, and round and review clocks continue. A disconnect never awards a win. Rooms run in one server process, disappear on restart, and are eligible for cleanup after every seated player has been disconnected for ten minutes.
- A room code lets a guest claim an open lobby seat. Reclaiming an existing seat requires its private token. The server can temporarily refuse new rooms if the in-memory room limit has been reached.

| Setting | Current values |
| --- | --- |
| Categories | One or more distinct choices from Animals, Countries, Food, Sports, and Cities. All five are selected by default. |
| Round duration | 15–180 whole seconds. Default is 60 seconds. |
| Game mode | Fixed rounds or Endless. Default is Fixed rounds. |
| Round count | 1–20 whole rounds for Fixed rounds. Default is 5. Endless has no round count. |
| Community Word Approval | The host can enable peer review of eligible answers absent from the chosen category's word list. Off by default. |

## Round flow

1. The host starts the first round. For every round the server selects a random letter from A–Z, uses the room’s configured categories, and sets a fresh start time and deadline from the configured duration. Both browsers receive the same round number, letter, categories, and deadline. Independent draws can produce the same letter in consecutive rounds.
2. Each player sees the round number, letter, countdown, connection status, and one editable answer field per category. The objective is to enter a word beginning with the announced letter that belongs to the category. Players can leave any field blank or edit it during the active round. Draft updates go to the server for the sending player’s own seat, and only the server’s latest saved draft for each field is used at submission.
3. Either player can click **Done** during the active round with any or all fields blank. The first valid Done the server accepts before the deadline ends the round for both players. The server freezes both players’ saved drafts, including those of the player who did not click Done. Late updates and repeated Done requests cannot alter the submission.
4. If the deadline is reached first, the server submits the same saved drafts automatically, including while either player is disconnected. The browser countdown is informational. The server freezes submissions once, including when Done and timeout occur close together.
5. If Community Word Approval is enabled and any answers are pending, both players enter a review phase before scoring. Otherwise, the server scores the round immediately.
6. Both players receive the same scored results. The results show the round number, starting letter, ending reason, both players’ answers with a status and points per category, any review decisions, and each player's round and cumulative match totals. Submitted answers have leading and trailing whitespace trimmed and retain their original case for display. The final results also show the last scored round.

## Answer validation and scoring

The server validates each submitted answer against the round’s starting letter and the word list for that category. An empty answer after trimming is **Blank**. For other answers, the server compares case-insensitively after trimming. A wrong first letter is **Invalid**. An answer with the correct first letter that appears in the selected category's list is eligible for **Valid** or **Duplicate** status. In the default strict mode, an answer absent from the category list is **Invalid**.

The categories and starter word lists live in [word_lists.py](word_lists.py). Matching uses exact spelling after trimming and case folding, so strict mode requires alternate spellings to be listed explicitly. The results screen and README explain the list limitation. The MVP uses built-in data without an external dictionary API.

### Community Word Approval

- When the host enables approval, an unlisted answer can enter review if it starts with the assigned letter and consists of groups of Unicode letters with optional combining marks. A single space, hyphen, or straight or curly apostrophe can separate groups. Blank answers, wrong starting letters, and entries with malformed text bypass review.
- Both players see the submitted review candidates, their categories, and their current decisions. Only the opponent of the submitting player can choose **Accept** or **Reject**. The first valid decision for an entry is final.
- One 15-second server deadline applies to the whole review. If every candidate has a decision, review ends early. Otherwise, the server rejects all remaining pending entries when time expires. Disconnects leave decisions intact and the clock running.
- Accepted answers are valid for this round. Rejected and timed-out answers are invalid and score zero. Community decisions leave the built-in lists unchanged and do not carry into later rounds or rooms. Ready, End Match, and fixed-match completion follow scoring.

| Outcome in a category | Display status | Points |
| --- | --- | ---: |
| The answer passes validation and the opponent has a different valid answer, an invalid answer, or a blank | Valid | 10 |
| Both answers pass validation and their normalized forms match | Duplicate | 5 each |
| The answer fails validation | Invalid | 0 |
| The answer is empty after trimming | Blank | 0 |

The server compares answers only between the two players in the same category and round, after all review decisions are final. Matching unlisted answers count as duplicates only when both are accepted. If only one is accepted, it earns 10 points. The server sums category points into each player's round score, then adds those points to the player's cumulative match score once. Both browsers display the shared results. Competing timers and repeated commands cannot award points twice.

## Progression and match results

- **Ready.** After a non-final round, both players see each other’s Ready status. The server starts the next round once both seats have chosen **Ready** for the current results and both are connected. If both seats are ready and one is disconnected, play resumes when that seat reconnects. The new round starts with blank drafts, a fresh timer, and cleared Ready choices. Cumulative scores carry over. Repeated Ready actions have no extra effect.
- **Fixed rounds.** The server ends the match automatically when it scores the configured final round. The final results appear without a final Ready step.
- **Endless.** Between rounds, either player can choose **End Match** and confirm it. Canceling leaves both players on the results screen unless play has already advanced. When the last Ready and End Match compete, whichever valid action the server processes first decides whether the match advances or ends.
- **Final results.** Both players see the final scores, the winner or a draw when scores are tied, and the last round's scored answers. Either player can choose **Create New Room** to play again in a new room.
