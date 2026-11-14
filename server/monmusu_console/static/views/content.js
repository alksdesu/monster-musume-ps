import { api, attempt, button, field, fill, h, pen, receipt, refreshMarks, struck, table } from "../app.js";
import { groupActions } from "./presets.js";

const PAGE = 50;
const POSITION = "#";
const SEARCH_DELAY = 300;
const OPEN_UP_TO = 12;
const DATE = /^(\d{4})\/(\d{2})\/(\d{2})(?: (\d{2}):(\d{2})(?::(\d{2}))?)?$/;
const JAPANESE = /[぀-ヿ一-鿿]/;
const browse = { table: "", q: "", offset: 0, changed: false };
let labels = new Map();
let resourcesList = null;
let groupsList = [];
let tablesList = [];

const scalar = (value) => value === null || typeof value !== "object";
const link = (name, key) => `#content/${name}${key ? `/${encodeURIComponent(JSON.stringify(key))}` : ""}`;
const titled = (name) => labels.get(name) ?? name;

function rowName(key, title) {
  const where = POSITION in key ? `第 ${key[POSITION] + 1} 行` : Object.values(key).join("，");
  return title ? `${title}（${where}）` : where;
}

function joined(parent, step) {
  if (!parent) return String(step);
  return typeof step === "number" ? `${parent}[${step}]` : `${parent}.${step}`;
}

function summary(value) {
  if (Array.isArray(value)) return `${value.length} 项`;
  return `${Object.keys(value).length} 个字段`;
}

function leafCount(value) {
  if (scalar(value)) return 1;
  return Object.values(value).reduce((total, inner) => total + leafCount(inner), 0);
}

function hit(text, needle) {
  const shown = String(text);
  const at = needle ? shown.toLowerCase().indexOf(needle.toLowerCase()) : -1;
  if (at < 0) return shown;
  return [shown.slice(0, at), h("mark", {}, shown.slice(at, at + needle.length)), shown.slice(at + needle.length)];
}

// A nested value has no column of its own, so its control is chosen the same way the server chooses for a column.
function kindOf(value) {
  if (typeof value === "boolean") return "flag";
  if (typeof value === "number") return "number";
  if (DATE.test(value)) return "datetime";
  return String(value).includes("\n") ? "textarea" : "text";
}

function toLocal(text) {
  const [, year, month, day, hour = "00", minute = "00"] = DATE.exec(text) ?? [];
  return year ? `${year}-${month}-${day}T${hour}:${minute}` : "";
}

// The game reads dates in its own spelling; the control's value is written back the way the shipped one was.
function fromLocal(value, shipped) {
  const [date, time = "00:00"] = value.split("T");
  const [, , , , hour, , second] = DATE.exec(shipped) ?? [];
  const day = date.replaceAll("-", "/");
  if (hour === undefined) return day;
  return `${day} ${time}${second === undefined ? "" : ":00"}`;
}

function groupedCatalog(groups, tables, chosen) {
  const filter = h("input", { type: "search", placeholder: "商店、卡池、shop…", "aria-label": "筛选表" });
  const nav = h("nav", { "aria-label": "可改的表" });

  function entry(row) {
    return h(
      "a",
      { href: link(row.name), title: `${row.label} ${row.name}`, "aria-current": row === chosen ? "true" : null, "data-marked": row.changed > 0 },
      h("span", {}, row.label, h("small", { class: "note" }, ` ${row.name}`)),
      row.changed > 0 && h("span", { class: "pen", "aria-label": `改了 ${row.changed} 行` }, String(row.changed)),
    );
  }

  function draw() {
    const needle = filter.value.trim().toLowerCase();
    const shown = tables.filter((row) => `${row.label} ${row.name}`.toLowerCase().includes(needle));
    fill(
      nav,
      groups.map((group) => {
        const inside = shown.filter((row) => row.group === group.key).sort((left, right) => left.order - right.order || left.name.localeCompare(right.name));
        if (!inside.length) return null;
        return h(
          "section",
          { class: "shelf", "data-marked": inside.some((row) => row.changed > 0) },
          h("h4", { title: group.blurb }, group.title, h("small", { class: "note" }, ` ${inside.length}`)),
          inside.map(entry),
        );
      }),
      !shown.length && h("p", { class: "empty" }, "没有名字里带这几个字的表。"),
    );
  }

  filter.addEventListener("input", draw);
  draw();
  return h("div", { class: "tables" }, field(`筛选 ${tables.length} 张表`, filter), nav);
}

function spokenCell(value, said, needle) {
  const shown = hit(value, needle);
  if (!said) return shown;
  return h("span", { class: "spoken", title: String(value) }, h("span", { class: "said" }, said), " ", h("span", { class: "source" }, shown));
}

function groupSection(group, actions, remount) {
  const inside = tablesList.filter((row) => row.group === group.key).sort((left, right) => left.order - right.order);
  const own = actions.filter((action) => action.group === group.key);
  if (!inside.length && !own.length) return null;
  return h(
    "section",
    { class: "group", id: `group-${group.key}` },
    h("h3", {}, group.title, h("small", { class: "note" }, ` ${group.blurb}`)),
    groupActions(own, remount),
    h("p", { class: "chips" }, "表：", inside.map((row) => h("a", { href: link(row.name), "data-marked": row.changed > 0 }, row.label, row.changed > 0 && h("span", { class: "pen" }, ` ${row.changed}`)))),
  );
}

async function drawGroups(pane, remount) {
  const [{ actions }, { changes }] = await Promise.all([api.get("presets"), api.get("content/changes")]);

  async function revert(change) {
    const body = { table: change.table, key: change.key, fields: { [change.path]: null } };
    if (await attempt(() => api.post("content/row", body))) await remount("已改回原值。");
  }

  fill(
    pane,
    groupsList.map((group) => groupSection(group, actions, remount)),
    h("h3", {}, "全部改动"),
    h("p", { class: "note" }, "这里列出所有表里改过的字段。"),
    table(
      [
        { title: "表", cell: (change) => titled(change.table) },
        { title: "行", cell: (change) => h("a", { href: link(change.table, change.key) }, rowName(change.key)) },
        { title: "字段", cell: (change) => change.path },
        { title: "原值", cell: (change) => struck(String(change.shipped ?? "")) },
        { title: "现值", cell: (change) => pen(String(change.value)) },
        { title: "", cell: (change) => button("改回", () => revert(change), "quiet") },
      ],
      changes.map((change) => ({ ...change, marked: true })),
      "还没有改动。",
    ),
  );
}

// One value for every row the search finds, written through the same overrides as a single edit.
function bulkEditor(chosen, page, remount) {
  const columns = page.columns.filter((column) => !page.key.includes(column.name) && !["list", "group"].includes(column.kind));
  const column = h("select", {}, columns.map((entry) => h("option", { value: entry.name }, entry.label === entry.name ? entry.name : `${entry.label} ${entry.name}`)));
  const path = h("input", { type: "text", size: 24, placeholder: "或写路径，如 rewards[0].count" });
  const valueBox = h("span", {});
  const scopeAll = h("input", { type: "radio", name: "scope", value: "all", checked: !browse.q && !browse.changed });
  const scopeFound = h("input", { type: "radio", name: "scope", value: "found", checked: Boolean(browse.q || browse.changed) });
  let current = null;

  function drawValue() {
    const kind = columns.find((entry) => entry.name === column.value)?.kind ?? "text";
    if (kind === "flag") current = h("input", { type: "checkbox" });
    else if (kind === "number") current = h("input", { type: "number", step: "any" });
    else if (kind === "datetime") current = h("input", { type: "datetime-local" });
    else current = h("input", { type: "text", size: 30 });
    current.dataset.kind = kind;
    fill(valueBox, current);
  }

  function typed() {
    const kind = current.dataset.kind;
    if (kind === "flag") return Number(current.checked);
    if (kind === "number") return current.value.trim() === "" ? NaN : Number(current.value);
    if (kind === "datetime") return current.value ? fromLocal(current.value, "2000/01/01 00:00:00") : "";
    return current.value;
  }

  async function apply() {
    const value = typed();
    if (Number.isNaN(value)) {
      receipt("请填一个数。");
      return;
    }
    const found = scopeFound.checked;
    const target = path.value.trim() || column.value;
    const count = found ? page.total : chosen.rows;
    if (!confirm(`把${found ? "当前找到的" : "这张表全部"} ${count} 行的 ${target} 都改成 ${JSON.stringify(value)}？`)) return;
    const body = { table: chosen.name, path: target, value, ...(found ? { q: browse.q, changed: browse.changed } : {}) };
    const outcome = await attempt(() => api.post("content/bulk", body));
    if (outcome) await remount(`已改 ${outcome.rows} 行，服务器已经按新值生效。游戏要重启或回标题重进才会读到。`);
  }

  column.addEventListener("change", drawValue);
  drawValue();
  return h(
    "details",
    { class: "bulk" },
    h("summary", {}, "批量改"),
    h("div", { class: "row" }, field("字段", column), field("", path), field("改成", valueBox)),
    h("div", { class: "row" }, h("label", { class: "inline" }, scopeAll, `这张表全部 ${chosen.rows} 行`), h("label", { class: "inline" }, scopeFound, `当前找到的 ${page.total} 行`), button("改", apply, "danger")),
  );
}

async function drawRows(pane, chosen, remount) {
  if (browse.table !== chosen.name) Object.assign(browse, { table: chosen.name, q: "", offset: 0, changed: false });
  const search = h("input", { type: "search", value: browse.q, placeholder: "任意字段里的文字或数字" });
  const onlyChanged = h("input", { type: "checkbox", checked: browse.changed });
  const body = h("div", {});
  const bulkBox = h("div", {});
  let timer = null;

  async function load() {
    const query = { table: chosen.name, q: browse.q, offset: browse.offset, limit: PAGE, ...(browse.changed ? { changed: 1 } : {}) };
    const page = await attempt(() => api.get("content/rows", query));
    if (page) {
      draw(page);
      fill(bulkBox, bulkEditor(chosen, page, remount));
    }
  }

  function turn(step) {
    browse.offset = Math.max(0, browse.offset + step * PAGE);
    load();
  }

  async function resetTable() {
    if (!confirm(`把「${chosen.label}」里改过的 ${chosen.changed} 行全部改回原值？`)) return;
    if (await attempt(() => api.post("content/reset", { table: chosen.name }))) await remount("这张表已全部改回原值。");
  }

  function cell(column, row) {
    const value = row.values[column.name];
    const changed = row.changed.some((path) => path === column.name || path.startsWith(`${column.name}.`) || path.startsWith(`${column.name}[`));
    const shown = scalar(value) ? spokenCell(value, row.spoken[column.name], browse.q.trim()) : summary(value);
    return changed ? pen(shown) : shown;
  }

  function draw(page) {
    const last = Math.min(browse.offset + PAGE, page.total);
    const columns = page.columns.map((column) => ({
      title: h("span", { title: column.name }, column.label, column.label !== column.name && h("small", { class: "note" }, ` ${column.name}`)),
      number: column.kind === "number",
      cell: (row) => cell(column, row),
    }));
    const rows = page.rows.map((row) => ({ ...row, marked: row.changed.length > 0 }));
    const listed = table(columns, rows, browse.q || browse.changed ? "没有符合条件的行。" : "这张表是空的。", (row) => (location.hash = link(chosen.name, row.key)));
    listed.querySelector("table")?.classList.add("grid");
    fill(
      body,
      h(
        "div",
        { class: "row spread" },
        h("span", { class: "pager" }, page.total ? `第 ${browse.offset + 1} 到 ${last} 行，共 ${page.total} 行` : "共 0 行"),
        h(
          "span",
          { class: "pager" },
          h("button", { type: "button", disabled: browse.offset === 0, onclick: () => turn(-1) }, "上一页"),
          h("button", { type: "button", disabled: last >= page.total, onclick: () => turn(1) }, "下一页"),
        ),
      ),
      listed,
    );
  }

  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      Object.assign(browse, { q: search.value, offset: 0 });
      load();
    }, SEARCH_DELAY);
  });
  onlyChanged.addEventListener("change", () => {
    Object.assign(browse, { changed: onlyChanged.checked, offset: 0 });
    load();
  });
  const group = groupsList.find((entry) => entry.key === chosen.group);
  const { actions } = await api.get("presets");
  const own = actions.filter((action) => action.group === chosen.group);
  fill(
    pane,
    h("h3", {}, chosen.label, h("small", { class: "note" }, ` ${chosen.name}`)),
    own.length > 0 && h("details", { class: "strip" }, h("summary", {}, `「${group?.title ?? chosen.group}」这一类的快捷操作（${own.length} 项）`), groupActions(own, remount)),
    h("p", { class: "note" }, `${chosen.rows} 行，每行 ${chosen.fields} 个字段。日文旁边的中文是汉化译文，只帮着看，改的是日文原值。点一行进去改。`),
    h(
      "div",
      { class: "row" },
      field("搜索这张表", search),
      h("label", { class: "inline" }, onlyChanged, "只看改过的"),
      chosen.changed > 0 && button("整张表改回原值", resetTable, "danger"),
    ),
    bulkBox,
    body,
  );
  await load();
}

async function resources() {
  resourcesList ??= (await api.get("resources")).rows;
  return resourcesList;
}

async function drawRow(pane, chosen, key, remount) {
  const opened = await api.get("content/row", { table: chosen.name, key: JSON.stringify(key) });
  const kinds = new Map(opened.columns.map((column) => [column.name, column]));
  const saved = new Set(opened.changed);
  const staged = new Map();
  const tbody = h("tbody", {});
  const saveButton = button("保存这一行", save, "main");
  const picks = kinds.size && [...kinds.values()].some((column) => column.kind === "resource") ? await resources() : [];

  function stage(path, value, shipped, row) {
    const same = value === shipped;
    // Putting a saved change back has to be sent; an unsaved one is simply dropped.
    if (!same) staged.set(path, value);
    else if (saved.has(path)) staged.set(path, null);
    else staged.delete(path);
    if (same) row.removeAttribute("data-marked");
    else row.setAttribute("data-marked", "");
    saveButton.textContent = staged.size ? `保存这一行（${staged.size} 处待保存）` : "保存这一行";
  }

  // Each control hands back what the game stores, in the type and spelling the shipped value had.
  function control(kind, shipped, current, report) {
    if (kind === "flag") {
      const box = h("input", { type: "checkbox", checked: Boolean(current) });
      const word = h("span", {}, box.checked ? "开" : "关");
      box.addEventListener("change", () => {
        word.textContent = box.checked ? "开" : "关";
        report(typeof shipped === "boolean" ? box.checked : Number(box.checked));
      });
      const put = (value) => {
        box.checked = Boolean(value);
        word.textContent = box.checked ? "开" : "关";
      };
      return { node: h("label", { class: "inline" }, box, word), put };
    }
    if (kind === "number") {
      const input = h("input", { type: "number", step: "any", value: current });
      input.addEventListener("input", () => input.value.trim() !== "" && report(Number(input.value)));
      return { node: input, put: (value) => (input.value = value) };
    }
    if (kind === "datetime") {
      const input = h("input", { type: "datetime-local", value: toLocal(current) });
      input.addEventListener("input", () => input.value && report(fromLocal(input.value, shipped)));
      return { node: h("span", { class: "stamped" }, input, h("small", { class: "note" }, "日本时间")), put: (value) => (input.value = toLocal(value)) };
    }
    if (kind === "textarea") {
      const area = h("textarea", {}, current);
      area.addEventListener("input", () => report(area.value));
      return { node: area, put: (value) => (area.value = value) };
    }
    const input = h("input", { type: "text", value: current, list: kind === "resource" ? "resources" : null });
    input.addEventListener("input", () => report(input.value));
    return { node: input, put: (value) => (input.value = value) };
  }

  function leaf(name, path, shipped, current, depth) {
    const column = depth === 0 ? kinds.get(name) : null;
    const locked = depth === 0 && name in key;
    const kind = column?.kind ?? kindOf(shipped);
    const said = depth === 0 ? opened.spoken[name] : null;
    const row = h("tr", { "data-marked": current !== shipped });
    const made = locked ? null : control(kind, shipped, current, (value) => stage(path, value, shipped, row));
    const label = column && column.label !== column.name ? [column.label, h("small", { class: "note" }, ` ${column.name}`)] : String(name);
    row.append(
      h("td", { style: `--depth: ${depth}` }, label),
      h("td", {}, String(shipped), said && h("span", { class: "said" }, said)),
      h("td", {}, made?.node ?? h("span", { class: "note" }, "用来找到这一行，不能改")),
      h(
        "td",
        { class: "actions" },
        made &&
          button(
            "改回",
            () => {
              made.put(shipped);
              stage(path, shipped, shipped, row);
            },
            "quiet",
          ),
      ),
    );
    return { visible: () => [row] };
  }

  function group(name, path, shipped, current, depth) {
    const touched = [...saved].some((changed) => changed.startsWith(`${path}.`) || changed.startsWith(`${path}[`));
    const toggle = button("", flip, "quiet");
    const column = depth === 0 ? kinds.get(name) : null;
    const shownName = column && column.label !== column.name ? `${column.label} ${column.name}` : name;
    const head = h("tr", { class: "group", "data-marked": touched }, h("td", { style: `--depth: ${depth}`, colspan: 4 }, toggle));
    // A pool's prize list runs to thousands of values; they are built only when asked for.
    const node = {
      open: touched || leafCount(shipped) <= OPEN_UP_TO,
      children: null,
      inside() {
        this.children ??= entries(shipped, current, path, depth + 1);
        return this.children.flatMap((child) => child.visible());
      },
      visible() {
        return this.open ? [head, ...this.inside()] : [head];
      },
    };

    function label() {
      toggle.textContent = `${node.open ? "收起" : "展开"} ${shownName}（${summary(shipped)}）`;
      toggle.setAttribute("aria-expanded", String(node.open));
    }

    function flip() {
      if (node.open) for (const row of node.inside()) row.remove();
      node.open = !node.open;
      if (node.open) head.after(...node.inside());
      label();
    }

    label();
    return node;
  }

  function entries(shipped, current, parent, depth) {
    const steps = Array.isArray(shipped) ? shipped.map((_, index) => index) : Object.keys(shipped);
    return steps.map((step) => {
      const path = joined(parent, step);
      const name = typeof step === "number" ? `[${step}]` : step;
      return scalar(shipped[step])
        ? leaf(name, path, shipped[step], current[step], depth)
        : group(name, path, shipped[step], current[step], depth);
    });
  }

  async function save() {
    if (!staged.size) {
      receipt("这一行没有待保存的改动。");
      return;
    }
    const body = { table: chosen.name, key, fields: Object.fromEntries(staged) };
    if (await attempt(() => api.post("content/row", body))) await remount("已保存，服务器已经按新值生效。游戏要重启或回标题重进才会读到。");
  }

  async function resetRow() {
    if (!confirm(`把 ${rowName(key, opened.title)} 改过的 ${saved.size} 个字段全部改回原值？`)) return;
    if (await attempt(() => api.post("content/reset", { table: chosen.name, key }))) await remount("这一行已全部改回原值。");
  }

  fill(tbody, entries(opened.shipped, opened.current, "", 0).flatMap((node) => node.visible()));
  fill(
    pane,
    h("p", {}, h("a", { href: link(chosen.name) }, `回到「${chosen.label}」的列表`)),
    h("h3", {}, `${chosen.label}：${rowName(key, opened.title)}`),
    JAPANESE.test(opened.title) && opened.spoken && Object.values(opened.spoken)[0] && h("p", { class: "note" }, "原值下面的中文是汉化译文，只帮着看，改的是日文原值。"),
    h("div", { class: "row" }, saveButton, saved.size > 0 && button("这一行全部改回原值", resetRow, "danger")),
    picks.length > 0 && h("datalist", { id: "resources" }, picks.map((row) => h("option", { value: row.rid }, row.name))),
    h(
      "div",
      { class: "scroll" },
      h(
        "table",
        { class: "sheet" },
        h("thead", {}, h("tr", {}, ["字段", "原值", "现值", ""].map((title) => h("th", { scope: "col" }, title)))),
        tbody,
      ),
    ),
  );
}

export const content = {
  title: "内容",
  async mount(root, argument) {
    const [name, ...rest] = argument.split("/");
    const { groups, tables } = await api.get("content");
    labels = new Map(tables.map((row) => [row.name, row.label]));
    groupsList = groups;
    tablesList = tables;
    const chosen = tables.find((entry) => entry.name === name);
    const pane = h("div", {});
    root.append(
      h("h2", {}, "内容"),
      h(
        "p",
        { class: "note" },
        "商店、卡池、任务、签到、活动、通行证、关卡这些运营内容都能改，按用途分在左边。每一类上面是这一类的快捷操作：全部开放、挑着开、开关和旋钮；点进一张表能逐行改，也能批量改。改动只记在服务器这边，游戏文件不动；服务器立即生效，游戏要重启或回标题重进才会读到。战斗数据保持原样，不在这里。",
      ),
      h("div", { class: "catalog" }, groupedCatalog(groups, tables, chosen), pane),
    );
    // Every change moves the counts in the list of tables, so the page is drawn again from the server's answer.
    async function remount(done) {
      await content.mount(fill(root), argument);
      receipt(done);
      refreshMarks();
    }

    if (!chosen) await drawGroups(pane, remount);
    else if (rest.length) await drawRow(pane, chosen, JSON.parse(rest.join("/")), remount);
    else await drawRows(pane, chosen, remount);
  },
};
