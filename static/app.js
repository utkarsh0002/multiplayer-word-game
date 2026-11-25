const home = document.querySelector("#home");
const homeActions = document.querySelector("#home-actions");
const createForm = document.querySelector("#create-form");
const joinForm = document.querySelector("#join-form");
const joinCode = document.querySelector("#join-code");
const settingsForm = document.querySelector("#settings-form");
const settingsTemplate = document.querySelector("#settings-template");
const statusMessage = document.querySelector("#connection-status");
const retryButton = document.querySelector("#retry");
const homeError = document.querySelector("#home-error");
const homeSeatStatus = document.querySelector("#home-seat-status");
const retrySeatHome = document.querySelector("#retry-seat-home");
const leaveSavedRoom = document.querySelector("#leave-saved-room");
const lobby = document.querySelector("#lobby");
const lobbyTitle = document.querySelector("#lobby-title");
const lobbyConnection = document.querySelector("#lobby-connection");
const lobbyError = document.querySelector("#lobby-error");
const chatPanel = document.querySelector("#room-chat");
const chatHistory = document.querySelector("#chat-history");
const chatEmpty = document.querySelector("#chat-empty");
const chatStatus = document.querySelector("#chat-status");
const chatError = document.querySelector("#chat-error");
const chatDelivery = document.querySelector("#chat-delivery");
const chatForm = document.querySelector("#chat-form");
const chatInput = document.querySelector("#chat-input");
const chatSend = document.querySelector("#chat-send");
const roomCode = document.querySelector("#room-code");
const copyStatus = document.querySelector("#copy-status");
const playerList = document.querySelector("#players");
const roomStatus = document.querySelector("#room-status");
const startButton = document.querySelector("#start-game");
const readyButton = document.querySelector("#ready-next-round");
const endMatchButton = document.querySelector("#end-match");
const endMatchDialog = document.querySelector("#confirm-end-match");
const returnHomeButton = document.querySelector("#return-home");
const retrySeatRoom = document.querySelector("#retry-seat-room");
const hostSettings = document.querySelector("#host-settings");
const lobbySettings = document.querySelector("#lobby-settings");
const roundPanel = document.querySelector("#round-panel");
const roundForm = document.querySelector("#round-form");
const roundInputs = document.querySelector("#round-inputs");
const doneButton = document.querySelector("#done-round");
const reviewPanel = document.querySelector("#review-panel");
const reviewOpponent = document.querySelector("#review-opponent");
const reviewOpponentTitle = document.querySelector("#review-opponent-title");
const reviewOwn = document.querySelector("#review-own");
const reviewProgress = document.querySelector("#review-progress");
const roundResults = document.querySelector("#round-results");
const finalResults = document.querySelector("#final-results");
const finalRoundTitle = document.querySelector("#final-round-title");
const roundClock = document.querySelector("#round-clock");
const roundTime = document.querySelector("#round-time");
const roundTimeLabel = document.querySelector("#round-time-label");
const answerStatusLabels = {
  blank: "Blank",
  invalid: "Invalid",
  valid: "Valid",
  duplicate: "Duplicate",
};
const reviewStatusLabels = {
  pending: "Awaiting review",
  accepted: "Accepted",
  rejected: "Rejected",
  timed_out: "Rejected after timeout",
};
const categoryLabels = {};
for (const field of settingsTemplate.content.querySelectorAll("input[name=category]")) {
  categoryLabels[field.value] = field.parentElement.textContent.trim();
}

const sessionKey = "word-rally-room";
const reconnectDelays = [1200, 2400, 4800];
const chatByteLimit = 500;
const chatHistoryLimit = 50;
const chatConfirmationDelay = 5000;
const chatRateLimitDelay = 2000;
const chatEncoder = new TextEncoder();
let session = savedSession();
let socket = null;
let room = null;
let admissionAction = null;
let connected = false;
let pendingAction = null;
let reviewVoteFocus = null;
let reconnectTimer = null;
let reconnectAttempt = 0;
let closingIntentionally = false;
let suspended = false;
let confirmedSettings = null;
let restoredDrafts = null;
let countdownInterval = null;
let countdownDeadline = 0;
let chatMessages = [];
let chatReady = false;
let pendingChat = null;
const chatAttempts = [];
let lastChatConfirmationSequence = 0;
let chatDraftVersion = 0;
let chatRetryTimer = null;

async function checkServer() {
  retryButton.hidden = true;
  statusMessage.dataset.state = "checking";
  statusMessage.textContent = "Checking server…";

  try {
    const response = await fetch("/health", {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });

    if (!response.ok || (await response.json()).status !== "ok") {
      throw new Error("Health check failed");
    }

    statusMessage.dataset.state = "online";
    statusMessage.textContent = "Last server check passed";
  } catch {
    statusMessage.dataset.state = "unavailable";
    statusMessage.textContent = "Last server check failed";
    retryButton.hidden = false;
  }
}

function savedSession() {
  try {
    return JSON.parse(sessionStorage.getItem(sessionKey));
  } catch {
    return null;
  }
}

function storeSession(value) {
  session = value;
  try {
    if (value) {
      sessionStorage.setItem(sessionKey, JSON.stringify(value));
    } else {
      sessionStorage.removeItem(sessionKey);
    }
  } catch {
    // The current tab can still play when browser storage is unavailable.
  }
}

function setError(element, message = "") {
  element.textContent = message;
  element.hidden = !message;
}

function showEntry(form) {
  homeActions.hidden = Boolean(form);
  createForm.hidden = form !== createForm;
  joinForm.hidden = form !== joinForm;
  setError(homeError);
}

function updateRoundCount(form) {
  const endless = form.elements.mode.value === "endless";
  form.querySelector(".round-count").hidden = endless;
  form.elements.rounds.disabled = endless;
}

function readSettings(form, errorElement) {
  const data = new FormData(form);
  const categories = data.getAll("category");
  if (!categories.length) {
    setError(errorElement, "Select at least one category.");
    form.querySelector("input[name=category]").focus();
    return null;
  }
  return {
    categories,
    duration: Number(data.get("duration")),
    mode: data.get("mode"),
    rounds: data.has("rounds") ? Number(data.get("rounds")) : null,
    community_word_approval: data.has("community_word_approval"),
  };
}

function showSettings(settings) {
  document.querySelector("#current-categories").textContent = settings.categories.map((key) => categoryLabels[key]).join(", ");
  document.querySelector("#current-duration").textContent = `${settings.duration} seconds`;
  document.querySelector("#current-mode").textContent = settings.mode === "fixed" ? `${settings.rounds} fixed rounds` : "Endless";
  document.querySelector("#current-community-word-approval").textContent = settings.community_word_approval ? "On" : "Off";

  // Connection updates should leave an unfinished host edit in place.
  const currentSettings = JSON.stringify(settings);
  if (confirmedSettings === currentSettings) return;
  confirmedSettings = currentSettings;
  for (const field of settingsForm.querySelectorAll("input[name=category]")) {
    field.checked = settings.categories.includes(field.value);
  }
  settingsForm.elements.duration.value = settings.duration;
  settingsForm.elements.mode.value = settings.mode;
  settingsForm.elements.rounds.value = settings.rounds ?? 5;
  settingsForm.elements.community_word_approval.checked = settings.community_word_approval === true;
  updateRoundCount(settingsForm);
}

function showPlayers() {
  const slots = room.players.length < 2 ? [...room.players, { label: "Player 2" }] : room.players;
  const rows = slots.map((player) => {
    const row = document.createElement("li");
    const label = document.createElement("strong");
    const flags = document.createElement("div");
    const status = document.createElement("span");
    const role = player.host ? " · Host" : "";
    const self = player.id && player.id === session?.player_id;
    const playerConnected = player.connected && (!self || connected);
    label.textContent = `${player.label}${role}${self ? " (you)" : ""}`;
    flags.className = "player-flags";
    status.className = "player-status";
    status.dataset.connected = Boolean(playerConnected);
    status.textContent = playerConnected ? "Connected" : player.id ? "Disconnected" : "Waiting to join";
    flags.append(status);
    if (room.phase === "results") {
      const ready = document.createElement("span");
      ready.className = "player-ready";
      ready.dataset.ready = room.ready[player.id];
      ready.textContent = room.ready[player.id] ? "Ready" : "Not ready";
      flags.append(ready);
    }
    row.append(label, flags);
    return row;
  });
  playerList.replaceChildren(...rows);
}

function updateCountdown() {
  const seconds = Math.max(0, Math.ceil((countdownDeadline - performance.now()) / 1000));
  const remainder = String(seconds % 60).padStart(2, "0");
  const display = `${Math.floor(seconds / 60)}:${remainder}`;
  if (roundTime.textContent !== display) roundTime.textContent = display;
}

function stopCountdown() {
  clearInterval(countdownInterval);
  countdownInterval = null;
}

function buildAnswerFields(categories) {
  const fields = categories.map((category) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    label.textContent = categoryLabels[category];
    input.name = category;
    input.maxLength = 80;
    input.autocomplete = "off";
    label.append(input);
    return label;
  });
  roundInputs.replaceChildren(...fields);
}

function reviewStatus(status) {
  const label = document.createElement("span");
  label.className = "review-status";
  label.dataset.status = status;
  label.textContent = reviewStatusLabels[status];
  return label;
}

function createReviewEntry(entry, roundNumber, reviewable) {
  const row = document.createElement("li");
  row.className = "review-entry";
  row.dataset.category = entry.category;
  row.tabIndex = -1;
  const categoryName = categoryLabels[entry.category];
  const category = document.createElement("span");
  category.className = "review-category";
  category.textContent = categoryName;
  const answer = document.createElement("strong");
  answer.textContent = entry.answer;
  row.append(category, answer, reviewStatus(entry.status));
  if (reviewable && entry.status === "pending") {
    const actions = document.createElement("div");
    actions.className = "actions review-actions";
    for (const decision of ["accept", "reject"]) {
      const button = document.createElement("button");
      const verb = decision === "accept" ? "Accept" : "Reject";
      button.type = "button";
      button.dataset.decision = decision;
      button.textContent = verb;
      button.setAttribute("aria-label", `${verb} ${categoryName} answer`);
      if (decision === "accept") button.className = "primary";
      button.addEventListener("click", () => sendAction({
        type: "review_answer",
        round_number: roundNumber,
        player_id: entry.player_id,
        category: entry.category,
        decision,
      }));
      actions.append(button);
    }
    row.append(actions);
  }
  return row;
}

function showReview(round) {
  const entries = round.review.entries;
  const own = entries.filter((entry) => entry.player_id === session?.player_id);
  const opponent = entries.filter((entry) => entry.player_id !== session?.player_id);
  const focused = document.activeElement;
  const focusedCategory = reviewOpponent.contains(focused) ? focused.closest(".review-entry")?.dataset.category : null;
  const rows = opponent.map((entry) => createReviewEntry(entry, round.number, true));
  if (!rows.length) {
    const empty = document.createElement("li");
    empty.textContent = "No answers to review.";
    rows.push(empty);
  }
  reviewOpponent.replaceChildren(...rows);
  reviewOwn.replaceChildren(...own.map((entry) => createReviewEntry(entry, round.number, false)));
  document.querySelector("#review-own-panel").hidden = own.length === 0;

  const pending = entries.filter((entry) => entry.status === "pending").length;
  reviewProgress.textContent = `${pending} ${pending === 1 ? "answer" : "answers"} awaiting review.`;
  if (focusedCategory) {
    const row = rows.find((item) => item.dataset.category === focusedCategory);
    const button = row?.querySelector(`[data-decision="${focused.dataset.decision}"]`);
    (button ?? row)?.focus();
  }
}

function showResults(round) {
  const headers = ["Category", ...room.players.map((player) => player.label)];
  document.querySelector("#results-players").replaceChildren(...headers.map((header) => {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = header;
    return cell;
  }));

  const rows = round.categories.map((category) => {
    const row = document.createElement("tr");
    const label = document.createElement("th");
    label.scope = "row";
    label.textContent = categoryLabels[category];
    row.append(label);
    for (const player of room.players) {
      const answer = round.submitted_answers[player.id][category];
      const result = round.answer_results[player.id][category];
      const cell = document.createElement("td");
      if (answer) {
        const text = document.createElement("span");
        text.className = "answer-text";
        text.textContent = answer;
        cell.append(text);
      }

      const details = document.createElement("div");
      details.className = "answer-details";
      const status = document.createElement("span");
      status.className = "answer-status";
      status.dataset.status = result.status;
      status.textContent = answerStatusLabels[result.status];
      const points = document.createElement("span");
      points.className = "answer-points";
      points.textContent = `${result.points} pts`;
      details.append(status, points);
      cell.append(details);
      const reviewed = round.review?.entries.find((entry) => entry.player_id === player.id && entry.category === category);
      if (reviewed) {
        const decision = reviewStatus(reviewed.status);
        decision.classList.add("review-decision");
        cell.append(decision);
      }
      row.append(cell);
    }
    return row;
  });
  document.querySelector("#results-answers").replaceChildren(...rows);

  const totals = [
    ["Round total", round.round_scores],
    ["Match total", room.match_scores],
  ];
  const totalRows = totals.map(([name, scores]) => {
    const row = document.createElement("tr");
    const label = document.createElement("th");
    label.scope = "row";
    label.textContent = name;
    row.append(label);
    for (const player of room.players) {
      const cell = document.createElement("td");
      cell.textContent = `${scores[player.id]} pts`;
      row.append(cell);
    }
    return row;
  });
  document.querySelector("#results-totals").replaceChildren(...totalRows);
  document.querySelector("#round-ended-message").textContent =
    round.ended_by === "timeout" ? "Time ran out." : "A player clicked Done.";
  document.querySelector("#results-note").textContent = room.settings.community_word_approval
    ? "Eligible words outside the built-in list count only if the other player accepts them."
    : "Word Rally checks a built-in list for each category. Words missing from the list are marked Invalid.";
}

function showFinalResults() {
  const { winner_id: winnerId, rounds_played: roundsPlayed } = room.final_result;
  const winner = room.players.find((player) => player.id === winnerId);
  document.querySelector("#match-outcome").textContent = winner ? `${winner.label} wins!` : "Draw";
  document.querySelector("#match-rounds").textContent = `${roundsPlayed} ${roundsPlayed === 1 ? "round" : "rounds"} played`;
  finalRoundTitle.textContent = `Round ${room.round.number} results`;

  const standings = room.players.flatMap((player) => {
    const name = document.createElement("dt");
    name.textContent = player.label;
    const score = document.createElement("dd");
    score.textContent = `${room.match_scores[player.id]} pts`;
    return [name, score];
  });
  document.querySelector("#match-scores").replaceChildren(...standings);
}

function showRound(previousRoom) {
  const round = room.round;
  const active = room.phase === "active";
  const reviewing = room.phase === "review";
  const finished = room.phase === "finished";
  const scored = room.phase === "results" || finished;
  roundPanel.hidden = !round;
  roundForm.hidden = !active;
  roundClock.hidden = !active && !reviewing;
  reviewPanel.hidden = !reviewing;
  roundResults.hidden = !scored;
  finalResults.hidden = !finished;
  finalRoundTitle.hidden = !finished;

  if (!active && !reviewing) stopCountdown();
  if (!round) return;

  const newRound = previousRoom?.code !== room.code || previousRoom?.round?.number !== round.number;
  document.querySelector("#round-letter").textContent = round.letter;
  if (active) {
    if (newRound) buildAnswerFields(round.categories);
    if (restoredDrafts?.round_number === round.number) {
      for (const field of roundInputs.elements) {
        field.value = restoredDrafts.answers[field.name] ?? "";
      }
    }
  } else if (reviewing) {
    if (newRound || previousRoom?.phase !== "review" ||
        JSON.stringify(previousRoom.round.review.entries) !== JSON.stringify(round.review.entries)) {
      showReview(round);
    }
  } else if (scored && (newRound || previousRoom?.phase !== room.phase)) {
    showResults(round);
    if (finished) showFinalResults();
  }
  if (active || reviewing) {
    roundTimeLabel.textContent = reviewing ? "Review time remaining" : "Time remaining";
    const snapshotDeadline = performance.now() + (reviewing ? round.review.remaining_ms : round.remaining_ms);
    // A later snapshot can have spent longer in transit than an earlier one.
    const sameTimedPhase = !newRound && previousRoom.phase === room.phase;
    countdownDeadline = sameTimedPhase ? Math.min(countdownDeadline, snapshotDeadline) : snapshotDeadline;
    updateCountdown();
    if (countdownInterval === null) countdownInterval = setInterval(updateCountdown, 100);
  }
  restoredDrafts = null;
}

function canSendChat() {
  return chatReady && connected && socket?.readyState === WebSocket.OPEN &&
    pendingChat === null && chatRetryTimer === null;
}

function updateChatControls() {
  chatInput.disabled = !(chatReady && connected && socket?.readyState === WebSocket.OPEN);
  chatInput.readOnly = pendingChat !== null;
  chatSend.disabled = !canSendChat();

  const opponent = room?.players.find((player) => player.id !== session?.player_id);
  let status = "";
  if (room && !connected) {
    status = "Reconnect to send a message.";
  } else if (room && !opponent) {
    status = "Chat opens when the second player joins.";
  } else if (room && !chatReady) {
    status = "Loading chat history…";
  } else if (opponent && !opponent.connected) {
    status = opponent.label + " is disconnected. They can read messages when they return.";
  }
  if (chatStatus.textContent !== status) chatStatus.textContent = status;
  chatStatus.hidden = !status;
}

function createChatRow(message) {
  const row = document.createElement("li");
  const author = document.createElement("strong");
  const text = document.createElement("span");
  author.textContent = room.players.find((player) => player.id === message.player_id)?.label ?? "Player";
  text.textContent = message.text;
  row.append(author, text);
  return row;
}

function updateChatEmptyState() {
  chatEmpty.hidden = chatMessages.length > 0;
  chatHistory.hidden = chatMessages.length === 0;
}

function replaceChatHistory(messages) {
  const unique = new Map(messages.map((message) => [message.seq, message]));
  chatMessages = [...unique.values()].sort((first, second) => first.seq - second.seq).slice(-chatHistoryLimit);
  chatHistory.replaceChildren(...chatMessages.map(createChatRow));
  updateChatEmptyState();
  chatHistory.scrollTop = chatHistory.scrollHeight;
}

function addChatMessage(message) {
  const next = chatMessages.findIndex((entry) => entry.seq >= message.seq);
  const index = next < 0 ? chatMessages.length : next;
  if (chatMessages[index]?.seq === message.seq || (index === 0 && chatMessages.length === chatHistoryLimit)) return false;

  const followLatest = chatHistory.scrollHeight - chatHistory.scrollTop - chatHistory.clientHeight <= 8;
  chatMessages.splice(index, 0, message);
  chatHistory.insertBefore(createChatRow(message), chatHistory.children[index] ?? null);
  if (chatMessages.length > chatHistoryLimit) {
    chatMessages.shift();
    chatHistory.firstElementChild.remove();
  }
  updateChatEmptyState();
  if (followLatest) chatHistory.scrollTop = chatHistory.scrollHeight;
  return true;
}

function finishChatSend(status = "") {
  if (pendingChat) clearTimeout(pendingChat.timeout);
  pendingChat = null;
  setError(chatDelivery, status);
}

function showUnconfirmedChatDelivery() {
  finishChatSend("Delivery unconfirmed. Check the chat before sending again.");
  updateChatControls();
}

function normalizeChatText(text) {
  // Python str.strip() uses Unicode whitespace plus U+001C through U+001F.
  return text.replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, "");
}

function confirmChatMessage(message) {
  if (message.player_id !== session?.player_id || message.seq <= lastChatConfirmationSequence) return false;
  const index = chatAttempts.findIndex((attempt) =>
    message.seq > attempt.previousSequence && message.text === attempt.text);
  if (index === -1) return false;

  const attempt = chatAttempts[index];
  const latest = index === chatAttempts.length - 1;
  lastChatConfirmationSequence = message.seq;
  // Responses are ordered. For identical text, the first possible attempt gets the echo.
  chatAttempts.splice(0, index + 1);
  if (!latest) return false;

  if (attempt.draftVersion === chatDraftVersion && normalizeChatText(chatInput.value) === attempt.text) {
    chatInput.value = "";
    setError(chatError);
  }
  finishChatSend();
  return true;
}

function showChatError(message) {
  const ambiguous = chatReady && chatAttempts.length > 1;
  if (chatReady && chatAttempts.length > 0) {
    // A later reply must belong to a later send, even if this rejection's exact attempt is unknown.
    chatAttempts.shift();
    if (!ambiguous) finishChatSend();
  }
  if (message.code === "chat_rate_limited") {
    clearTimeout(chatRetryTimer);
    chatRetryTimer = setTimeout(() => {
      chatRetryTimer = null;
      updateChatControls();
    }, chatRateLimitDelay);
  }
  setError(chatError, ambiguous ? "A chat send was rejected. " + message.message : message.message);
  updateChatControls();
}

function clearChat() {
  finishChatSend();
  chatAttempts.length = 0;
  lastChatConfirmationSequence = 0;
  clearTimeout(chatRetryTimer);
  chatRetryTimer = null;
  chatReady = false;
  replaceChatHistory([]);
  chatInput.value = "";
  setError(chatError);
}

function updateControls() {
  updateChatControls();
  const recoveringSeat = Boolean(session) && !connected;
  const retryAvailable = recoveringSeat && !socket && reconnectTimer === null;
  for (const control of home.querySelectorAll(".primary, #show-join, [data-home]")) {
    control.disabled = Boolean(socket) || recoveringSeat;
  }
  let admissionStatus = "";
  if (admissionAction === "create") admissionStatus = "Creating room…";
  else if (admissionAction === "join") admissionStatus = "Joining room…";
  else if (recoveringSeat) {
    admissionStatus = retryAvailable ? "Connection paused. Retry to reclaim your seat." : "Reconnecting to your room…";
  }
  setError(homeSeatStatus, admissionStatus);
  retrySeatHome.hidden = !retryAvailable;
  retrySeatRoom.hidden = !retryAvailable;
  leaveSavedRoom.hidden = !recoveringSeat;
  if (!room) return;

  const isHost = room.players.some((player) => player.id === session?.player_id && player.host);
  const bothConnected = room.players.length === 2 && room.players.every((player) => player.connected);
  const inLobby = room.phase === "lobby";
  const inResults = room.phase === "results";
  const reviewing = room.phase === "review";
  const finished = room.phase === "finished";
  const ownReady = inResults && room.ready[session?.player_id];
  const canEndMatch = inResults && room.settings.mode === "endless";
  const canAnswer = room.phase === "active" && connected && !pendingAction;
  lobbyConnection.dataset.state = connected ? "online" : recoveringSeat && !retryAvailable ? "checking" : "unavailable";
  if (connected) {
    lobbyConnection.textContent = inLobby ? "Lobby connected" : "Room connected";
  } else if (recoveringSeat) {
    lobbyConnection.textContent = retryAvailable ? "Connection paused" : "Reconnecting…";
  } else {
    lobbyConnection.textContent = "Connection lost";
  }
  startButton.hidden = !isHost || !inLobby;
  startButton.disabled = !connected || !bothConnected || Boolean(pendingAction);
  readyButton.hidden = !inResults;
  readyButton.disabled = !inResults || !connected || ownReady || Boolean(pendingAction);
  endMatchButton.hidden = !canEndMatch;
  endMatchButton.disabled = !canEndMatch || !connected || Boolean(pendingAction);
  if (endMatchDialog.open && (!canEndMatch || !connected)) endMatchDialog.close();
  lobbySettings.hidden = !inLobby;
  hostSettings.hidden = !isHost || !inLobby;
  settingsForm.querySelector("button[type=submit]").disabled = !connected || Boolean(pendingAction);
  returnHomeButton.hidden = connected || finished;
  roundInputs.disabled = !canAnswer;
  doneButton.disabled = !canAnswer;
  for (const button of reviewOpponent.querySelectorAll("button")) {
    button.disabled = !reviewing || !connected || Boolean(pendingAction);
  }

  if (inLobby) {
    lobbyTitle.textContent = "Room lobby";
    if (!connected) {
      roomStatus.textContent = "Reconnect to continue.";
    } else if (pendingAction?.type === "start") {
      roomStatus.textContent = "Starting the round…";
    } else if (pendingAction?.type === "update_settings") {
      roomStatus.textContent = "Saving settings…";
    } else if (!bothConnected) {
      roomStatus.textContent = room.players.length < 2 ? "Share the room code with a friend." : "Waiting for the other player to reconnect.";
    } else {
      roomStatus.textContent = isHost ? "Both players are ready to start." : "Waiting for the host to start.";
    }
  } else if (finished) {
    lobbyTitle.textContent = "Match complete";
    roomStatus.textContent = "";
  } else {
    const title = `Round ${room.round.number}`;
    if (reviewing) {
      lobbyTitle.textContent = `${title} review`;
    } else {
      lobbyTitle.textContent = inResults ? `${title} ended` : title;
    }
    const otherPlayer = room.players.find((player) => player.id !== session?.player_id);
    if (!connected) {
      roomStatus.textContent = "Reconnect to continue.";
    } else if (!otherPlayer?.connected) {
      roomStatus.textContent = "The other player is disconnected.";
    } else {
      roomStatus.textContent = ownReady ? "Waiting for the other player to be ready." : "";
    }
  }
}

function keepPendingAction() {
  if (pendingAction?.type === "start") return room.phase === "lobby";
  if (pendingAction?.type === "update_settings" && room.phase === "lobby") {
    const requested = pendingAction.settings;
    const confirmed = room.settings;
    const settingsMatch = requested.duration === confirmed.duration &&
      requested.mode === confirmed.mode && requested.rounds === confirmed.rounds &&
      Boolean(requested.community_word_approval) === Boolean(confirmed.community_word_approval) &&
      requested.categories.length === confirmed.categories.length &&
      requested.categories.every((category, index) => category === confirmed.categories[index]);
    return !settingsMatch;
  }
  if (pendingAction?.type === "done") return room.phase === "active";
  if (pendingAction?.type === "end_match") return room.phase === "results";
  if (pendingAction?.type === "ready") return room.phase === "results" && !room.ready[session?.player_id];
  if (pendingAction?.type === "review_answer" && room.phase === "review" && room.round.number === pendingAction.round_number) {
    return room.round.review.entries.some((entry) => entry.player_id === pendingAction.player_id &&
      entry.category === pendingAction.category && entry.status === "pending");
  }
  return false;
}

document.addEventListener("focusin", (event) => {
  if (reviewVoteFocus && event.target !== document.body && !reviewOpponent.contains(event.target)) {
    reviewVoteFocus = null;
  }
});

function restoreReviewVoteFocus() {
  if (!reviewVoteFocus || !connected) return;
  const vote = reviewVoteFocus;
  if (room.code !== vote.roomCode || room.phase !== "review" || room.round.number !== vote.roundNumber) {
    reviewVoteFocus = null;
    return;
  }
  if (pendingAction?.type === "review_answer") return;
  reviewVoteFocus = null;

  const focused = document.activeElement;
  if (focused && focused !== document.body && !reviewOpponent.contains(focused)) return;

  const buttons = Array.from(reviewOpponent.querySelectorAll("button")).filter((button) => !button.disabled);
  const sameButton = buttons.find((button) => button.dataset.decision === vote.decision &&
    button.closest(".review-entry")?.dataset.category === vote.category);
  (sameButton ?? buttons[0] ?? reviewOpponentTitle).focus();
}

function showRoom(state) {
  const opening = lobby.hidden;
  const previousRoom = room;
  room = state;
  connected = true;
  admissionAction = null;
  if (!keepPendingAction()) pendingAction = null;
  roomCode.value = room.code;
  showPlayers();
  showSettings(room.settings);
  showRound(previousRoom);
  updateControls();
  restoreReviewVoteFocus();
  home.hidden = true;
  lobby.hidden = false;
  if (opening || (previousRoom?.phase !== room.phase && !chatPanel.contains(document.activeElement))) {
    lobbyTitle.focus();
  }
}

function stopSocket() {
  if (!socket) return;
  closingIntentionally = true;
  socket.close();
}

function resumeSession() {
  if (session) connect({ type: "resume", code: session.code, token: session.token });
}

function reconnect() {
  const delay = reconnectDelays[reconnectAttempt];
  if (!session || suspended || socket || reconnectTimer !== null || delay === undefined) return false;
  reconnectAttempt += 1;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    resumeSession();
  }, delay);
  return true;
}

function connect(request) {
  if (suspended || socket || reconnectTimer !== null || (session && request.type !== "resume")) return;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const connection = new WebSocket(`${protocol}//${window.location.host}/ws`);
  let admissionError = null;
  let receivedSession = false;
  let chatSubscriptionRequested = false;
  chatReady = false;
  socket = connection;
  admissionAction = request.type;
  connected = false;
  pendingAction = null;
  restoredDrafts = null;
  setError(homeError);
  setError(lobbyError);
  updateControls();

  const handshakeTimeout = setTimeout(() => connection.close(), 8000);
  connection.addEventListener("open", () => {
    if (socket === connection && !closingIntentionally) connection.send(JSON.stringify(request));
  });
  connection.addEventListener("message", (event) => {
    if (socket !== connection || closingIntentionally || admissionError) return;
    const message = JSON.parse(event.data);
    if (message.type === "session") {
      receivedSession = true;
      storeSession({ code: message.code, player_id: message.player_id, token: message.token });
    } else if (message.type === "drafts") {
      restoredDrafts = message;
    } else if (message.type === "state") {
      clearTimeout(handshakeTimeout);
      reconnectAttempt = 0;
      if (pendingAction) setError(lobbyError);
      showRoom(message.room);
      if (receivedSession && !chatSubscriptionRequested && room.players.length === 2) {
        chatSubscriptionRequested = true;
        connection.send(JSON.stringify({ type: "chat_subscribe" }));
      }
    } else if (message.type === "chat_history" && chatSubscriptionRequested && !chatReady) {
      replaceChatHistory(message.messages);
      for (const entry of chatMessages) confirmChatMessage(entry);
      // The old socket is closed. Unmatched attempts can no longer receive a response.
      chatAttempts.length = 0;
      chatReady = true;
      updateChatControls();
    } else if (message.type === "chat_message" && chatReady) {
      if (addChatMessage(message.message) && confirmChatMessage(message.message)) updateChatControls();
    } else if (message.type === "chat_error" && chatSubscriptionRequested) {
      showChatError(message);
    } else if (message.type === "error") {
      if (admissionAction) {
        clearTimeout(handshakeTimeout);
        admissionError = message;
        // Keep ownership until close completes. A retry must never overlap a closing socket.
        connection.close();
        return;
      }
      pendingAction = null;
      setError(lobby.hidden ? homeError : lobbyError, message.message);
      updateControls();
      restoreReviewVoteFocus();
    }
  });
  connection.addEventListener("close", () => {
    clearTimeout(handshakeTimeout);
    if (socket !== connection) return;
    socket = null;
    chatReady = false;
    const intentional = closingIntentionally;
    closingIntentionally = false;
    connected = false;
    admissionAction = null;
    pendingAction = null;
    if (pendingChat) showUnconfirmedChatDelivery();
    if (suspended) return;
    if (room) showPlayers();
    const errorElement = lobby.hidden ? homeError : lobbyError;
    if (intentional) {
      resumeSession();
    } else if (admissionError) {
      if (request.type === "resume" && ["invalid_session", "room_not_found"].includes(admissionError.code)) {
        leaveRoom();
        setError(homeError, admissionError.message + " Create or join a room to continue.");
        return;
      }
      if (admissionError.code === "duplicate_connection" && request.type === "resume") {
        if (!reconnect()) {
          setError(errorElement, "Your seat is still connected. Retry after the other connection closes.");
        }
      } else {
        setError(errorElement, admissionError.message);
      }
    } else if (session) {
      if (!reconnect()) setError(errorElement, "Could not reconnect to your room. Try again when the connection is available.");
    } else {
      setError(homeError, "Could not connect to the lobby server. Try again.");
    }
    updateControls();
  });
}

function sendAction(message) {
  if (!connected || pendingAction || socket?.readyState !== WebSocket.OPEN) return;
  pendingAction = message;
  if (message.type === "review_answer" && reviewOpponent.contains(document.activeElement)) {
    // Disabling the vote button can blur it before the next snapshot arrives.
    reviewVoteFocus = {
      roomCode: room.code, roundNumber: message.round_number, category: message.category, decision: message.decision,
    };
  }
  setError(lobbyError);
  updateControls();
  socket.send(JSON.stringify(message));
}

for (const form of [createForm, settingsForm]) {
  form.querySelector(".settings-fields").append(settingsTemplate.content.cloneNode(true));
  form.elements.mode.addEventListener("change", () => updateRoundCount(form));
}

document.querySelector("#show-create").addEventListener("click", () => {
  showEntry(createForm);
  document.querySelector("#create-title").focus();
});
document.querySelector("#show-join").addEventListener("click", () => {
  showEntry(joinForm);
  joinCode.focus();
});
for (const button of document.querySelectorAll("[data-home]")) {
  button.addEventListener("click", () => {
    const action = button.form === createForm ? "#show-create" : "#show-join";
    showEntry(null);
    document.querySelector(action).focus();
  });
}

createForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (admissionAction) return;
  const settings = readSettings(createForm, homeError);
  if (settings) connect({ type: "create", settings });
});
joinForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!admissionAction) connect({ type: "join", code: joinCode.value.trim().toUpperCase() });
});
settingsForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const settings = readSettings(settingsForm, lobbyError);
  if (settings) sendAction({ type: "update_settings", settings });
});
chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.shiftKey) event.preventDefault();
});
chatInput.addEventListener("input", () => {
  chatDraftVersion += 1;
  if (chatRetryTimer === null) setError(chatError);
});
chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!canSendChat()) return;
  const rawText = chatInput.value;
  const text = normalizeChatText(rawText);
  if (!text || /[\u000a-\u000d\u001c-\u001e\u0085\u2028\u2029]/u.test(rawText)) {
    setError(chatError, "Enter a nonblank message on one line.");
    return;
  }
  // JSON would escape an unpaired surrogate, which the server's UTF-8 parser rejects.
  if (/[\uD800-\uDFFF]/u.test(text)) {
    setError(chatError, "Enter valid Unicode text.");
    return;
  }
  if (chatEncoder.encode(text).length > chatByteLimit) {
    setError(chatError, "Messages can contain up to " + chatByteLimit + " UTF-8 bytes.");
    return;
  }
  pendingChat = {
    text,
    previousSequence: chatMessages.at(-1)?.seq ?? 0,
    draftVersion: chatDraftVersion,
    // The server can suppress a repeated rate-limit error.
    timeout: setTimeout(showUnconfirmedChatDelivery, chatConfirmationDelay),
  };
  chatAttempts.push(pendingChat);
  setError(chatError);
  setError(chatDelivery, "Sending…");
  updateChatControls();
  socket.send(JSON.stringify({ type: "chat_send", text }));
});

startButton.addEventListener("click", () => sendAction({ type: "start" }));
roundForm.addEventListener("input", (event) => {
  if (room?.phase !== "active" || !connected || pendingAction || socket?.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({
    type: "draft",
    round_number: room.round.number,
    category: event.target.name,
    answer: event.target.value,
  }));
});
roundForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (room?.phase === "active") sendAction({ type: "done", round_number: room.round.number });
});
readyButton.addEventListener("click", () => {
  if (room?.phase === "results") sendAction({ type: "ready", round_number: room.round.number });
});
endMatchButton.addEventListener("click", () => {
  if (!endMatchButton.disabled && !endMatchDialog.open) endMatchDialog.showModal();
});
endMatchDialog.addEventListener("submit", (event) => {
  if (event.submitter?.value === "confirm" && room?.phase === "results") {
    sendAction({ type: "end_match", round_number: room.round.number });
  }
});

document.querySelector("#copy-code").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(roomCode.value);
    copyStatus.textContent = "Room code copied.";
  } catch {
    roomCode.focus();
    roomCode.select();
    copyStatus.textContent = "Room code selected. Copy it to share.";
  }
});

function leaveRoom(entry = null) {
  clearTimeout(reconnectTimer);
  reconnectTimer = null;
  stopSocket();
  storeSession(null);
  clearChat();
  room = null;
  admissionAction = null;
  connected = false;
  pendingAction = null;
  reviewVoteFocus = null;
  reconnectAttempt = 0;
  restoredDrafts = null;
  confirmedSettings = null;
  copyStatus.textContent = "";
  stopCountdown();
  lobby.hidden = true;
  home.hidden = false;
  showEntry(entry);
  updateControls();
  document.querySelector(entry === createForm ? "#create-title" : "#show-create").focus();
}

returnHomeButton.addEventListener("click", () => leaveRoom());
leaveSavedRoom.addEventListener("click", () => leaveRoom());
document.querySelector("#create-new-room").addEventListener("click", () => leaveRoom(createForm));

function retrySeat() {
  if (!session || suspended || socket || reconnectTimer !== null) return;
  reconnectAttempt = 0;
  resumeSession();
}

retrySeatHome.addEventListener("click", retrySeat);
retrySeatRoom.addEventListener("click", retrySeat);

window.addEventListener("pagehide", () => {
  suspended = true;
  connected = false;
  updateChatControls();
  clearTimeout(reconnectTimer);
  reconnectTimer = null;
  stopCountdown();
  stopSocket();
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted && suspended) {
    suspended = false;
    reconnectAttempt = 0;
    resumeSession();
    updateControls();
  }
});

retryButton.addEventListener("click", checkServer);
checkServer();
resumeSession();
