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
    const child = this.querySelectorAll(selector)[0];
    if (child || this.tagName) return child ?? null;
    if (!this.selectors.has(selector)) {
      this.selectors.set(selector, Object.assign(new ElementStub(), { ownerDocument: this.ownerDocument }));
    }
    return this.selectors.get(selector);
  }

  matches(selector) {
    if (selector === "input[name=category]") return this.tagName === "INPUT" && this.name === "category";
    if (selector === "button") return this.tagName === "BUTTON";
    if (selector === "button:not(:disabled)") return this.tagName === "BUTTON" && !this.disabled;
    if (selector.startsWith(".")) return this.className?.split(" ").includes(selector.slice(1));
    const attribute = /^\[data-([a-z]+)(?:="([^"]+)")?\]$/.exec(selector);
    if (!attribute) return false;
    const value = this.dataset[attribute[1]];
    return attribute[2] === undefined ? value !== undefined : value === attribute[2];
  }

  querySelectorAll(selector) {
    return this.children.flatMap((child) => [
      ...(child.matches(selector) ? [child] : []),
      ...child.querySelectorAll(selector),
    ]);
  }

  closest(selector) {
    return this.matches(selector) ? this : this.parentElement?.closest(selector) ?? null;
  }

  createElement(name) {
    return Object.assign(new ElementStub(), { tagName: name.toUpperCase(), ownerDocument: this.ownerDocument });
  }

  get disabled() { return this.isDisabled; }
  set disabled(value) {
    this.isDisabled = value;
    if (value && this.ownerDocument?.activeElement === this) this.ownerDocument.activeElement = this.ownerDocument.body;
  }

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
  focus() {
    if (this.disabled || !this.ownerDocument || this.ownerDocument.activeElement === this) return;
    this.ownerDocument.activeElement = this;
    this.ownerDocument.emit("focusin", { target: this });
  }
}

function addSettingsControls(form, document) {
  const controls = [];
  function control(tag, name, properties) {
    const field = Object.assign(document.createElement(tag), { name, ...properties });
    controls.push(field);
    form.append(field);
    return field;
  }

  for (const value of ["animals", "countries", "food", "sports", "cities"]) {
    control("input", "category", { type: "checkbox", value, checked: true });
  }
  form.elements = Object.assign(controls, {
    duration: control("input", "duration", { type: "number", value: "60" }),
    mode: control("select", "mode", { value: "fixed" }),
    rounds: control("input", "rounds", { type: "number", value: "5" }),
    community_word_approval: control("input", "community_word_approval", {
      type: "checkbox", value: "on", checked: false,
    }),
  });
}

function fakeClock() {
  let now = 0;
  let nextId = 1;
  const timers = new Map();

  return {
    now: () => now,
    setTimeout(callback, delay) {
      const id = nextId++;
      timers.set(id, { callback, due: now + delay });
      return id;
    },
    setInterval(callback, delay) {
      const id = nextId++;
      timers.set(id, { callback, due: now + delay, interval: delay });
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
        if (timer.interval) timers.set(id, { ...timer, due: now + timer.interval });
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
  document.ownerDocument = document;
  document.body = document.createElement("body");
  document.activeElement = document.body;
  const get = (selector) => document.querySelector(selector);
  for (const selector of ["#create-form", "#settings-form"]) addSettingsControls(get(selector), document);
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
    setInterval: clock.setInterval,
    clearInterval: clock.clearTimeout,
    performance: { now: clock.now },
    FormData: class {
      constructor(form) {
        this.fields = new Map();
        for (const field of form.elements) {
          if (!field.name || field.disabled || (field.type === "checkbox" && !field.checked)) continue;
          const values = this.fields.get(field.name) ?? [];
          values.push(String(field.value));
          this.fields.set(field.name, values);
        }
      }
      getAll(name) { return this.fields.get(name) ?? []; }
      get(name) { return this.getAll(name)[0] ?? null; }
      has(name) { return this.fields.has(name); }
    },
    TextEncoder,
    fetch: async () => ({ ok: true, json: async () => ({ status: "ok" }) }),
    AbortSignal: { timeout: () => undefined },
  }, { filename: "static/app.js" });

  return {
    get, sockets, document, window, advance: clock.advance,
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

function setSettingsFields(page, settings, selector = "#settings-form") {
  const form = page.get(selector);
  for (const category of form.querySelectorAll("input[name=category]")) {
    category.checked = settings.categories.includes(category.value);
  }
  form.elements.duration.value = String(settings.duration);
  form.elements.mode.value = settings.mode;
  form.elements.mode.emit("change");
  if (settings.rounds !== null) form.elements.rounds.value = String(settings.rounds);
  form.elements.community_word_approval.checked = settings.community_word_approval;
}

function submitForm(page, selector) {
  page.get(selector).emit("submit", { preventDefault() {} });
}

test("pending Start survives unrelated peer snapshots until a phase change or rejection", () => {
  const page = startPage();
  const socket = admitChat(page);
  const start = page.get("#start-game");

  start.emit("click");
  socket.receive(roomWithOpponent("lobby", 1, false));
  socket.receive(roomWithOpponent());
  assert.equal(start.disabled, true);
  start.emit("click");
  assert.deepEqual(socket.sent.slice(2), [{ type: "start" }]);

  socket.receive({ type: "error", code: "players_required", message: "Both players must be connected." });
  assert.equal(start.disabled, false);
  start.emit("click");
  socket.receive(roomWithOpponent("active"));
  assert.equal(start.hidden, true);
  assert.equal(page.get("#done-round").disabled, false);
});

test("pending settings wait for the matching normalized settings snapshot", () => {
  const page = startPage();
  const socket = admitChat(page);
  const desired = {
    categories: ["animals", "food"], duration: 45, mode: "endless", rounds: null, community_word_approval: false,
  };
  const save = page.get("#settings-form").querySelector("button[type=submit]");
  setSettingsFields(page, desired);
  submitForm(page, "#settings-form");

  socket.receive(roomWithOpponent("lobby", 1, false));
  socket.receive(roomWithOpponent());
  assert.equal(save.disabled, true);
  assert.equal(page.get("#start-game").disabled, true);
  submitForm(page, "#settings-form");
  assert.deepEqual(socket.sent.slice(2), [{ type: "update_settings", settings: desired }]);

  const matching = roomWithOpponent();
  const { community_word_approval, ...snapshotSettings } = desired;
  matching.room.settings = snapshotSettings;
  socket.receive(matching);
  assert.equal(save.disabled, false);
  assert.equal(page.get("#start-game").disabled, false);
  assert.equal(page.get("#current-duration").textContent, "45 seconds");
});

test("round and review countdowns never increase on a delayed snapshot of the same timed phase", () => {
  const page = startPage();
  const socket = admitChat(page);
  const clock = page.get("#round-time");
  const active = roomWithOpponent("active");
  active.room.round.remaining_ms = 15_000;
  socket.receive(active);
  assert.equal(clock.textContent, "0:15");
  page.advance(8000);
  assert.equal(clock.textContent, "0:07");

  const staleActive = roomWithOpponent("active", 1, false);
  staleActive.room.round.remaining_ms = 12_000;
  socket.receive(staleActive);
  assert.equal(clock.textContent, "0:07");

  const review = roomWithOpponent("review");
  socket.receive(review);
  assert.equal(clock.textContent, "0:10", "review starts a separate countdown");
  page.advance(5000);
  const staleReview = roomWithOpponent("review", 1, false);
  staleReview.room.round.review.remaining_ms = 8000;
  socket.receive(staleReview);
  assert.equal(clock.textContent, "0:05");
  const newerReview = roomWithOpponent("review");
  newerReview.room.round.review.remaining_ms = 2000;
  socket.receive(newerReview);
  assert.equal(clock.textContent, "0:02", "a newer snapshot can still shorten the display");

  socket.receive(roomWithOpponent("results"));
  socket.receive(roomWithOpponent("active", 2));
  assert.equal(clock.textContent, "0:30", "the next round starts a fresh countdown");
});

test("first-time Create and Join show admission progress until their socket closes", () => {
  for (const [form, status, type] of [
    ["#create-form", /creat/i, "create"],
    ["#join-form", /join/i, "join"],
  ]) {
    const page = startPage(null);
    if (type === "create") setSettingsFields(page, lobbyState.room.settings, form);
    page.get("#join-code").value = savedSeat.code;
    submitForm(page, form);
    const indicator = page.get("#home-seat-status");
    assert.equal(indicator.hidden, false);
    assert.match(indicator.textContent, status);
    assert.equal(page.get("#retry-seat-home").hidden, true);

    const socket = page.sockets[0];
    socket.open();
    assert.equal(socket.sent[0].type, type);
    page.advance(8000);
    assert.equal(socket.readyState, 2);
    socket.finishClose();
    assert.equal(indicator.hidden, true);
    assert.equal(page.get("#home-error").hidden, false);
    assert.match(page.get("#home-error").textContent, /connect|timed out/i);
  }
});

test("a disconnected lobby shows recovery rather than the last connected readiness status", () => {
  const page = startPage();
  const socket = admitChat(page);
  assert.match(page.get("#room-status").textContent, /ready to start/i);
  socket.finishClose();
  assert.match(page.get("#lobby-connection").textContent, /reconnecting/i);
  assert.match(page.get("#room-status").textContent, /reconnect/i);
  assert.equal(page.get("#start-game").disabled, true);
  assert.deepEqual(page.currentSeat(), savedSeat);
});

test("terminal resume failures exit the stale room, expose the error, and permit a fresh Join", () => {
  for (const code of ["invalid_session", "room_not_found"]) {
    const page = startPage();
    const first = admitChat(page, [chatEntry(1, "Room history")]);
    first.receive(roomWithOpponent("active"));
    first.finishClose();
    page.advance(1200);
    const retry = page.sockets.at(-1);
    retry.open();
    retry.receive({ type: "error", code, message: "That room cannot be resumed." });
    retry.finishClose();

    assert.equal(page.get("#lobby").hidden, true);
    assert.equal(page.get("#home").hidden, false);
    assert.equal(page.get("#home-error").hidden, false);
    assert.match(page.get("#home-error").textContent, /room|session/i);
    assert.equal(page.get("#retry-seat-home").hidden, true);
    assert.equal(page.currentSeat(), null);
    assert.deepEqual(visibleMessages(page), []);

    page.get("#join-code").value = savedSeat.code;
    submitForm(page, "#join-form");
    const fresh = page.sockets.at(-1);
    fresh.open();
    assert.deepEqual(fresh.sent, [{ type: "join", code: savedSeat.code }]);
  }
});

test("chat only clears the draft after a new matching sender echo and sends once while pending", () => {
  const page = startPage();
  const author = savedSeat.player_id;
  const socket = admitChat(page, [chatEntry(5, "Hello", author)]);
  const input = page.get("#chat-input");
  const send = page.get("#chat-send");
  submitChat(page, "  Hello  ");
  assert.equal(input.value, "  Hello  ");
  assert.equal(send.disabled, true);
  assert.match(page.get("#chat-delivery").textContent, /sending/i);
  submitForm(page, "#chat-form");
  assert.deepEqual(socket.sent.slice(2), [{ type: "chat_send", text: "Hello" }]);

  socket.receive({ type: "chat_message", message: chatEntry(5, "Hello", author) });
  socket.receive({ type: "chat_message", message: chatEntry(6, "Hello") });
  assert.equal(input.value, "  Hello  ");
  assert.equal(send.disabled, true);
  socket.receive({ type: "chat_message", message: chatEntry(7, "Hello", author) });
  assert.equal(input.value, "");
  assert.equal(send.disabled, false);
  assert.deepEqual(visibleMessages(page), ["Hello", "Hello", "Hello"]);
});

test("chat rate rejection retains the message and pauses retry without auto-sending", () => {
  const page = startPage();
  const socket = admitChat(page);
  submitChat(page, "Keep after rejection");
  socket.receive({ type: "chat_error", code: "chat_rate_limited", message: "You are sending too quickly." });
  assert.equal(page.get("#chat-input").value, "Keep after rejection");
  assert.match(page.get("#chat-error").textContent, /too quickly/i);
  assert.equal(page.get("#chat-send").disabled, true);

  submitForm(page, "#chat-form");
  assert.equal(socket.sent.length, 3);
  page.advance(2000);
  assert.equal(page.get("#chat-send").disabled, false);
  assert.equal(socket.sent.length, 3);
  assert.match(page.get("#chat-error").textContent, /too quickly/i);
  submitForm(page, "#chat-form");
  assert.deepEqual(socket.sent.at(-1), { type: "chat_send", text: "Keep after rejection" });
});

test("chat confirmation wait ends with the unsent text intact and a manual retry path", () => {
  const page = startPage();
  const socket = admitChat(page);
  submitChat(page, "Still my draft");
  page.advance(5000);
  assert.equal(page.get("#chat-input").value, "Still my draft");
  assert.match(page.get("#chat-delivery").textContent, /unconfirm|not confirm|uncertain/i);
  assert.equal(page.get("#chat-send").disabled, false);
  assert.equal(socket.sent.length, 3);
});

test("a chat send interrupted by a disconnect retains the draft on reconnection", () => {
  const page = startPage();
  const first = admitChat(page);
  submitChat(page, "Interrupted send");
  first.finishClose();
  assert.equal(page.get("#chat-input").value, "Interrupted send");
  assert.equal(page.get("#chat-send").disabled, true);
  page.advance(1200);
  const retry = admitChat(page);
  assert.deepEqual(retry.sent, [resume, subscribe]);
  assert.equal(page.get("#chat-input").value, "Interrupted send");
  assert.match(page.get("#chat-delivery").textContent, /unconfirm|not confirm|uncertain/i);
  assert.equal(page.get("#chat-send").disabled, false);
});

function reviewSnapshot(...entries) {
  const state = roomWithOpponent("review");
  state.room.round.review.entries = entries;
  return state;
}

test("review focus moves from the disabled vote to the next available vote and then the heading", () => {
  const page = startPage();
  const socket = admitChat(page);
  const animals = { player_id: opponent.id, category: "animals", answer: "Audit animal", status: "pending" };
  const food = { player_id: opponent.id, category: "food", answer: "Audit food", status: "pending" };
  const own = { player_id: savedSeat.player_id, category: "animals", answer: "Own review", status: "pending" };
  socket.receive(reviewSnapshot(animals, food, own));
  const reviewList = page.get("#review-opponent");
  const acceptAnimals = reviewList.children[0].querySelector('[data-decision="accept"]');
  acceptAnimals.focus();
  acceptAnimals.emit("click");
  assert.ok(page.document.activeElement === page.document.body, "disabling a focused button blurs it");
  socket.receive(reviewSnapshot({ ...animals, status: "accepted" }, food, own));
  const next = reviewList.children[1].querySelector("button");
  assert.ok(page.document.activeElement === next, "next review vote should be focused");
  assert.equal(next.disabled, false);

  next.emit("click");
  socket.receive(reviewSnapshot({ ...animals, status: "accepted" }, { ...food, status: "accepted" }, own));
  assert.ok(page.document.activeElement === page.get("#review-opponent-title"), "review heading should be focused");
});

test("a rejected review vote returns keyboard focus to the re-enabled vote", () => {
  const page = startPage();
  const socket = admitChat(page);
  socket.receive(reviewSnapshot({
    player_id: opponent.id, category: "animals", answer: "Audit animal", status: "pending",
  }));
  const reject = page.get("#review-opponent").querySelector('[data-decision="reject"]');
  reject.focus();
  reject.emit("click");
  assert.ok(page.document.activeElement === page.document.body);
  socket.receive({ type: "error", code: "invalid_review", message: "Review remains open." });
  assert.equal(reject.disabled, false);
  assert.ok(page.document.activeElement === reject, "re-enabled review vote should be focused");
});


function resumeAfterClose(page, socket, state, messages = []) {
  if (socket.readyState !== socket.constructor.CLOSED) socket.finishClose();
  page.advance(1200);
  const resumed = page.sockets.at(-1);
  assert.ok(resumed !== socket, "the closed socket was replaced");
  resumed.open();
  assert.deepEqual(resumed.sent, [resume]);
  resumed.receive({ type: "session", ...savedSeat });
  resumed.receive(state);
  resumed.receive({ type: "chat_history", messages });
  assert.deepEqual(resumed.sent, [resume, subscribe]);
  return resumed;
}

test("a late rate-limit rejection cannot settle a newer pending chat", () => {
  const page = startPage();
  const socket = admitChat(page);
  const input = page.get("#chat-input");
  const send = page.get("#chat-send");
  submitChat(page, "First attempt");
  page.advance(5000);
  submitChat(page, "Second attempt");

  socket.receive({ type: "chat_error", code: "chat_rate_limited", message: "You are sending too quickly." });
  assert.equal(input.value, "Second attempt");
  assert.equal(input.readOnly, true);
  assert.match(page.get("#chat-delivery").textContent, /sending/i);
  assert.equal(send.disabled, true);
  socket.receive({ type: "chat_message", message: chatEntry(1, "Second attempt", savedSeat.player_id) });
  assert.equal(input.value, "");
  assert.equal(input.readOnly, false);
  assert.equal(send.disabled, true, "the rate-limit cooldown still applies");
  page.advance(2000);
  assert.equal(send.disabled, false);
  assert.deepEqual(socket.sent.slice(2), [
    { type: "chat_send", text: "First attempt" },
    { type: "chat_send", text: "Second attempt" },
  ]);
});

test("a rejection followed by an own echo confirms the identical retry in FIFO order", () => {
  const page = startPage();
  const socket = admitChat(page);
  const text = "Identical retry";
  submitChat(page, text);
  page.advance(5000);
  submitForm(page, "#chat-form");
  assert.deepEqual(socket.sent.slice(2), [
    { type: "chat_send", text },
    { type: "chat_send", text },
  ]);

  socket.receive({ type: "chat_error", code: "chat_rate_limited", message: "You are sending too quickly." });
  assert.equal(page.get("#chat-input").readOnly, true);
  assert.match(page.get("#chat-delivery").textContent, /sending/i);
  socket.receive({ type: "chat_message", message: chatEntry(1, text, savedSeat.player_id) });
  assert.equal(page.get("#chat-input").value, "");
  assert.equal(page.get("#chat-delivery").hidden, true);
  page.advance(5000);
  assert.equal(page.get("#chat-delivery").hidden, true, "a confirmed retry does not time out");
  assert.equal(page.get("#chat-send").disabled, false);
});

test("a sender echo after the chat confirmation timeout clears the unchanged draft", () => {
  const page = startPage();
  const socket = admitChat(page, [chatEntry(8, "Late confirmation", savedSeat.player_id)]);
  submitChat(page, "Late confirmation");
  page.advance(5000);
  assert.match(page.get("#chat-delivery").textContent, /unconfirm|uncertain/i);
  socket.receive({ type: "chat_message", message: chatEntry(8, "Late confirmation", savedSeat.player_id) });
  socket.receive({ type: "chat_message", message: chatEntry(9, "Late confirmation") });
  assert.equal(page.get("#chat-input").value, "Late confirmation");
  socket.receive({ type: "chat_message", message: chatEntry(10, "Late confirmation", savedSeat.player_id) });
  assert.equal(page.get("#chat-input").value, "");
  assert.equal(page.get("#chat-delivery").hidden, true);
  assert.equal(page.get("#chat-send").disabled, false);
  assert.equal(socket.sent.length, 3);
});

test("a late sender echo preserves edits made after the timeout even if the text matches again", () => {
  const text = "My original draft";
  for (const latestDraft of ["A newer draft", text]) {
    const page = startPage();
    const socket = admitChat(page);
    const input = page.get("#chat-input");
    submitChat(page, text);
    page.advance(5000);
    input.value = "A newer draft";
    input.emit("input");
    input.value = latestDraft;
    input.emit("input");
    socket.receive({ type: "chat_message", message: chatEntry(1, text, savedSeat.player_id) });
    assert.equal(input.value, latestDraft);
    assert.equal(page.get("#chat-delivery").hidden, true);
    assert.equal(socket.sent.length, 3);
  }
});

test("a late sender echo preserves a newer draft's validation feedback", () => {
  const page = startPage();
  const socket = admitChat(page);
  const input = page.get("#chat-input");
  const error = page.get("#chat-error");
  submitChat(page, "Original");
  page.advance(5000);

  const oversized = "x".repeat(501);
  input.value = oversized;
  input.emit("input");
  submitForm(page, "#chat-form");
  const validation = error.textContent;
  assert.match(validation, /500 UTF-8 bytes/);
  assert.equal(error.hidden, false);

  socket.receive({ type: "chat_message", message: chatEntry(1, "Original", savedSeat.player_id) });
  assert.equal(input.value, oversized);
  assert.equal(error.textContent, validation);
  assert.equal(error.hidden, false);
  assert.deepEqual(socket.sent.slice(2), [{ type: "chat_send", text: "Original" }]);
});

test("one late echo cannot settle an identical manual retry whose delivery is still uncertain", () => {
  const page = startPage();
  const socket = admitChat(page);
  submitChat(page, "Repeated text");
  page.advance(5000);
  submitForm(page, "#chat-form");
  socket.receive({ type: "chat_message", message: chatEntry(1, "Repeated text", savedSeat.player_id) });
  assert.equal(page.get("#chat-input").value, "Repeated text");
  assert.equal(page.get("#chat-send").disabled, true);
  assert.match(page.get("#chat-delivery").textContent, /sending/i);

  page.advance(5000);
  assert.match(page.get("#chat-delivery").textContent, /unconfirm|uncertain/i);
  assert.equal(page.get("#chat-input").value, "Repeated text");
  assert.equal(page.get("#chat-send").disabled, false);
  assert.equal(socket.sent.length, 4);
});

test("an echo already matched before reconnect cannot also confirm an identical retry", () => {
  const page = startPage();
  const socket = admitChat(page);
  const text = "The same text twice";
  submitChat(page, text);
  page.advance(5000);
  submitForm(page, "#chat-form");
  const accepted = chatEntry(1, text, savedSeat.player_id);
  socket.receive({ type: "chat_message", message: accepted });

  const resumed = resumeAfterClose(page, socket, roomWithOpponent(), [accepted]);
  assert.equal(page.get("#chat-input").value, text);
  assert.match(page.get("#chat-delivery").textContent, /unconfirm|uncertain/i);
  assert.equal(page.get("#chat-send").disabled, false);
  assert.deepEqual(resumed.sent, [resume, subscribe]);
});

test("only a newer matching own message in reconnect history confirms an interrupted chat", () => {
  const author = savedSeat.player_id;
  const text = "Held during disconnect";
  const old = chatEntry(8, text, author);
  const peer = chatEntry(9, text);

  for (const accepted of [false, true]) {
    const page = startPage();
    const socket = admitChat(page, [old]);
    submitChat(page, text);
    const history = [old, peer];
    if (accepted) history.push(chatEntry(10, text, author));
    const resumed = resumeAfterClose(page, socket, roomWithOpponent(), history);
    assert.equal(page.get("#chat-input").value, accepted ? "" : text);
    assert.equal(page.get("#chat-delivery").hidden, accepted);
    assert.equal(page.get("#chat-send").disabled, false);
    page.advance(5000);
    assert.deepEqual(resumed.sent, [resume, subscribe]);
  }
});

test("chat normalizes leading U+001F before submission so the server echo confirms it", () => {
  const page = startPage();
  const socket = admitChat(page);
  submitChat(page, "\u001FHi");
  assert.deepEqual(socket.sent.at(-1), { type: "chat_send", text: "Hi" });
  socket.receive({ type: "chat_message", message: chatEntry(1, "Hi", savedSeat.player_id) });
  assert.equal(page.get("#chat-input").value, "");
  assert.equal(page.get("#chat-delivery").hidden, true);
});

test("review focus resumes on the same round with the next vote or the unapplied vote", () => {
  const animals = { player_id: opponent.id, category: "animals", answer: "First candidate", status: "pending" };
  const food = { player_id: opponent.id, category: "food", answer: "Second candidate", status: "pending" };
  for (const accepted of [false, true]) {
    const page = startPage();
    const socket = admitChat(page);
    socket.receive(reviewSnapshot(animals, food));
    const reviewList = page.get("#review-opponent");
    const firstVote = reviewList.children[0].querySelector("button");
    firstVote.focus();
    firstVote.emit("click");
    assert.ok(page.document.activeElement === page.document.body, "the disabled choice blurred");

    const firstEntry = accepted ? { ...animals, status: "accepted" } : animals;
    const resumed = resumeAfterClose(page, socket, reviewSnapshot(firstEntry, food));
    const next = reviewList.children[accepted ? 1 : 0].querySelector("button");
    assert.equal(next.disabled, false);
    assert.ok(page.document.activeElement === next, "the correct resumed review vote is focused");
    next.emit("click");
    assert.equal(resumed.sent.at(-1).type, "review_answer");
    assert.equal(resumed.sent.at(-1).category, accepted ? "food" : "animals");
  }
});

test("a review vote cannot restore stale focus in a later round or after the review ends", () => {
  const animals = { player_id: opponent.id, category: "animals", answer: "Candidate", status: "pending" };
  for (const nextPhase of ["review", "results"]) {
    const page = startPage();
    const socket = admitChat(page);
    socket.receive(reviewSnapshot(animals));
    const button = page.get("#review-opponent").querySelector("button");
    button.focus();
    button.emit("click");

    const nextState = nextPhase === "review" ? reviewSnapshot(animals) : roomWithOpponent("results");
    if (nextPhase === "review") nextState.room.round.number = 2;
    resumeAfterClose(page, socket, nextState);
    const expectedFocus = nextPhase === "results" ? page.get("#lobby-title") : page.document.body;
    assert.ok(page.document.activeElement === expectedFocus, "focus did not move to a stale vote");
  }
});

test("a new room cannot inherit unfinished review focus from a room the player left", () => {
  const animals = { player_id: opponent.id, category: "animals", answer: "Candidate", status: "pending" };
  const page = startPage();
  const socket = admitChat(page);
  socket.receive(reviewSnapshot(animals));
  const reviewList = page.get("#review-opponent");
  const button = reviewList.querySelector("button");
  button.focus();
  button.emit("click");
  socket.finishClose();
  page.get("#return-home").emit("click");

  const newCode = "DEF234";
  page.get("#join-code").value = newCode;
  submitForm(page, "#join-form");
  const fresh = page.sockets.at(-1);
  fresh.open();
  assert.deepEqual(fresh.sent, [{ type: "join", code: newCode }]);
  fresh.receive({ type: "session", ...savedSeat, code: newCode });
  const state = roomWithOpponent();
  state.room.code = newCode;
  fresh.receive(state);
  page.document.body.focus();
  const newReview = reviewSnapshot(animals);
  newReview.room.code = newCode;
  fresh.receive(newReview);
  assert.ok(page.document.activeElement === page.get("#lobby-title"), "the new review focuses its own title");
  assert.ok(page.document.activeElement !== reviewList.querySelector("button"), "the previous vote is not focused");
  assert.deepEqual(fresh.sent, [{ type: "join", code: newCode }, subscribe]);
});

test("host Start settles from the resumed phase and an unapplied Start requires a new click", () => {
  for (const applied of [false, true]) {
    const page = startPage();
    const socket = admitChat(page);
    const start = page.get("#start-game");
    start.emit("click");
    assert.equal(start.disabled, true);
    assert.deepEqual(socket.sent.slice(2), [{ type: "start" }]);

    const resumed = resumeAfterClose(page, socket, roomWithOpponent(applied ? "active" : "lobby"));
    page.advance(8000);
    assert.deepEqual(resumed.sent, [resume, subscribe]);
    if (applied) {
      assert.equal(start.hidden, true);
      assert.equal(page.get("#done-round").disabled, false);
    } else {
      assert.equal(start.hidden, false);
      assert.equal(start.disabled, false);
      start.emit("click");
      assert.deepEqual(resumed.sent, [resume, subscribe, { type: "start" }]);
      resumed.receive(roomWithOpponent("active"));
      assert.equal(start.hidden, true);
      assert.equal(page.get("#done-round").disabled, false);
    }
  }
});

test("host Save settles from resumed settings and only a manual submit retries an unapplied Save", () => {
  const requested = { categories: ["animals", "food"], duration: 45, mode: "endless", rounds: null,
    community_word_approval: false };
  for (const applied of [false, true]) {
    const page = startPage();
    const socket = admitChat(page);
    const form = page.get("#settings-form");
    const save = form.querySelector("button[type=submit]");
    setSettingsFields(page, requested);
    submitForm(page, "#settings-form");
    assert.equal(save.disabled, true);
    assert.deepEqual(socket.sent.slice(2), [{ type: "update_settings", settings: requested }]);

    const { community_word_approval, ...normalized } = requested;
    const confirmed = roomWithOpponent();
    confirmed.room.settings = normalized;
    const resumed = resumeAfterClose(page, socket, applied ? confirmed : roomWithOpponent());
    page.advance(8000);
    assert.deepEqual(resumed.sent, [resume, subscribe]);
    assert.equal(save.disabled, false);
    assert.equal(page.get("#current-duration").textContent, applied ? "45 seconds" : "60 seconds");
    assert.equal(Number(form.elements.duration.value), requested.duration, "the visible duration survives reconnect");
    assert.equal(form.elements.mode.value, requested.mode);
    assert.equal(form.elements.rounds.disabled, true);
    assert.equal(form.elements.community_word_approval.checked, requested.community_word_approval);
    assert.deepEqual(form.querySelectorAll("input[name=category]").filter((field) => field.checked)
      .map((field) => field.value), requested.categories);
    if (!applied) {
      submitForm(page, "#settings-form");
      assert.deepEqual(resumed.sent, [resume, subscribe, { type: "update_settings", settings: requested }]);
      resumed.receive(confirmed);
      assert.equal(save.disabled, false);
      assert.equal(page.get("#current-duration").textContent, "45 seconds");
    }
  }
});



test("resumed review focus respects a deliberate move outside the pending vote", () => {
  const page = startPage();
  const socket = admitChat(page);
  const animals = { player_id: opponent.id, category: "animals", answer: "First candidate", status: "pending" };
  const food = { player_id: opponent.id, category: "food", answer: "Second candidate", status: "pending" };
  socket.receive(reviewSnapshot(animals, food));
  const button = page.get("#review-opponent").querySelector("button");
  button.focus();
  button.emit("click");
  const roomCode = page.get("#room-code");
  roomCode.focus();

  resumeAfterClose(page, socket, reviewSnapshot({ ...animals, status: "accepted" }, food));
  assert.ok(page.document.activeElement === roomCode, "resume keeps deliberate keyboard focus");
});

test("resume leaves review focus alone after the focused chat input is disabled by a disconnect", () => {
  const page = startPage();
  const socket = admitChat(page);
  const animals = { player_id: opponent.id, category: "animals", answer: "First candidate", status: "pending" };
  const food = { player_id: opponent.id, category: "food", answer: "Second candidate", status: "pending" };
  socket.receive(reviewSnapshot(animals, food));
  const button = page.get("#review-opponent").querySelector("button");
  button.focus();
  button.emit("click");
  const input = page.get("#chat-input");
  input.focus();
  assert.equal(page.document.activeElement, input);

  socket.finishClose();
  assert.equal(input.disabled, true);
  assert.equal(page.document.activeElement, page.document.body, "the disabled input blurred");
  resumeAfterClose(page, socket, reviewSnapshot({ ...animals, status: "accepted" }, food));
  assert.equal(input.disabled, false);
  assert.equal(page.document.activeElement, page.document.body, "resume does not focus another vote");
});
