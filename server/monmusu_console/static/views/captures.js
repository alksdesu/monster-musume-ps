import { api, attempt, button, field, fill, h, receipt, table } from "../app.js";

const KINDS = { missing: "没有这一项", NoneType: "null", str: "文本", number: "数字", bool: "布尔", dict: "对象", list: "数组" };

function pretty(value) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function named(pick) {
  return pick ? `${pick.name} 第 ${pick.index + 1} 条 ${pick.endpoint}` : "还没选";
}

export const captures = {
  title: "抓包",
  async mount(root, argument) {
    const sessions = (await api.get("captures")).sessions;
    if (!sessions.length) {
      root.append(h("h2", {}, "抓包"), h("p", { class: "empty" }, "captures 目录里还没有抓包文件。它们由 observer 插件在客户端运行时写入。"));
      return;
    }
    const name = sessions.some((row) => row.name === argument) ? argument : sessions[0].name;
    const chooser = h("select", {}, sessions.map((row) => h("option", { selected: row.name === name }, row.name)));
    chooser.addEventListener("change", () => (location.hash = `captures/${chooser.value}`));
    const filter = h("input", { type: "search", size: 28, placeholder: "按端点筛选" });
    const list = h("div", {});
    const detail = h("div", {});
    const compared = h("div", {});
    const sides = h("p", {});
    const picks = (captures.picks ??= { left: null, right: null });
    const entries = (await api.get("capture", { name })).entries;

    function drawSides() {
      sides.textContent = `左边：${named(picks.left)}。右边：${named(picks.right)}。`;
    }

    function pick(which, entry) {
      picks[which] = { name, index: entry.index, endpoint: entry.endpoint };
      drawSides();
    }

    async function open(entry) {
      const loaded = await attempt(() => api.get("capture/entry", { name, index: entry.index }));
      if (!loaded) return;
      fill(detail, h("h3", {}, `${loaded.time} ${loaded.endpoint}`),
        h("div", { class: "split" }, h("div", {}, h("p", { class: "note" }, "请求"), h("pre", {}, pretty(loaded.request) || "空")), h("div", {}, h("p", { class: "note" }, "响应"), h("pre", {}, loaded.response == null ? "这条请求之后没有记录到响应。" : pretty(loaded.response)))),
      );
    }

    async function compare() {
      if (!picks.left || !picks.right) {
        receipt("先在列表里各选一条放到左边和右边。", "error");
        return;
      }
      const result = await attempt(() => api.post("capture/compare", { left: picks.left, right: picks.right }));
      if (!result) return;
      fill(compared, table(
          [
            { title: "位置", cell: (row) => row.path },
            { title: "左边", cell: (row) => KINDS[row.left] ?? row.left },
            { title: "右边", cell: (row) => KINDS[row.right] ?? row.right },
          ],
          result.differences,
          "两条响应的结构完全一致：字段和类型都相同。",
        ),
      );
    }

    function drawList() {
      const needle = filter.value.trim().toLowerCase();
      fill(list, table(
          [
            { title: "时间", cell: (entry) => entry.time },
            { title: "端点", cell: (entry) => entry.endpoint },
            { title: "响应", cell: (entry) => (entry.answered ? "有" : "无") },
            {
              title: "",
              cell: (entry) => [button("查看", () => open(entry)), " ", button("放到左边", () => pick("left", entry)), " ", button("放到右边", () => pick("right", entry))],
            },
          ],
          entries.filter((entry) => entry.endpoint.toLowerCase().includes(needle)),
          "这个文件里没有符合条件的请求。",
        ),
      );
    }

    filter.addEventListener("input", drawList);
    root.append(
      h("h2", {}, "抓包"),
      h("div", { class: "row" }, field("会话文件", chooser), field("筛选", filter)),
      list,
      detail,
      h("h3", {}, "对照两条响应的结构"),
      h("p", {}, "把官服的一条响应放到左边、本地的同一端点放到右边，可以看出哪些字段缺了、多了或者类型不同。选好一边后可以换一个会话文件再选另一边。"),
      sides,
      h("div", { class: "row" }, button("对照", compare, "main")),
      compared,
    );
    drawSides();
    drawList();
  },
};
