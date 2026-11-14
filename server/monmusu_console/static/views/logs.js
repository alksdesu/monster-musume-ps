import { api, attempt, button, field, fill, h, table, when } from "../app.js";

const POLL_EVERY = 2000;
const LEVELS = ["全部", "WARNING", "ERROR"];
const RANK = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 };

export const logs = {
  title: "日志",
  async mount(root) {
    const live = h("pre", { "aria-label": "服务器日志" });
    const tail = h("pre", { "aria-label": "日志文件末尾" });
    const level = h("select", {}, LEVELS.map((name) => h("option", {}, name)));
    const follow = h("input", { type: "checkbox", checked: true });
    let rows = [];
    let last = 0;
    let offline = false;

    function draw() {
      const floor = RANK[level.value] ?? 0;
      const shown = rows.filter((row) => RANK[row.level] >= floor);
      fill(live, ...shown.map((row) =>
          h("div", { class: "log-line", "data-level": row.level }, `${when(row.time)} ${row.level} ${row.logger} ${row.message}`),
        ),
      );
      if (offline) live.textContent = "游戏服务器没有运行，这里只在它运行时显示。下面的日志文件随时可以看。";
      else if (!shown.length) live.textContent = "这次启动以来还没有符合条件的日志。";
      if (follow.checked) live.scrollTop = live.scrollHeight;
    }

    async function poll() {
      let reply;
      try {
        reply = await api.get("logs", { after: last });
      } catch {
        if (!offline) ((offline = true), (rows = []), draw());
        return;
      }
      // A restarted server counts its log lines from one again.
      if (offline || reply.latest < last) {
        offline = false;
        rows = [];
        last = 0;
        reply = await api.get("logs", { after: 0 });
      }
      if (reply.rows.length) last = reply.rows.at(-1).seq;
      rows = rows.concat(reply.rows);
      draw();
    }

    async function open(name) {
      const file = await attempt(() => api.get("logs/file", { name }));
      if (file) tail.textContent = file.text || "这个文件是空的。";
    }

    level.addEventListener("change", draw);
    const files = (await api.get("logs/files")).files;
    root.append(
      h("h2", {}, "服务器日志"),
      h("div", { class: "row" }, field("只看", level), h("label", { class: "inline" }, follow, "有新日志时滚到底部")),
      live,
      h("h2", {}, "日志文件"),
      table(
        [
          { title: "文件", cell: (file) => file.name },
          { title: "大小", cell: (file) => `${Math.ceil(file.size / 1024)} KB` },
          { title: "最后写入", cell: (file) => when(file.modified) },
          { title: "", cell: (file) => button("看末尾", () => open(file.name)) },
        ],
        files,
        "logs 目录里还没有日志文件。客户端日志由 start-client.ps1 写到这里。",
      ),
      tail,
    );
    tail.textContent = "选一个文件查看它最后 64 KB 的内容。";
    draw();
    await poll();
    const timer = setInterval(poll, POLL_EVERY);
    return () => clearInterval(timer);
  },
};
