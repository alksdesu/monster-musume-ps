import { overview } from "./views/overview.js";
import { translation } from "./views/translation.js";
import { shortcuts } from "./views/shortcuts.js";
import { roster } from "./views/roster.js";
import { content } from "./views/content.js";
import { schedule } from "./views/schedule.js";
import { saves } from "./views/saves.js";
import { player } from "./views/player.js";
import { configs, shared } from "./views/files.js";
import { captures } from "./views/captures.js";
import { logs } from "./views/logs.js";
import { masters } from "./views/masters.js";

const API = "api/";
const STATUS_EVERY = 5000;
const VIEWS = { overview, translation, shortcuts, roster, player, content, schedule, saves, configs, shared, captures, logs, masters };
const HOME = "overview";

function kept(children) {
  return children.flat(Infinity).filter((child) => child != null && child !== false);
}

export function fill(node, ...children) {
  node.replaceChildren(...kept(children));
  return node;
}

export function h(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value == null || value === false) continue;
    if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value === true ? "" : value);
  }
  node.append(...kept(children));
  return node;
}

const PROBLEMS = {
  offline: "游戏服务器没有运行。点右上角的「启动服务器」。",
  settings: "读不了 server/data/settings.json，所以不知道游戏服务器该在哪个地址上运行。",
  start_failed: "游戏服务器没能启动，原因见日志。",
  start_timeout: "游戏服务器启动得比预期久，还没有应答。过一会儿再看状态。",
  stop_timeout: "游戏服务器没有按时停下。它不是由这个程序启动的，要到启动它的那个窗口里结束。",
  launch_failed: "游戏没能启动，原因见「概览」里的输出。",
  launch_timeout: "启动游戏的脚本迟迟没有结束，已经中止。",
  close_timeout: "游戏没有关掉，需要手动结束它。",
};

export function problemText(body, status) {
  return PROBLEMS[body.code] ?? body.error ?? `服务器返回 ${status}`;
}

export class ApiError extends Error {
  constructor(status, body) {
    super(problemText(body, status));
    this.status = status;
    this.body = body;
  }
}

async function answer(response) {
  const text = await response.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    body = { error: text };
  }
  if (!response.ok) throw new ApiError(response.status, body);
  return body;
}

export const api = {
  async get(path, query = {}) {
    const search = new URLSearchParams(query).toString();
    return answer(await fetch(`${API}${path}${search ? `?${search}` : ""}`));
  },
  async post(path, body) {
    return answer(
      await fetch(`${API}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    );
  },
};

export function when(seconds) {
  return new Date(seconds * 1000).toLocaleString("zh-CN", { hour12: false });
}

export function receipt(text, kind = "done") {
  const bar = document.getElementById("receipt");
  bar.textContent = text;
  bar.dataset.kind = kind;
  bar.hidden = !text;
}

// Runs an action and turns whatever it throws into a line the operator can act on.
export async function attempt(action, done) {
  try {
    const result = await action();
    if (done) receipt(typeof done === "function" ? done(result) : done);
    return result;
  } catch (error) {
    receipt(error.message, "error");
    return undefined;
  }
}

export function table(columns, rows, empty, open) {
  if (!rows.length) return h("p", { class: "empty" }, empty);
  const aligned = (column) => (column.number ? "number" : null);
  const head = h("tr", {}, columns.map((column) => h("th", { scope: "col", class: aligned(column) }, column.title)));
  const body = rows.map((row) =>
    h(
      "tr",
      {
        "data-moved": row.moved === true,
        "data-marked": row.marked === true,
        "data-open": Boolean(open),
        onclick: open && (() => open(row)),
      },
      columns.map((column) => h("td", { class: column.title ? aligned(column) : "actions" }, column.cell(row))),
    ),
  );
  return h("div", { class: "scroll" }, h("table", {}, h("thead", {}, head), h("tbody", {}, body)));
}

export function field(title, control) {
  return h("label", {}, title, control);
}

export function button(title, onclick, weight) {
  return h("button", { type: "button", onclick, "data-weight": weight }, title);
}

// What the operator wrote is set apart from what the game ships.
export function pen(text) {
  return h("span", { class: "pen" }, text);
}

export function struck(text) {
  return h("s", { class: "struck" }, text);
}

export function outcomeText(outcome) {
  const parts = [outcome.synced ? "改动会随该玩家的下一次请求同步到客户端。" : ""];
  if (outcome.dropped) parts.push("有数据被删除，客户端要回标题重进才会消失。");
  if (outcome.unsynced?.length) parts.push(`${outcome.unsynced.join("、")} 不走同步，客户端下次读取时才会看到。`);
  return parts.filter(Boolean).join("") || "已保存。";
}

export const login = { name: "local", shippedDates: false };

export async function order(target, body, doing, done) {
  receipt(doing);
  const finished = api.post(`app/${target}`, body);
  refreshStatus();
  try {
    await finished;
    receipt(done);
  } catch (error) {
    receipt(error.message, "error");
  }
  await refreshStatus();
}

export function startServer() {
  return order("server", { action: "start" }, "正在启动游戏服务器，通常要几秒。", "游戏服务器已启动。");
}

export function startGame() {
  const body = { action: "start", viewer: login.name, shipped_dates: login.shippedDates };
  return order("client", body, "正在启动游戏。服务器没开的话会先开。", "游戏已启动。");
}

const stampNode = document.getElementById("stamp");
const line = document.getElementById("bar-line");
const actions = document.getElementById("bar-actions");
let stamped = "";

function stamp(state, text) {
  if (stamped === state) return;
  stamped = state;
  stampNode.dataset.state = state;
  stampNode.textContent = text;
  stampNode.removeAttribute("data-pressed");
  requestAnimationFrame(() => stampNode.setAttribute("data-pressed", ""));
}

const STAMPS = { running: "运行中", paused: "已暂停", stopped: "未开服", starting: "启动中", stopping: "停止中" };
const LINES = { stopped: "游戏服务器没有运行", starting: "游戏服务器正在启动", stopping: "游戏服务器正在停止" };
const GAME = { running: "游戏开着", starting: "游戏正在启动", stopping: "游戏正在关闭", stopped: "游戏没开" };
const watchers = new Set();

export function watchStatus(callback) {
  watchers.add(callback);
  return () => watchers.delete(callback);
}

export async function refreshStatus() {
  let app;
  try {
    app = await api.get("app");
  } catch {
    stamp("lost", "未连接");
    line.textContent = "这个程序自己的后台没有应答，关掉重新打开它。";
    fill(actions);
    return null;
  }
  const { state, status } = app.server;
  const shown = status?.paused ? "paused" : state;
  stamp(shown, STAMPS[shown]);
  const serving = status
    ? `已接入 ${status.endpoints} / ${status.contracts} 个端点，${status.online.length} 个存档在线`
    : LINES[state];
  line.textContent = `${serving}；${GAME[app.client.state]}`;
  fill(
    actions,
    state === "stopped" && button("启动服务器", startServer),
    app.client.state === "stopped" && button("开始游戏", startGame, "main"),
  );
  for (const watcher of watchers) watcher(app);
  return app;
}

const rail = document.getElementById("rail");
const view = document.getElementById("view");
let leave = null;

// The rail's margin carries how much the operator has changed on each page.
const MARKS = {
  content: {
    ink: "pen",
    unit: "处内容改动",
    count: async () => (await api.get("content/changes")).changes.length,
  },
  schedule: {
    ink: "pencil",
    unit: "条排期",
    count: async () => {
      const { document: plan } = await api.get("schedule");
      return (plan.activities?.length ?? 0) + (plan.rows?.length ?? 0);
    },
  },
};

export async function refreshMarks() {
  for (const [name, mark] of Object.entries(MARKS)) {
    const slot = rail.querySelector(`[data-view="${name}"] span`);
    const total = await mark.count().catch(() => 0);
    slot.textContent = total || "";
    if (total) slot.setAttribute("aria-label", `${total} ${mark.unit}`);
    else slot.removeAttribute("aria-label");
  }
}

function route() {
  const [name, ...rest] = decodeURIComponent(location.hash.slice(1)).split("/");
  return VIEWS[name] ? { name, argument: rest.join("/") } : { name: HOME, argument: "" };
}

async function show() {
  const { name, argument } = route();
  leave?.();
  receipt("");
  for (const link of rail.children) {
    if (link.dataset.view === name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  fill(view);
  leave = (await attempt(() => VIEWS[name].mount(view, argument))) ?? null;
  refreshMarks();
}

for (const [name, entry] of Object.entries(VIEWS)) {
  rail.append(h("a", { href: `#${name}`, "data-view": name }, entry.title, MARKS[name] && h("span", { class: MARKS[name].ink })));
}
window.addEventListener("hashchange", show);
refreshStatus();
setInterval(refreshStatus, STATUS_EVERY);
show();
