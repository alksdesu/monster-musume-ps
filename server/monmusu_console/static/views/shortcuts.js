import { api, attempt, button, fill, h, outcomeText, receipt } from "../app.js";
import { chosenSave } from "./profiles.js";

const RESULTS = {
  units_all: (r) => `新加入 ${r.added} 个角色`,
  units_max: (r) => `拉满 ${r.filled} 个角色`,
  boms_all: (r) => `新加入 ${r.added} 个召唤物`,
  skins_all: (r) => `发放 ${r.given} 件皮肤`,
  subskills_all: (r) => `发放 ${r.given} 种技能插件`,
  stories_all: (r) => `新加入 ${r.added} 个角色，发放 ${r.skins} 件皮肤，拉高 ${r.raised} 个角色的等级，登记 ${r.opened} 段剧情`,
  stories_read: (r) => `标记 ${r.marked} 段剧情为已读`,
  quests_all: (r) => `登记 ${r.cleared} 关通关`,
  missions_all: (r) => `达成 ${r.achieved} 个任务`,
  wallets_max: (r) => `补满 ${r.filled} 种货币`,
  items_max: (r) => `补满 ${r.filled} 种道具`,
  passes_all: (r) => `解锁 ${r.unlocked} 期通行证的付费轨`,
  sheets_all: (r) => `解锁 ${r.unlocked} 张付费签到表`,
  stamina_full: (r) => `补了 ${r.gained} 点体力`,
  player_max: (r) => `玩家等级 ${r.from} → ${r.to}`,
  tutorial_done: (r) => (r.finished ? "教程已标成完成" : "教程本来就完成了"),
};

// Each shortcut shows how much of the save it already covers, so pressing it is never a guess.
function tally(key, status) {
  switch (key) {
    case "units_all":
      return [status.units.owned, status.units.total, "个角色已拥有"];
    case "units_max":
      return [status.units_filled.done, status.units_filled.total, "个角色已拉满"];
    case "boms_all":
      return [status.boms.owned, status.boms.total, "个召唤物已拥有"];
    case "skins_all":
      return [status.skins.held, status.skins.total, "件皮肤已持有"];
    case "subskills_all":
      return [status.subskills.held, status.subskills.total, "种技能插件已持有"];
    case "stories_all":
      return [status.stories.open, status.stories.total, "段剧情已可看"];
    case "stories_read":
      return [status.stories.read, status.stories.total, "段剧情已读"];
    case "quests_all":
      return [status.quests.cleared, status.quests.total, "关已通"];
    case "missions_all":
      return [status.missions.done, status.missions.total, "个任务已达成"];
    case "wallets_max":
      return [status.wallets.full, status.wallets.total, "种货币已满"];
    case "passes_all":
      return [status.passes.done, status.passes.total, "期在期通行证已解锁"];
    case "sheets_all":
      return [status.sheets.done, status.sheets.total, "张付费签到表已解锁"];
    case "stamina_full":
      return [status.stamina.amount, status.stamina.max, "体力"];
    case "player_max":
      return [status.player.rank, status.player.max, "级"];
    case "tutorial_done":
      return [status.tutorial ? 1 : 0, 1, status.tutorial ? "已完成" : "未完成"];
    default:
      return null;
  }
}

function meter(parts) {
  if (!parts) return h("span", { class: "tally" });
  const [done, total, unit] = parts;
  const share = total ? Math.min(1, done / total) : 0;
  return h(
    "span",
    { class: "tally", "data-full": done >= total && total > 0, style: `--share: ${share}` },
    h("span", { class: "tally-ink" }),
    h("span", { class: "tally-text" }, h("strong", {}, String(done)), ` / ${total} ${unit}`),
  );
}

function described(key, result) {
  if (key === "everything") {
    return Object.entries(result)
      .map(([inner, value]) => RESULTS[inner]?.(value))
      .filter(Boolean)
      .join("；");
  }
  return RESULTS[key]?.(result) ?? "已执行";
}

export const shortcuts = {
  title: "一键",
  async mount(root, argument) {
    const chosen = await chosenSave("shortcuts", argument);
    if (!chosen) {
      root.append(h("h2", {}, "一键"), h("p", { class: "empty" }, "还没有存档。先在「概览」开始游戏，或在「存档」页新建一个。"));
      return;
    }
    const { profile, save, picker } = chosen;
    const ledger = h("div", { class: "ledger" });

    async function reload(text) {
      await shortcuts.mount(fill(root), profile);
      receipt(text);
    }

    async function run(entry) {
      if (!confirm(`对「${save.nickname}」执行「${entry.title}」？\n\n${entry.blurb}\n\n执行前会先把全部存档备份一份。`)) return;
      receipt(`正在执行「${entry.title}」。`);
      const outcome = await attempt(() => api.post("shortcuts/run", { profile, key: entry.key }));
      if (outcome) await reload(`${entry.title}：${described(entry.key, outcome.result)}。备份在 backups/${outcome.backup}。${outcomeText(outcome)}`);
    }

    function draw({ catalog, status }) {
      fill(
        ledger,
        catalog.map((entry) =>
          h(
            "div",
            { class: "entry", "data-all": entry.key === "everything" },
            h("div", { class: "entry-text" }, h("strong", {}, entry.title), h("p", {}, entry.blurb), h("small", { class: "note" }, `会改：${entry.touches}`)),
            meter(tally(entry.key, status)),
            button("执行", () => run(entry), entry.key === "everything" ? "main" : undefined),
          ),
        ),
      );
    }

    root.append(
      h("h2", {}, "一键"),
      h("p", { class: "note" }, "对一个存档整批地改。每一项都写清会碰到什么，右边的刻度是现在已经到哪了；点「执行」前会自动备份全部存档，游戏里要回标题重进才会看到。"),
      h("div", { class: "row" }, picker, h("a", { href: `#roster/${encodeURIComponent(profile)}` }, "逐个调角色去「角色」页"), h("a", { href: `#player/${encodeURIComponent(profile)}` }, "改货币和发奖去「玩家」页")),
      ledger,
    );
    draw(await api.get("shortcuts", { profile }));
  },
};
