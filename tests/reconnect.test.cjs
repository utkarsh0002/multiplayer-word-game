const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { TextEncoder } = require("node:util");
const { runInNewContext } = require("node:vm");

const source = readFileSync(join(__dirname, "..", "static", "app.js"), "utf8");
const sessionKey = "word-rally-room";
const savedSeat = { code: "ABC234", player_id: "player-one", token: "t".repeat(32) };
const resume = { type: "resume", code: savedSeat.code, token: savedSeat.token };
const duplicate = {
  type: "error",
  code: "duplicate_connection",
  message: "This player is already connected in another window.",
};
const lobbyState = {
  type: "state",
  room: {
    code: savedSeat.code,
    phase: "lobby",
    settings: { categories: ["animals"], duration: 60, mode: "fixed", rounds: 5 },
    players: [{ id: savedSeat.player_id, label: "Player 1", host: true, connected: true }],
  },
};

class EventTargetStub {
  listeners = new Map();

  addEventListener(type, listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  emit(type, event = {}) {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

class ElementStub extends EventTargetStub {
  children = [];
  dataset = {};
  attributes = new Map();
  value = "";
  scrollTop = 0;
  scrollHeight = 0;
  clientHeight = 0;
  elements = {
    duration: { value: 60 },
    mode: { value: "fixed", addEventListener() {} },
    rounds: { value: 5 },
    community_word_approval: { checked: false },
  };
  selectors = new Map();
  content = { querySelectorAll: () => [], cloneNode: () => new ElementStub() };

  querySelector(selector) {
    if (!this.selectors.has(selector)) this.selectors.set(selector, new ElementStub());
    return this.selectors.get(selector);
  }

  querySelectorAll() { return []; }
  createElement(name) { return Object.assign(new ElementStub(), { tagName: name.toUpperCase() }); }
  get firstElementChild() { return this.children[0] ?? null; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  contains(node) { return this === node || this.children.some((child) => child.contains(node)); }
  append(...children) { for (const child of children) this.insertBefore(child, null); }
  insertBefore(child, reference) {
    child.remove();
    const index = reference === null ? this.children.length : this.children.indexOf(reference);
    assert.ok(index >= 0, "reference is a child of this element");
    this.children.splice(index, 0, child);
    child.parentElement = this;
  }
  remove() {
    if (!this.parentElement) return;
    const siblings = this.parentElement.children;
    siblings.splice(siblings.indexOf(this), 1);
    this.parentElement = null;
  }
  replaceChildren(...children) {
    for (const child of this.children) child.parentElement = null;
    this.children = [];
    this.append(...children);
  }
  set innerHTML(_value) { assert.fail("server text must be rendered through DOM text APIs"); }
  focus() {}
}

function fakeClock() {
  let now = 0;
  let nextId = 1;
  const timers = new Map();

  return {
    setTimeout(callback, delay) {
      const id = nextId++;
      timers.set(id, { callback, due: now + delay });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    advance(ms) {
      const end = now + ms;
      while (true) {
        const next = [...timers.entries()]
          .filter(([, timer]) => timer.due <= end)
          .sort(([, first], [, second]) => first.due - second.due)[0];
        if (!next) break;
        const [id, timer] = next;
        timers.delete(id);
        now = timer.due;
        timer.callback();
      }
      now = end;
    },
  };
}

function startPage(seat = savedSeat) {
  const storage = new Map();
  if (seat) storage.set(sessionKey, JSON.stringify(seat));
  const sockets = [];
  const clock = fakeClock();
  const document = new ElementStub();
  const get = (selector) => document.querySelector(selector);
  for (const selector of ["#lobby", "#retry-seat-home", "#retry-seat-room"]) get(selector).hidden = true;
  const window = Object.assign(new EventTargetStub(), {
    location: { protocol: "http:", host: "localhost:8000" },
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
  });

  class Socket extends EventTargetStub {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;
    readyState = Socket.CONNECTING;
    sent = [];

    constructor() {
      super();
      assert.ok(sockets.every((socket) => socket.readyState === Socket.CLOSED), "socket attempts overlap");
      sockets.push(this);
    }

    open() {
      this.readyState = Socket.OPEN;
      this.emit("open");
    }

    receive(message) {
      assert.equal(this.readyState, Socket.OPEN);
      this.emit("message", { data: JSON.stringify(message) });
    }

    send(data) {
      assert.equal(this.readyState, Socket.OPEN);
      this.sent.push(JSON.parse(data));
    }

    close() {
      if (this.readyState !== Socket.CLOSED) this.readyState = Socket.CLOSING;
    }

    finishClose() {
      this.readyState = Socket.CLOSED;
      this.emit("close");
    }
  }

  runInNewContext(source, {
    document,
    window,
    WebSocket: Socket,
    sessionStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    setInterval: () => Symbol("interval"),
    clearInterval: clock.clearTimeout,
    performance: { now: () => 0 },
    TextEncoder,
    fetch: async () => ({ ok: true, json: async () => ({ status: "ok" }) }),
    AbortSignal: { timeout: () => undefined },
  }, { filename: "static/app.js" });

  return {
    get, sockets, window, advance: clock.advance,
    currentSeat: () => JSON.parse(storage.get(sessionKey) ?? "null"),
  };
}

function rejectDuplicate(socket) {
  socket.open();
  assert.deepEqual(socket.sent, [resume]);
  socket.receive(duplicate);
}

function admit(socket) {
  socket.receive({ type: "session", ...savedSeat });
  socket.receive(lobbyState);
}

test("repeated duplicate replies keep the token and stop after increasing automatic delays", () => {
  const page = startPage();
  const delays = [1200, 2400, 4800];
  for (const [index, delay] of delays.entries()) {
    const socket = page.sockets[index];
    rejectDuplicate(socket);
    assert.deepEqual(page.currentSeat(), savedSeat);
    socket.finishClose();
    page.advance(delay - 1);
    assert.equal(page.sockets.length, index + 1);
    page.advance(1);
    assert.equal(page.sockets.length, index + 2);
  }

  const last = page.sockets.at(-1);
  rejectDuplicate(last);
  last.finishClose();
  page.advance(60_000);
  assert.equal(page.sockets.length, 4);
  assert.deepEqual(page.currentSeat(), savedSeat);
  assert.equal(page.get("#retry-seat-home").hidden, false);
});

test("competing recovery triggers do not overlap a closing socket or scheduled retry", () => {
  const page = startPage();
  const first = page.sockets[0];
  rejectDuplicate(first);
  // Hold the close event past the first retry delay.
  page.window.emit("pageshow", { persisted: true });
  page.get("#retry-seat-home").emit("click");
  page.advance(1500);
  assert.equal(page.sockets.length, 1);

  first.finishClose();
  page.window.emit("pageshow", { persisted: true });
  page.get("#retry-seat-home").emit("click");
  page.advance(1199);
  assert.equal(page.sockets.length, 1);
  page.advance(1);
  assert.equal(page.sockets.length, 2);
  page.advance(2000);
  assert.equal(page.sockets.length, 2);
  assert.deepEqual(page.currentSeat(), savedSeat);
});

test("manual retry resumes the original seat, restores the lobby, and resets backoff", () => {
  const page = startPage();
  for (const delay of [1200, 2400, 4800, 0]) {
    const socket = page.sockets.at(-1);
    rejectDuplicate(socket);
    socket.finishClose();
    page.advance(delay);
  }
  const retry = page.get("#retry-seat-home");
  assert.equal(retry.hidden, false);
  retry.emit("click");
  retry.emit("click");
  assert.equal(page.sockets.length, 5);
  const reconnected = page.sockets.at(-1);
  reconnected.open();
  assert.deepEqual(reconnected.sent, [resume]);
  admit(reconnected);

  assert.deepEqual(page.currentSeat(), savedSeat);
  assert.equal(page.get("#lobby").hidden, false);
  assert.equal(page.get("#room-code").value, savedSeat.code);
  assert.match(page.get("#players").children[0].children[0].textContent, /\(you\)/);
  assert.equal(page.get("#retry-seat-room").hidden, true);

  reconnected.finishClose();
  page.advance(1199);
  assert.equal(page.sockets.length, 5);
  page.advance(1);
  assert.equal(page.sockets.length, 6);
});

test("confirmed invalid and expired sessions clear the token and stop recovery", () => {
  for (const code of ["invalid_session", "room_not_found"]) {
    const page = startPage();
    const first = page.sockets[0];
    first.open();
    first.receive({ type: "error", code, message: "The saved seat is unavailable." });
    first.finishClose();
    page.advance(60_000);
    assert.equal(page.currentSeat(), null);
    assert.equal(page.sockets.length, 1);
    assert.equal(page.get("#retry-seat-home").hidden, true);
  }
});

test("a nonterminal resume error offers an in-room retry until the player explicitly leaves", () => {
  const page = startPage();
  const first = page.sockets[0];
  first.open();
  admit(first);
  first.finishClose();
  page.advance(1200);

  const automatic = page.sockets.at(-1);
  automatic.open();
  assert.deepEqual(automatic.sent, [resume]);
  automatic.receive({ type: "error", code: "invalid_message", message: "First create, join, or rejoin a room." });
  automatic.finishClose();
  assert.deepEqual(page.currentSeat(), savedSeat);
  const retry = page.get("#retry-seat-room");
  assert.equal(retry.hidden, false);

  retry.emit("click");
  const manual = page.sockets.at(-1);
  manual.open();
  assert.deepEqual(manual.sent, [resume]);
  manual.finishClose();
  const leave = page.get("#return-home");
  assert.equal(leave.hidden, false);
  leave.emit("click");
  assert.equal(page.currentSeat(), null);
  assert.equal(page.get("#home").hidden, false);
  page.advance(60_000);
  assert.equal(page.sockets.length, 3);
});

test("an ordinary Join still opens one connection and saves the admitted seat", () => {
  const page = startPage(null);
  assert.equal(page.sockets.length, 0);
  page.get("#join-code").value = " abc234 ";
  page.get("#join-form").emit("submit", { preventDefault() {} });
  const joining = page.sockets[0];
  joining.open();
  assert.deepEqual(joining.sent, [{ type: "join", code: savedSeat.code }]);
  admit(joining);
  assert.deepEqual(page.currentSeat(), savedSeat);
  assert.equal(page.get("#lobby").hidden, false);
});

const opponent = { id: "player-two", label: "Player 2", host: false, connected: true };
const subscribe = { type: "chat_subscribe" };

function roomWithOpponent(phase = "lobby", number = 1, opponentConnected = true) {
  const room = {
    ...lobbyState.room,
    phase,
    players: [...lobbyState.room.players, { ...opponent, connected: opponentConnected }],
  };
  if (phase !== "lobby") {
    room.round = { number, letter: "A", categories: ["animals"], remaining_ms: 30_000 };
  }
  if (phase === "review") room.round.review = { remaining_ms: 10_000, entries: [] };
  if (phase === "results" || phase === "finished") {
    room.round.ended_by = "done";
    room.round.submitted_answers = Object.fromEntries(room.players.map(({ id }) => [id, { animals: "" }]));
    room.round.answer_results = Object.fromEntries(room.players.map(({ id }) => [id, {
      animals: { status: "blank", points: 0 },
    }]));
    room.round.round_scores = Object.fromEntries(room.players.map(({ id }) => [id, 0]));
    room.match_scores = { ...room.round.round_scores };
  }
  if (phase === "results") room.ready = { [savedSeat.player_id]: false, [opponent.id]: false };
  if (phase === "finished") room.final_result = { winner_id: null, rounds_played: number };
  return { type: "state", room };
}

function chatEntry(seq, text, playerId = opponent.id) {
  return { seq, player_id: playerId, text };
}

function admitChat(page, messages = []) {
  const socket = page.sockets.at(-1);
  socket.open();
  socket.receive({ type: "session", ...savedSeat });
  socket.receive(roomWithOpponent());
  socket.receive({ type: "chat_history", messages });
  return socket;
}

function visibleMessages(page) {
  return page.get("#chat-history").children.map((row) => row.children[1].textContent);
}

function submitChat(page, value) {
  page.get("#chat-input").value = value;
  page.get("#chat-form").emit("submit", { preventDefault() {} });
}

test("chat subscribes once after admission and a state with both seats, even if the opponent is offline", () => {
  const page = startPage();
  const socket = page.sockets[0];
  socket.open();
  assert.deepEqual(socket.sent, [resume]);
  socket.receive({ type: "session", ...savedSeat });
  assert.deepEqual(socket.sent, [resume]);
  socket.receive(lobbyState);
  assert.deepEqual(socket.sent, [resume]);
  assert.equal(page.get("#chat-input").disabled, true);

  socket.receive(roomWithOpponent("lobby", 1, false));
  assert.deepEqual(socket.sent, [resume, subscribe]);
  socket.receive(roomWithOpponent());
  assert.deepEqual(socket.sent, [resume, subscribe]);
  assert.equal(page.get("#chat-send").disabled, true, "wait for the history response");
  socket.receive({ type: "chat_history", messages: [] });
  assert.equal(page.get("#chat-input").disabled, false);
  assert.equal(page.get("#chat-empty").hidden, false);
});

test("chat renders history oldest first with player identity and no invented timestamp", () => {
  const page = startPage();
  admitChat(page, [chatEntry(3, "Third"), chatEntry(2, "Second"), chatEntry(2, "Second")]);
  assert.deepEqual(visibleMessages(page), ["Second", "Third"]);
  const first = page.get("#chat-history").children[0];
  assert.deepEqual(first.children.map((child) => child.textContent), ["Player 2", "Second"]);
  assert.equal(page.get("#chat-empty").hidden, true);
});

test("reconnect preserves the visible log until the authoritative snapshot replaces it", () => {
  const page = startPage();
  const first = admitChat(page, [chatEntry(1, "Outdated"), chatEntry(2, "Still here")]);
  first.finishClose();
  assert.deepEqual(visibleMessages(page), ["Outdated", "Still here"]);
  page.advance(1200);
  const reconnected = page.sockets.at(-1);
  reconnected.open();
  assert.deepEqual(reconnected.sent, [resume]);
  reconnected.receive({ type: "session", ...savedSeat });
  reconnected.receive(roomWithOpponent());
  assert.deepEqual(reconnected.sent, [resume, subscribe]);
  assert.deepEqual(visibleMessages(page), ["Outdated", "Still here"]);

  reconnected.receive({ type: "chat_history", messages: [chatEntry(2, "Still here"), chatEntry(3, "While away")] });
  reconnected.receive({ type: "chat_message", message: chatEntry(3, "While away") });
  assert.deepEqual(visibleMessages(page), ["Still here", "While away"]);
});

test("live chat is visible in sequence order and duplicate sequences are ignored", () => {
  const page = startPage();
  const socket = admitChat(page, [chatEntry(8, "First")]);
  socket.receive({ type: "chat_message", message: chatEntry(10, "Last") });
  socket.receive({ type: "chat_message", message: chatEntry(9, "Middle") });
  socket.receive({ type: "chat_message", message: chatEntry(9, "Repeated sequence") });
  assert.deepEqual(visibleMessages(page), ["First", "Middle", "Last"]);
});

test("the visible chat log retains only the newest 50 entries", () => {
  const page = startPage();
  const history = Array.from({ length: 50 }, (_, index) => chatEntry(index + 1, String(index + 1)));
  const socket = admitChat(page, history);
  socket.receive({ type: "chat_message", message: chatEntry(51, "51") });
  socket.receive({ type: "chat_message", message: chatEntry(1, "Evicted") });
  assert.deepEqual(visibleMessages(page), Array.from({ length: 50 }, (_, index) => String(index + 2)));
});

test("HTML-looking chat text renders as literal text", () => {
  const page = startPage();
  const text = '<img src=x onerror="alert(1)"><script>alert(1)</script>';
  const socket = admitChat(page, [chatEntry(1, text)]);
  socket.receive({ type: "chat_message", message: chatEntry(2, text) });
  assert.deepEqual(visibleMessages(page), [text, text]);
  for (const row of page.get("#chat-history").children) {
    assert.deepEqual(row.children[1].children, []);
  }
});

test("blank, multiline, and oversized messages stay local while exactly 500 UTF-8 bytes can be sent", () => {
  const page = startPage();
  const socket = admitChat(page);
  submitChat(page, " \t ");
  submitChat(page, "two\u2028lines");
  assert.deepEqual(socket.sent, [resume, subscribe]);
  assert.match(page.get("#chat-error").textContent, /(?:single|one).line/i);
  submitChat(page, "\uD800");
  assert.deepEqual(socket.sent, [resume, subscribe]);
  assert.match(page.get("#chat-error").textContent, /valid Unicode/i);

  submitChat(page, "🙂".repeat(126));
  assert.deepEqual(socket.sent, [resume, subscribe]);
  assert.match(page.get("#chat-error").textContent, /500.*bytes/i);

  const limit = "🙂".repeat(125);
  submitChat(page, ` ${limit} `);
  assert.deepEqual(socket.sent, [resume, subscribe, { type: "chat_send", text: limit }]);
});

test("Shift+Enter prevents the single-line input's default form submission", () => {
  const page = startPage();
  const socket = admitChat(page);
  const input = page.get("#chat-input");
  input.value = "Still editing";
  let prevented = false;
  input.emit("keydown", { key: "Enter", shiftKey: true, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.deepEqual(socket.sent, [resume, subscribe]);
  assert.equal(input.value, "Still editing");
});

test("local disconnect disables sending and retains the current history", () => {
  const page = startPage();
  const socket = admitChat(page, [chatEntry(1, "Keep this")]);
  socket.finishClose();
  assert.equal(page.get("#chat-input").disabled, true);
  assert.equal(page.get("#chat-send").disabled, true);
  assert.deepEqual(visibleMessages(page), ["Keep this"]);
  submitChat(page, "Cannot send yet");
  assert.deepEqual(socket.sent, [resume, subscribe]);
});

test("an opponent disconnect keeps the transcript visible and the local composer available", () => {
  const page = startPage();
  const socket = admitChat(page, [chatEntry(1, "See you soon")]);
  socket.receive(roomWithOpponent("lobby", 1, false));
  assert.match(page.get("#chat-status").textContent, /Player 2.*disconnected/i);
  assert.equal(page.get("#chat-input").disabled, false);
  assert.deepEqual(visibleMessages(page), ["See you soon"]);
  submitChat(page, "For when you return");
  assert.deepEqual(socket.sent.at(-1), { type: "chat_send", text: "For when you return" });
});

test("an unacknowledged message is never resent by reconnect", () => {
  const page = startPage();
  const first = admitChat(page);
  submitChat(page, "Delivery uncertain");
  assert.deepEqual(first.sent.at(-1), { type: "chat_send", text: "Delivery uncertain" });
  first.finishClose();
  page.advance(1200);
  const reconnected = admitChat(page);
  assert.deepEqual(reconnected.sent, [resume, subscribe]);
  assert.deepEqual(visibleMessages(page), []);
});

test("chat events leave pending game actions and game errors alone", () => {
  const page = startPage();
  const socket = admitChat(page);
  const start = page.get("#start-game");
  start.emit("click");
  assert.equal(start.disabled, true);
  submitChat(page, "Chat while Start is pending");
  socket.receive({ type: "chat_message", message: chatEntry(1, "Ready for the game?") });
  socket.receive({ type: "chat_error", code: "chat_rate_limited", message: "Wait before sending." });
  assert.equal(start.disabled, true);
  assert.equal(page.get("#lobby-error").hidden, true);
  assert.equal(page.get("#chat-error").textContent, "Wait before sending.");
  assert.deepEqual(visibleMessages(page), ["Ready for the game?"]);

  start.emit("click");
  assert.deepEqual(socket.sent.slice(2), [{ type: "start" }, { type: "chat_send", text: "Chat while Start is pending" }]);
  socket.receive({ type: "error", code: "players_required", message: "Wait for another player." });
  socket.receive({ type: "chat_error", code: "chat_rate_limited", message: "Still waiting." });
  assert.equal(page.get("#lobby-error").textContent, "Wait for another player.");
  assert.equal(page.get("#chat-error").textContent, "Still waiting.");
});

test("chat persists through active round, review, results, ready and final results", () => {
  const page = startPage();
  const socket = admitChat(page, [chatEntry(1, "From the lobby")]);
  const waitingForReady = roomWithOpponent("results");
  waitingForReady.room.ready[savedSeat.player_id] = true;
  const phases = [
    roomWithOpponent("active"), roomWithOpponent("review"), roomWithOpponent("results"),
    waitingForReady, roomWithOpponent("active", 2), roomWithOpponent("finished", 2),
  ];
  for (const state of phases) {
    socket.receive(state);
    assert.deepEqual(visibleMessages(page), ["From the lobby"]);
    assert.equal(page.get("#chat-input").disabled, false);
  }
  assert.deepEqual(socket.sent, [resume, subscribe]);
});

test("explicitly leaving clears room chat and the unsent draft", () => {
  const page = startPage();
  const socket = admitChat(page, [chatEntry(1, "Retained until Leave")]);
  page.get("#chat-input").value = "Draft";
  socket.finishClose();
  page.get("#return-home").emit("click");
  assert.deepEqual(visibleMessages(page), []);
  assert.equal(page.get("#chat-input").value, "");
  assert.equal(page.currentSeat(), null);
  assert.equal(page.get("#lobby").hidden, true);
});

test("confirmed invalid session or expired room clears local chat", () => {
  for (const code of ["invalid_session", "room_not_found"]) {
    const page = startPage();
    const first = admitChat(page, [chatEntry(1, "Old room")]);
    first.finishClose();
    page.advance(1200);
    const retry = page.sockets.at(-1);
    retry.open();
    retry.receive({ type: "error", code, message: "That room cannot be resumed." });
    retry.finishClose();
    assert.deepEqual(visibleMessages(page), []);
    assert.equal(page.get("#chat-input").disabled, true);
    assert.equal(page.currentSeat(), null);
  }
});
