import { api, attempt, button, field, fill, h, login, order, problemText, refreshStatus, startGame, startServer, watchStatus, when } from "../app.js";

const SOURCES = { features: "功能模块", settings: "settings.json", schedule: "排期", overrides: "内容覆盖" };
const SERVER = { running: "正常应答", stopped: "没有运行", starting: "正在启动", stopping: "正在停止" };
const CLIENT = { running: "正在运行", stopped: "没有运行", starting: "正在启动，第一次要先构建接入插件", stopping: "正在关闭" };
const LOGIN_PREFIX = "dmm:";

function facts(rows) {
  return h("dl", {}, rows.map(([name, value]) => [h("dt", {}, name), h("dd", {}, value)]));
}

function failure(failed, label) {
  return [failed && h("p", { class: "problem" }, problemText(failed)), failed?.log && h("pre", { "aria-label": label }, failed.log)];
}

function problems(status) {
  const rows = Object.entries(status.problems).filter(([, text]) => text);
  if (!rows.length) return h("p", {}, "功能模块、设置、排期和内容覆盖都已按最新内容生效。");
  return h(
    "div",
    {},
    h("p", { class: "problem" }, "下面的内容没能生效，服务器仍在使用上一份可用的版本。"),
    h("dl", {}, rows.map(([name, text]) => [h("dt", {}, SOURCES[name]), h("dd", { class: "problem" }, text)])),
  );
}

export const overview = {
  title: "概览",
  async mount(root) {
    const serverBox = h("section", {});
    const clientBox = h("section", {});
    const clientActions = h("div", { class: "row" });
    const logins = h("datalist", { id: "logins" });
    const viewer = h("input", { type: "text", list: "logins", value: login.name, size: 20 });
    const shipped = h("input", { type: "checkbox", checked: login.shippedDates });
    // The button in the top bar starts the game with whatever was last chosen here.
    viewer.addEventListener("input", () => (login.name = viewer.value.trim()));
    shipped.addEventListener("change", () => (login.shippedDates = shipped.checked));

    const server = (action, doing, done) => order("server", { action }, doing, done);
    const client = (action, doing, done) =>
      order("client", { action, viewer: login.name, shipped_dates: login.shippedDates }, doing, done);

    async function pause(paused) {
      const done = paused ? "游戏服务已暂停，控制台照常可用。" : "游戏服务已恢复。";
      if (await attempt(() => api.post("service", { action: paused ? "pause" : "resume" }), done)) await refreshStatus();
    }

    function drawServer({ state, status, address, owned, pid, failure: failed }) {
      const running = state === "running";
      const rows = [
        ["状态", status?.paused ? "已暂停，客户端的请求会收到 503" : SERVER[state]],
        ["地址", address ?? "未知"],
      ];
      if (status) {
        rows.push(
          ["进程", owned ? `由这个程序启动，进程号 ${pid}` : "由别处启动"],
          ["本次启动于", when(status.started_at)],
          ["服务器时间", when(status.now)],
          ["游戏端点", `${status.endpoints} / ${status.contracts}`],
          ["存档结构版本", String(status.schema_version)],
          ["在线存档", status.online.length ? status.online.join("、") : "没有客户端在线"],
        );
      }
      fill(serverBox, h("h2", {}, "游戏服务器"),
        facts(rows),
        h(
          "div",
          { class: "row" },
          state === "stopped" && button("启动服务器", startServer, "main"),
          running && (status.paused ? button("恢复服务", () => pause(false), "main") : button("暂停服务", () => pause(true))),
          running &&
            button("重启服务器", () => {
              if (confirm("重启会断开在线的客户端，它们需要回标题重新登录。继续吗？")) server("restart", "正在重启游戏服务器。", "游戏服务器已重新启动。");
            }),
          running &&
            button(
              "停止服务器",
              () => {
                if (confirm("停止后客户端会连不上，直到再次启动。继续吗？")) server("stop", "正在停止游戏服务器。", "游戏服务器已停止。");
              },
              "danger",
            ),
        ),
        failure(failed, "服务器日志末尾"),
        running && h("h3", {}, "加载情况"),
        running && problems(status),
      );
    }

    function drawClient({ state, pids, failure: failed }) {
      fill(clientBox, h("h2", {}, "游戏"),
        facts([["状态", state === "running" ? `${CLIENT.running}，进程号 ${pids.join("、")}` : CLIENT[state]]]),
        failure(failed, "启动脚本的输出"),
      );
      fill(clientActions, state === "stopped" && button("开始游戏", startGame, "main"),
        state === "running" && button("重启游戏", () => client("restart", "正在重启游戏。", "游戏已重新启动。")),
        state === "running" &&
          button(
            "关闭游戏",
            () => {
              if (confirm("关闭游戏窗口？没保存到服务器的进度（比如打到一半的战斗）会丢。")) client("stop", "正在关闭游戏。", "游戏已关闭。");
            },
            "danger",
          ),
      );
    }

    function draw(app) {
      drawServer(app.server);
      drawClient(app.client);
    }

    root.append(
      clientBox,
      h("div", { class: "row" }, field("登录身份", viewer), h("label", { class: "inline" }, shipped, "用原始日期启动，不套用排期"), logins),
      clientActions,
      h("p", { class: "note" }, "内容和排期的改动要重启游戏才会生效。关掉这个窗口不会停止服务器和游戏，下次打开时会接上它们。"),
      serverBox,
    );
    const unwatch = watchStatus(draw);
    draw(await api.get("app"));
    // The saves tell which logins exist, and only a running server can list them.
    api
      .get("saves")
      .then(({ saves }) =>
        fill(logins, ...saves.filter((row) => row.profile.startsWith(LOGIN_PREFIX)).map((row) => h("option", { value: row.profile.slice(LOGIN_PREFIX.length) }, row.nickname)),
        ),
      )
      .catch(() => {});
    return unwatch;
  },
};
