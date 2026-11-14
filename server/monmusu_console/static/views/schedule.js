import { api, attempt, button, field, fill, h, receipt, table } from "../app.js";

const FOREVER = "2999/12/31 23:59:59";
const UNBOUNDED = "-";
const PAGE = 50;
const KINDS = {
  event: "活动",
  gacha: "卡池",
  pass: "通行证",
  sugoroku: "双六",
  bingo: "宾果",
  quest: "关卡",
  secret: "ヒミツ分类",
  loginbonus: "签到表",
  banner: "横幅",
  shop_tab: "商店页签",
  goods: "商店商品",
  mission: "任务",
  announce: "公告图",
  vote: "人气投票",
};
const STATES = { "": "全部", running: "在期", ended: "已结束", upcoming: "未开始" };
const TIME_HINT = "不改";

function side(entry, name) {
  if (!(name in entry)) return "";
  return entry[name] === "" ? UNBOUNDED : entry[name];
}

function setSide(entry, name, text) {
  const value = text.trim();
  if (!value) delete entry[name];
  else entry[name] = value === UNBOUNDED ? "" : value;
}

function scope(entry) {
  if (entry.all === true) return "历来全部（有结束时间的）";
  if ("running_at" in entry) return `${entry.running_at} 时在期的全部`;
  return entry.ids.length > 4 ? `${entry.ids.slice(0, 4).join("、")} 等 ${entry.ids.length} 个` : entry.ids.join("、");
}

function window_(row) {
  return `${row.start || "不限"} 至 ${row.end || "不限"}`;
}

export const schedule = {
  title: "排期",
  async mount(root) {
    let loaded = await api.get("schedule");
    let draft = structuredClone(loaded.document);
    let dirty = false;
    const head = h("div", {});
    const list = h("div", {});
    const previewBox = h("div", {});
    const catalogBox = h("div", {});
    const browse = { kind: "event", status: "ended", q: "", offset: 0 };

    function touch() {
      dirty = true;
      drawHead();
    }

    function drawHead() {
      const total = Object.values(loaded.tables).reduce((sum, count) => sum + count, 0);
      const problem = h("p", { class: "problem" }, `磁盘上的排期没能生效，服务器在用上一份：${loaded.error}`);
      problem.hidden = !loaded.error;
      fill(head, h("p", {}, `当前生效的排期改写了 ${Object.keys(loaded.tables).length} 张表共 ${total} 行，版本 ${loaded.revision}。客户端回标题重进后才会拿到新的排期。`),
        problem,
        h("p", { class: "note" }, "开始和结束按日本时间填写，格式 2026/12/01 12:00:00；留空表示不改，填 - 表示不限。"),
        h("div", { class: "row" }, button(dirty ? "保存排期（有未保存的改动）" : "保存排期", save, "main")),
      );
    }

    async function save() {
      const saved = await attempt(() => api.post("schedule", { document: draft }), "排期已保存并生效。");
      if (!saved) return;
      loaded = saved;
      draft = structuredClone(saved.document);
      dirty = false;
      drawHead();
      drawList();
      drawCatalog();
    }

    async function preview(entry) {
      const result = await attempt(() => api.post("schedule/preview", { entry }));
      if (!result) return;
      fill(previewBox, h("h3", {}, `「${entry.name || KINDS[entry.kind]}」会改写的行`),
        table(
          [
            { title: "表", cell: (row) => row.table },
            { title: "行", cell: (row) => Object.values(row.key).join(" / ") },
            { title: "改成", cell: (row) => Object.entries(row.fields).map(([name, value]) => h("div", {}, `${name} = ${value || "不限"}`)) },
          ],
          result.sample,
          "这个条目不会改写任何行：它指向的内容本来就没有结束时间，或者已经比条目的结束时间更晚。",
        ),
        h("p", { class: "note" }, `合计：${Object.entries(result.tables).map(([name, count]) => `${name} ${count} 行`).join("，") || "0 行"}。每张表最多列出前 20 行。`),
      );
    }

    function drawList() {
      const time = (entry, name) => {
        const input = h("input", { type: "text", class: "moved", size: 19, value: side(entry, name), placeholder: TIME_HINT, "aria-label": name === "start" ? "开始" : "结束" });
        input.addEventListener("change", () => (setSide(entry, name, input.value), touch()));
        return input;
      };
      fill(list, table(
          [
            {
              title: "开",
              cell: (entry) => {
                const box = h("input", { type: "checkbox", checked: entry.enabled !== false, "aria-label": "启用" });
                box.addEventListener("change", () => ((entry.enabled = box.checked), touch()));
                return box;
              },
            },
            {
              title: "名称",
              cell: (entry) => {
                const input = h("input", { type: "text", size: 30, value: entry.name ?? "" });
                input.addEventListener("change", () => ((entry.name = input.value), touch()));
                return input;
              },
            },
            { title: "类型", cell: (entry) => KINDS[entry.kind] ?? entry.kind },
            { title: "范围", cell: scope },
            { title: "开始", cell: (entry) => time(entry, "start") },
            { title: "结束", cell: (entry) => time(entry, "end") },
            {
              title: "",
              cell: (entry) => [
                button("预览", () => preview(entry)),
                " ",
                button("删除", () => (draft.activities.splice(draft.activities.indexOf(entry), 1), touch(), drawList()), "danger"),
              ],
            },
          ],
          draft.activities,
          "排期里还没有条目。可以在下面的目录里挑内容加入。",
        ),
      );
    }

    function add(entry) {
      draft.activities.push(entry);
      touch();
      drawList();
      receipt(`已把「${entry.name}」加进排期，保存后生效。`);
    }

    const keepKind = h("select", {}, loaded.kinds.map((kind) => h("option", { value: kind }, KINDS[kind] ?? kind)));
    const keepAt = h("input", { type: "text", size: 19, placeholder: "2026/11/13 12:00:00" });

    async function drawCatalog() {
      const listed = await attempt(() => api.get("schedule/catalog", { ...browse, limit: PAGE }));
      if (!listed) return;
      const last = Math.max(0, listed.total - 1);
      fill(catalogBox, table(
          [
            { title: "编号", cell: (row) => String(row.id) },
            { title: "名称", cell: (row) => row.name },
            { title: "原本的期间", cell: (row) => window_(row.shipped) },
            { title: "现在的期间", cell: (row) => window_(row.current) },
            { title: "状态", cell: (row) => STATES[row.status] },
            {
              title: "",
              cell: (row) =>
                button("加入排期，一直开放", () => add({ name: row.name, kind: browse.kind, ids: [row.id], end: FOREVER, enabled: true })),
            },
          ],
          listed.rows,
          "没有符合条件的内容。换一个状态或搜索词试试。",
        ),
        h(
          "div",
          { class: "row" },
          h("span", { class: "note" }, `共 ${listed.total} 项，正在看第 ${listed.total ? browse.offset + 1 : 0} 到 ${Math.min(browse.offset + PAGE, listed.total)} 项`),
          button("上一页", () => ((browse.offset = Math.max(0, browse.offset - PAGE)), drawCatalog())),
          button("下一页", () => ((browse.offset = Math.min(last - (last % PAGE), browse.offset + PAGE)), drawCatalog())),
        ),
      );
    }

    const kind = h("select", {}, loaded.kinds.map((name) => h("option", { value: name, selected: name === browse.kind }, KINDS[name] ?? name)));
    const state = h("select", {}, Object.entries(STATES).map(([value, text]) => h("option", { value, selected: value === browse.status }, text)));
    const search = h("input", { type: "search", size: 24, placeholder: "编号或名称" });
    const browseBy = (name, control) =>
      control.addEventListener("change", () => ((browse[name] = control.value), (browse.offset = 0), drawCatalog()));
    browseBy("kind", kind);
    browseBy("status", state);
    browseBy("q", search);

    root.append(
      h("h2", {}, "排期"),
      head,
      list,
      previewBox,
      h("h3", {}, "按类整批开放"),
      h("p", {}, "现实时间往后走，在期的内容会陆续结束。选一类内容：可以把指定时刻还在期的全部留住，也可以不挑日子，把这一类历来有结束时间的全部开到永远。"),
      h(
        "div",
        { class: "row" },
        field("类型", keepKind),
        field("那一刻（日本时间）", keepAt),
        button("留住那一刻在期的", () =>
          add({ name: `${keepAt.value} 时在期的${KINDS[keepKind.value] ?? keepKind.value}`, kind: keepKind.value, running_at: keepAt.value.trim(), end: FOREVER, enabled: true }),
        ),
        button("历来全部一直开放", () =>
          add({ name: `${KINDS[keepKind.value] ?? keepKind.value}（全部开放）`, kind: keepKind.value, all: true, end: FOREVER, enabled: true }),
        ),
      ),
      h("h3", {}, "从目录里挑内容"),
      h("div", { class: "row" }, field("类型", kind), field("状态", state), field("搜索", search)),
      catalogBox,
    );
    drawHead();
    drawList();
    await drawCatalog();
  },
};
