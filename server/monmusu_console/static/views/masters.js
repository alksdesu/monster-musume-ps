import { api, attempt, button, h, receipt, when } from "../app.js";

const POLL_EVERY = 2000;

export const masters = {
  title: "主数据",
  async mount(root) {
    const output = h("pre", { "aria-label": "更新输出" });
    let timer = null;

    function show(progress) {
      output.textContent = progress.lines.join("\n") || "还没有运行过更新。";
      output.scrollTop = output.scrollHeight;
      if (!progress.running && progress.code != null) {
        receipt(progress.code === 0 ? "更新完成，新表在下一次请求时生效。" : `更新工具以代码 ${progress.code} 结束，见下面的输出。`, progress.code === 0 ? "done" : "error");
      }
    }

    function follow() {
      clearInterval(timer);
      timer = setInterval(async () => {
        const reply = await api.get("masters").catch(() => null);
        if (!reply) return;
        show(reply.progress);
        if (!reply.progress.running) clearInterval(timer);
      }, POLL_EVERY);
    }

    async function update() {
      if (!confirm("更新会连接官方的版本接口和资产服务器，下载并覆盖 masterdata 目录。继续吗？")) return;
      const progress = await attempt(() => api.post("masters/update", {}), "更新已开始。");
      if (progress) follow();
    }

    const reply = await api.get("masters");
    const { summary } = reply;
    root.append(
      h("h2", {}, "主数据"),
      h(
        "dl",
        {},
        h("dt", {}, "已导出的文件"),
        h("dd", {}, `${summary.files} 个`),
        h("dt", {}, "各目录"),
        h("dd", {}, Object.entries(summary.folders).map(([name, count]) => h("div", {}, `${name}：${count}`))),
        h("dt", {}, "上次下载"),
        h("dd", {}, summary.fetched_at == null ? "还没有下载过" : when(summary.fetched_at)),
      ),
      h("p", {}, `更新由 ${summary.tool} 完成。表文件一旦变化，服务器会在下一次请求时重新读取并重建索引，排期也会按新表重新展开。`),
      h("div", { class: "row" }, button("从官方服务器更新主数据", update)),
      output,
    );
    show(reply.progress);
    if (reply.progress.running) follow();
    return () => clearInterval(timer);
  },
};
