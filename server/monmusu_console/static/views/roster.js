import { api, attempt, button, field, fill, h, outcomeText, receipt } from "../app.js";
import { chosenSave } from "./profiles.js";

const ASPECTS = [
  ["rank", "觉醒"],
  ["level", "等级"],
  ["exskill", "EX 技能"],
  ["weapon", "专武"],
  ["potential", "潜能点"],
];
const RARITY_ORDER = { SSR: 0, SR: 1, HR: 2, R: 3 };
const browse = { profile: "", q: "", pick: "" };

const link = (profile, instance) => `#roster/${encodeURIComponent(profile)}${instance == null ? "" : `/${instance}`}`;
const byRarity = (left, right) => (RARITY_ORDER[left.rarity] ?? 9) - (RARITY_ORDER[right.rarity] ?? 9) || left.card_id - right.card_id;
const matches = (unit, needle) => !needle || `${unit.name} ${unit.nickname} ${unit.card_id}`.toLowerCase().includes(needle);

function list(units, profile, current) {
  const search = h("input", { type: "search", value: browse.q, placeholder: "名字、称号或编号", "aria-label": "筛选角色" });
  const nav = h("nav", { "aria-label": "拥有的角色" });

  function draw() {
    const needle = search.value.trim().toLowerCase();
    const shown = units.filter((unit) => matches(unit, needle)).sort(byRarity);
    fill(
      nav,
      shown.map((unit) =>
        h(
          "a",
          { href: link(profile, unit.instance_id), "aria-current": unit.instance_id === current ? "true" : null },
          h("span", {}, h("b", { class: "rarity", "data-rarity": unit.rarity }, unit.rarity), ` ${unit.name}`, h("small", { class: "note" }, ` ${unit.nickname}`)),
          h("span", { class: "level" }, `Lv ${unit.level}`),
        ),
      ),
      !shown.length && h("p", { class: "empty" }, units.length ? "没有叫这个名字的角色。" : "这个存档还没有角色。"),
    );
  }

  search.addEventListener("input", () => {
    browse.q = search.value;
    draw();
  });
  draw();
  return h("div", { class: "tables" }, field(`筛选 ${units.length} 个角色`, search), nav);
}

function stepper(min, max, value, onchange) {
  const input = h("input", { type: "number", min, max, value });
  const range = h("input", { type: "range", min, max, value, "aria-hidden": "true", tabindex: -1 });
  input.addEventListener("input", () => {
    range.value = input.value;
    onchange(Number(input.value));
  });
  range.addEventListener("input", () => {
    input.value = range.value;
    onchange(Number(range.value));
  });
  return h("span", { class: "stepper" }, input, range, h("span", { class: "note" }, `/ ${max}`));
}

function editor(unit, profile, reload) {
  const fields = {};
  const saveButton = button("保存", save, "main");

  function stage(name, value) {
    fields[name] = value;
    saveButton.textContent = `保存（${Object.keys(fields).length} 处待保存）`;
  }

  async function save() {
    if (!Object.keys(fields).length) {
      receipt("这个角色没有待保存的改动。");
      return;
    }
    const body = { profile, instance_id: unit.instance_id, fields };
    const outcome = await attempt(() => api.post("roster/unit", body));
    if (outcome) await reload(`已保存 ${unit.name}。${outcomeText(outcome)}`);
  }

  async function fillUp(aspects) {
    const body = { profile, aspects, instance_ids: [unit.instance_id] };
    const outcome = await attempt(() => api.post("roster/fill", body));
    if (outcome) await reload(`${unit.name} 已拉满。${outcomeText(outcome)}`);
  }

  const exskills = {};
  const potentialSkills = new Set(unit.potential.skills);
  const skills = h(
    "div",
    { class: "choices" },
    unit.potential.choices.map((choice) => {
      const box = h("input", { type: "checkbox", checked: potentialSkills.has(choice.slot) });
      box.addEventListener("change", () => {
        if (box.checked) potentialSkills.add(choice.slot);
        else potentialSkills.delete(choice.slot);
        stage("potential_skills", [...potentialSkills].sort());
      });
      return h("label", { class: "inline" }, box, `${choice.name}（${choice.cost} 点）`);
    }),
  );
  const skin = h(
    "select",
    {},
    unit.skins.map((row) => h("option", { value: row.id, selected: row.id === unit.skin }, `${row.name}${row.adult ? "（R18）" : ""}`)),
  );
  skin.addEventListener("change", () => stage("skin", Number(skin.value)));
  const guarded = h("input", { type: "checkbox", checked: unit.protected });
  guarded.addEventListener("change", () => stage("protected", guarded.checked));

  function line(title, control, top) {
    return h("tr", {}, h("th", { scope: "row" }, title), h("td", {}, control), h("td", { class: "actions" }, top && button("拉满", () => fillUp(top), "quiet")));
  }

  const rows = [
    line("觉醒（阶级）", stepper(0, unit.rank_cap, unit.rank, (value) => stage("rank", value)), ["rank"]),
    line("等级", stepper(1, unit.level_cap, unit.level, (value) => stage("level", value)), ["level"]),
    ...unit.exskills.map((skill) =>
      line(
        `EX 技能 ${skill.slot}：${skill.name}${skill.open ? "" : "（未开启）"}`,
        stepper(1, skill.cap, skill.level, (value) => {
          exskills[skill.slot] = value;
          stage("exskills", { ...exskills });
        }),
        ["exskill"],
      ),
    ),
    unit.weapon.cap > 0 && line("专武", stepper(0, unit.weapon.cap, unit.weapon.level, (value) => stage("weapon", value)), ["weapon"]),
    line("潜能点", stepper(0, unit.potential.cap, unit.potential.points, (value) => stage("potential_points", value)), ["potential"]),
    unit.potential.choices.length > 0 && line(`潜能技能（已用 ${unit.potential.used} 点）`, skills),
    unit.skins.length > 1 && line("皮肤", skin),
    line("锁定（防误卖）", h("label", { class: "inline" }, guarded, "锁定")),
  ];

  return h(
    "div",
    {},
    h("h3", {}, h("b", { class: "rarity", "data-rarity": unit.rarity }, unit.rarity), ` ${unit.name}`, h("small", { class: "note" }, ` ${unit.nickname}，卡号 ${unit.card_id}`)),
    h("p", { class: "note" }, "等级上限随觉醒阶级提高；潜能技能的点数不能超过潜能点。调好后点「保存」，右侧「拉满」单独把那一项直接到顶。"),
    h("div", { class: "row" }, saveButton, button("这个角色全部拉满", () => fillUp(ASPECTS.map(([key]) => key)))),
    h("div", { class: "scroll" }, h("table", { class: "sheet unit" }, h("tbody", {}, rows))),
  );
}

async function adder(profile, reload) {
  const { cards } = await api.get("roster/cards", { profile });
  const missing = cards.filter((card) => card.instance_id == null).sort(byRarity);
  const search = h("input", { type: "search", value: browse.pick, placeholder: "名字、称号或编号" });
  const picked = new Set();
  const grid = h("div", { class: "choices" });
  const addButton = button("添加选中的角色", add, "main");

  function draw() {
    const needle = search.value.trim().toLowerCase();
    fill(
      grid,
      missing
        .filter((card) => matches(card, needle))
        .map((card) => {
          const box = h("input", { type: "checkbox", checked: picked.has(card.card_id) });
          box.addEventListener("change", () => {
            if (box.checked) picked.add(card.card_id);
            else picked.delete(card.card_id);
            addButton.textContent = picked.size ? `添加选中的 ${picked.size} 个角色` : "添加选中的角色";
          });
          return h("label", { class: "inline" }, box, h("b", { class: "rarity", "data-rarity": card.rarity }, card.rarity), ` ${card.name}`, h("small", { class: "note" }, ` ${card.nickname}`));
        }),
      !missing.length && h("p", { class: "empty" }, "图鉴里的角色都已经在存档里了。"),
    );
  }

  async function add() {
    if (!picked.size) {
      receipt("先勾选要添加的角色。");
      return;
    }
    const outcome = await attempt(() => api.post("roster/add", { profile, card_ids: [...picked] }));
    if (outcome) await reload(`已添加 ${outcome.added} 个角色。${outcomeText(outcome)}`);
  }

  search.addEventListener("input", () => {
    browse.pick = search.value;
    draw();
  });
  draw();
  return h(
    "details",
    { class: "adder", open: browse.pick !== "" },
    h("summary", {}, `添加角色（还有 ${missing.length} 个没拥有）`),
    h("div", { class: "row" }, field("筛选", search), addButton, h("a", { href: `#shortcuts/${encodeURIComponent(profile)}` }, "全部加入去「一键」页")),
    grid,
  );
}

export const roster = {
  title: "角色",
  async mount(root, argument) {
    const [name, instance] = argument.split("/");
    const chosen = await chosenSave("roster", name);
    if (!chosen) {
      root.append(h("h2", {}, "角色"), h("p", { class: "empty" }, "还没有存档。先在「概览」开始游戏，或在「存档」页新建一个。"));
      return;
    }
    const { profile, picker } = chosen;
    if (browse.profile !== profile) Object.assign(browse, { profile, q: "", pick: "" });
    const { units } = await api.get("roster", { profile });
    const current = instance ? Number(instance) : units.sort(byRarity)[0]?.instance_id;
    const unit = units.find((row) => row.instance_id === current);
    const pane = h("div", {});

    async function reload(text) {
      await roster.mount(fill(root), `${profile}/${current ?? ""}`);
      receipt(text);
    }

    const aspectBoxes = ASPECTS.map(([key, title]) => {
      const box = h("input", { type: "checkbox", checked: true, value: key });
      return h("label", { class: "inline" }, box, title);
    });

    async function fillEveryone() {
      const aspects = aspectBoxes.map((label) => label.firstChild).filter((box) => box.checked).map((box) => box.value);
      if (!aspects.length) {
        receipt("先勾选要拉满的项目。");
        return;
      }
      if (!confirm(`把这个存档里全部 ${units.length} 个角色的 ${aspects.length} 项都拉到顶？`)) return;
      const outcome = await attempt(() => api.post("roster/fill", { profile, aspects }));
      if (outcome) await reload(`已拉满 ${Object.keys(outcome.filled).length} 个角色。${outcomeText(outcome)}`);
    }

    root.append(
      h("h2", {}, "角色"),
      h("p", { class: "note" }, "逐个调整存档里的角色：觉醒、等级、EX 技能、专武、潜能、皮肤。数值只在上限之内，不扣材料。游戏里要回标题重进才会看到。"),
      h("div", { class: "row" }, picker, h("span", { class: "fill-all" }, "全员拉满：", aspectBoxes, button("执行", fillEveryone))),
      h("div", { class: "catalog" }, list(units, profile, current), pane),
      await adder(profile, reload),
    );
    fill(pane, unit ? editor(unit, profile, reload) : h("p", { class: "empty" }, "左边挑一个角色来改，或在下面添加角色。"));
  },
};
