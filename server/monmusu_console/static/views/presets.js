import { api, attempt, button, field, fill, h, receipt, table } from "../app.js";

const PAGE = 50;
const SEARCH_DELAY = 300;
const STATES = { "": "全部", ended: "已结束", upcoming: "未开始", running: "在期" };
const PAGES = { shortcuts: "一键", roster: "角色" };

function meter(done, total, unit) {
  const share = total ? Math.min(1, done / total) : 0;
  return h(
    "span",
    { class: "tally", "data-full": total > 0 && done >= total, style: `--share: ${share}` },
    h("span", { class: "tally-ink" }),
    h("span", { class: "tally-text" }, h("strong", {}, String(done)), ` / ${total} ${unit}`),
  );
}

function window_(side) {
  const start = side.start || "不限";
  const end = side.end || "不限";
  return `${start} 到 ${end}`;
}

// Entrances of one kind are listed with their windows so a few can be opened by hand.
function picker(action, remount) {
  const browse = { q: "", status: "ended", offset: 0 };
  const search = h("input", { type: "search", size: 22, placeholder: "编号或名称" });
  const state = h("select", {}, Object.entries(STATES).map(([value, text]) => h("option", { value, selected: value === browse.status }, text)));
  const box = h("div", {});
  const chosen = new Set();
  const openButton = button("开放选中的", open, "main");
  let timer = null;

  async function load() {
    const listed = await attempt(() => api.get("presets/items", { key: action.key, ...browse, limit: PAGE }));
    if (listed) draw(listed);
  }

  async function open() {
    if (!chosen.size) {
      receipt("先勾选要开放的内容。");
      return;
    }
    const outcome = await attempt(() => api.post("presets/pick", { key: action.key, ids: [...chosen], on: true }));
    if (outcome) await remount(`${action.title.replace("全部开放", "")}：已挑开 ${outcome.picked} 个，游戏回标题重进后可见。`);
  }

  async function drop(id) {
    const outcome = await attempt(() => api.post("presets/pick", { key: action.key, ids: [id], on: false }));
    if (outcome) await remount("已从挑开的名单里去掉。");
  }

  function draw(listed) {
    const last = Math.min(browse.offset + PAGE, listed.total);
    fill(
      box,
      table(
        [
          {
            title: "",
            cell: (row) => {
              if (row.picked) return h("span", { class: "pencil" }, "已挑开");
              const tick = h("input", { type: "checkbox", checked: chosen.has(row.id), "aria-label": `选中 ${row.name}` });
              tick.addEventListener("change", () => {
                if (tick.checked) chosen.add(row.id);
                else chosen.delete(row.id);
                openButton.textContent = chosen.size ? `开放选中的 ${chosen.size} 个` : "开放选中的";
              });
              return tick;
            },
          },
          { title: "编号", cell: (row) => String(row.id) },
          { title: "名称", cell: (row) => row.name },
          { title: "原本的期间", cell: (row) => window_(row.shipped) },
          { title: "现在", cell: (row) => STATES[row.status] },
          { title: "", cell: (row) => row.picked && button("取消", () => drop(row.id), "quiet") },
        ],
        listed.rows,
        "没有符合条件的内容。换一个状态或搜索词试试。",
      ),
      h(
        "div",
        { class: "row spread" },
        h("span", { class: "pager" }, listed.total ? `第 ${browse.offset + 1} 到 ${last} 项，共 ${listed.total} 项` : "共 0 项"),
        h(
          "span",
          { class: "pager" },
          h("button", { type: "button", disabled: browse.offset === 0, onclick: () => ((browse.offset = Math.max(0, browse.offset - PAGE)), load()) }, "上一页"),
          h("button", { type: "button", disabled: last >= listed.total, onclick: () => ((browse.offset += PAGE), load()) }, "下一页"),
        ),
      ),
    );
  }

  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      Object.assign(browse, { q: search.value, offset: 0 });
      load();
    }, SEARCH_DELAY);
  });
  state.addEventListener("change", () => {
    Object.assign(browse, { status: state.value, offset: 0 });
    load();
  });
  const details = h(
    "details",
    { class: "picker" },
    h("summary", {}, `挑着开${action.picked.length ? `（已挑开 ${action.picked.length} 个）` : ""}`),
    h("div", { class: "row" }, field("状态", state), field("搜索", search), openButton),
    box,
  );
  details.addEventListener("toggle", () => details.open && !box.childElementCount && load());
  return details;
}

// Waves are the real-world line-ups: whatever ran together on a given day. A few can be open at once.
function waves(action, remount) {
  const box = h("div", {});
  const chosen = new Set(action.waves);
  const saveButton = button("按选中的期开放", save, "main");
  let limit = 0;

  function label() {
    saveButton.textContent = chosen.size ? `按选中的 ${chosen.size} 期开放` : "按选中的期开放";
  }

  async function load() {
    const listed = await attempt(() => api.get("presets/waves", { key: action.key }));
    if (listed) draw(listed);
  }

  async function save() {
    const outcome = await attempt(() => api.post("presets/waves", { key: action.key, moments: [...chosen] }));
    if (outcome) await remount(chosen.size ? `已按 ${outcome.picked} 期开放 ${outcome.opened} 个卡池，游戏回标题重进后可见。` : "已清空按期开放的名单。");
  }

  function draw(listed) {
    limit = listed.limit;
    fill(
      box,
      h("p", { class: "note" }, `每一期是现实中某一天同时在开的卡池。最多同时选 ${limit} 期；最新一期和官服关服前一致。横幅数是客户端还能显示出来的卡池数。`),
      table(
        [
          {
            title: "",
            cell: (row) => {
              const tick = h("input", { type: "checkbox", checked: chosen.has(row.at), "aria-label": `选中 ${row.at} 这一期` });
              tick.addEventListener("change", () => {
                if (tick.checked && chosen.size >= limit) {
                  tick.checked = false;
                  receipt(`最多同时开 ${limit} 期，先取消一期再选。`);
                  return;
                }
                if (tick.checked) chosen.add(row.at);
                else chosen.delete(row.at);
                label();
              });
              return tick;
            },
          },
          { title: "开始", cell: (row) => row.at.slice(0, 10) },
          { title: "横幅", cell: (row) => String(row.banners) },
          { title: "卡池", cell: (row) => row.groups.map((group) => group.name).join("、") },
        ],
        listed.waves,
        "主数据里没有带起止时间的卡池。",
      ),
    );
    label();
  }

  const details = h(
    "details",
    { class: "picker" },
    h("summary", {}, `按期开放${action.waves.length ? `（已选 ${action.waves.length} 期）` : ""}`),
    h("div", { class: "row" }, saveButton),
    box,
  );
  details.addEventListener("toggle", () => details.open && !box.childElementCount && load());
  return details;
}

function opener(action, remount) {
  async function all(on) {
    const asked = on ? `把${action.title.replace("全部开放", "")}全部开到永远？` : "撤销这一类的全部开放，恢复各自原本的期间？";
    if (!confirm(asked)) return;
    const outcome = await attempt(() => api.post("presets/run", { key: action.key, on }));
    if (outcome) await remount(on ? `已全部开放 ${outcome.opened} 个，游戏回标题重进后可见。` : `已撤销，${outcome.dropped} 个回到原本的期间。`);
  }

  return h(
    "div",
    { class: "entry" },
    h("div", { class: "entry-text" }, h("strong", {}, action.title), h("p", {}, action.blurb)),
    meter(action.running, action.total, "个在期"),
    h("span", { class: "entry-actions" }, action.opened ? button("撤销全开", () => all(false)) : button("全部开放", () => all(true), "main")),
    action.waves && waves(action, remount),
    picker(action, remount),
  );
}

function toggle(action, remount) {
  const box = h("input", { type: "checkbox", checked: action.on });
  box.addEventListener("change", async () => {
    const outcome = await attempt(() => api.post("presets/run", { key: action.key, on: box.checked }));
    if (outcome) await remount(outcome.on ? "消耗全免已打开，服务器立即生效。" : "消耗全免已关闭。");
    else box.checked = action.on;
  });
  return h(
    "div",
    { class: "entry" },
    h("div", { class: "entry-text" }, h("strong", {}, action.title), h("p", {}, action.blurb)),
    h("span", { class: "tally" }, h("span", { class: "tally-text" }, action.on ? "开着" : "关着")),
    h("label", { class: "inline" }, box, "开"),
  );
}

// A setting is turned right where its content lives, with the file written behind it.
function knob(action, remount) {
  const control =
    action.kind === "flag"
      ? h("input", { type: "checkbox", checked: action.value })
      : h("input", { type: "number", step: "any", min: 0, value: action.value });
  async function save() {
    const value = action.kind === "flag" ? control.checked : Number(control.value);
    if (action.kind !== "flag" && (control.value.trim() === "" || Number.isNaN(value))) {
      receipt("请填一个数。");
      return;
    }
    const outcome = await attempt(() => api.post("presets/run", { key: action.key, value }));
    if (outcome) await remount(`${action.title} 已改为 ${outcome.value}，服务器立即生效。`);
  }
  if (action.kind === "flag") control.addEventListener("change", save);
  return h(
    "div",
    { class: "entry entry-knob" },
    h("div", { class: "entry-text" }, h("strong", {}, action.title), h("p", {}, action.blurb)),
    h("span", { class: "tally" }, h("span", { class: "tally-text" }, "现在 ", h("strong", {}, String(action.value)))),
    action.kind === "flag" ? h("label", { class: "inline" }, control, "开") : h("span", { class: "knob" }, control, button("改", save)),
  );
}

function link(action) {
  return h(
    "div",
    { class: "entry entry-link" },
    h("div", { class: "entry-text" }, h("strong", {}, action.title), h("p", {}, action.blurb)),
    h("span", { class: "tally" }),
    h("a", { href: `#${action.page}` }, `去「${PAGES[action.page] ?? action.page}」页`),
  );
}

export function groupActions(actions, remount) {
  if (!actions.length) return null;
  return h(
    "div",
    { class: "ledger" },
    actions.map((action) => {
      if (action.action === "open") return opener(action, remount);
      if (action.action === "toggle") return toggle(action, remount);
      if (action.action === "knob") return knob(action, remount);
      return link(action);
    }),
  );
}
