// AURORA phone app. Talks only to the AURORA PC that served it, with this phone's key.
// Everything shown is inserted as text (never as HTML).
"use strict";

const KEY = "aurora-device-key";
const $ = (id) => document.getElementById(id);
let token = null;
try { token = localStorage.getItem(KEY); } catch (e) { token = null; }

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = "Bearer " + token;
  const response = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined, cache: "no-store" });
  let data = {};
  try { data = await response.json(); } catch (e) { data = {}; }
  if (response.status === 401) { forget(); throw new Error(data.error || "Not paired."); }
  if (!response.ok) throw new Error(data.error || "The PC didn't answer.");
  return data;
}

function forget() {
  token = null;
  try { localStorage.removeItem(KEY); } catch (e) { /* private mode */ }
  show();
}

function show() {
  $("pair").hidden = !!token;
  $("app").hidden = !token;
  if (token) { loadHistory(); refreshStatus(); }
  else { $("state").textContent = "not paired"; $("dot").className = "dot off"; }
}

// --- Pairing -----------------------------------------------------------------------------
$("pair-button").addEventListener("click", async () => {
  $("pair-error").textContent = "";
  $("pair-button").disabled = true;
  try {
    const data = await api("POST", "/api/pair", { code: $("code").value, name: $("name").value || "My phone" });
    token = data.token;
    try { localStorage.setItem(KEY, token); } catch (e) { /* kept for this visit only */ }
    show();
  } catch (error) {
    $("pair-error").textContent = error.message;
  } finally {
    $("pair-button").disabled = false;
  }
});

// --- Tabs ----------------------------------------------------------------------------------
document.querySelectorAll("nav button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b === button));
  document.querySelectorAll(".tab").forEach((tab) => { tab.hidden = tab.id !== "tab-" + button.dataset.tab; });
  ({ today: loadToday, memories: loadMemories, status: refreshStatus })[button.dataset.tab]?.();
}));

// --- Chat ----------------------------------------------------------------------------------
function bubble(role, text, sources) {
  const div = document.createElement("div");
  div.className = "bubble " + role;
  div.textContent = text;
  if (sources) {
    const small = document.createElement("span");
    small.className = "sources";
    small.textContent = sources;
    div.appendChild(small);
  }
  $("messages").appendChild(div);
  $("messages").scrollTop = $("messages").scrollHeight;
  return div;
}

async function loadHistory() {
  try {
    const data = await api("GET", "/api/history");
    $("messages").replaceChildren();
    if (!data.messages.length) bubble("assistant", "Hello. Ask me anything. This is the same conversation as on your PC.");
    data.messages.forEach((m) => bubble(m.role, m.text));
  } catch (error) { bubble("assistant waiting", error.message); }
}

async function send(text) {
  text = text.trim();
  if (!text) return;
  bubble("user", text);
  $("text").value = "";
  $("send").disabled = true;
  $("confirm").hidden = true;
  const waiting = bubble("assistant waiting", "AURORA is thinking…");
  try {
    const data = await api("POST", "/api/chat", { text });
    waiting.remove();
    bubble("assistant", data.reply, data.sources);
    $("confirm").hidden = !data.needs_confirmation;
  } catch (error) {
    waiting.textContent = error.message;
  } finally {
    $("send").disabled = false;
  }
}

$("send-form").addEventListener("submit", (event) => { event.preventDefault(); send($("text").value); });
$("yes").addEventListener("click", () => send("yes"));
$("no").addEventListener("click", () => send("no"));

// --- Today, memories, PC ------------------------------------------------------------------------
async function loadToday() {
  try {
    const data = await api("GET", "/api/today");
    $("briefing").textContent = data.briefing;
    $("reminders").replaceChildren();
    if (!data.reminders.length) { const li = document.createElement("li"); li.textContent = "No reminders."; $("reminders").appendChild(li); }
    data.reminders.forEach((r) => {
      const li = document.createElement("li");
      li.textContent = r.text;
      const when = document.createElement("small");
      when.textContent = new Date(r.due).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
      li.appendChild(when);
      $("reminders").appendChild(li);
    });
  } catch (error) { $("briefing").textContent = error.message; }
}

async function loadMemories() {
  $("memories").replaceChildren();
  try {
    const data = await api("GET", "/api/memories");
    $("memories-note").textContent = data.memories.length + " saved. Edit or delete them in the Memory Centre on the PC.";
    data.memories.forEach((m) => {
      const li = document.createElement("li");
      li.textContent = m.value;
      const kind = document.createElement("small");
      kind.textContent = m.category;
      li.appendChild(kind);
      $("memories").appendChild(li);
    });
  } catch (error) { $("memories-note").textContent = error.message; }
}

const STATES = { idle: ["Ready", "ok"], thinking: ["Thinking", "busy"], responding: ["Responding", "busy"],
  speaking: ["Speaking", "busy"], listening: ["Listening", "busy"], processing: ["Starting", "busy"],
  error: ["Something went wrong", "error"], offline: ["Paused", "off"] };

async function refreshStatus() {
  try {
    const s = await api("GET", "/api/status");
    const [word, dot] = STATES[s.ai] || [s.ai, "ok"];
    $("state").textContent = word + " · " + s.model;
    $("dot").className = "dot " + dot;
    const rows = [["AURORA", word], ["Model", s.model], ["CPU", s.cpu == null ? "—" : Math.round(s.cpu) + "%"],
      ["RAM", s.ram == null ? "—" : Math.round(s.ram) + "%"], ["Version", s.version]];
    $("status").replaceChildren();
    rows.forEach(([name, value]) => {
      const dt = document.createElement("dt"); dt.textContent = name;
      const dd = document.createElement("dd"); dd.textContent = value;
      $("status").append(dt, dd);
    });
  } catch (error) {
    $("state").textContent = "PC not reachable";
    $("dot").className = "dot error";
  }
}

$("refresh-today").addEventListener("click", loadToday);
$("refresh-status").addEventListener("click", refreshStatus);
$("unpair").addEventListener("click", async () => {
  if (!confirm("Disconnect this phone from AURORA?")) return;
  try { await api("POST", "/api/unpair"); } catch (e) { /* already gone */ }
  forget();
});

setInterval(() => { if (token && !document.hidden) refreshStatus(); }, 15000);
if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
show();
