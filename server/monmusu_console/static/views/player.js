import { api, attempt, button, field, fill, h, outcomeText, receipt, table } from "../app.js";
import { chosenSave } from "./profiles.js";

const DELIVERIES = { inbox: "放进礼物箱", direct: "直接入账，超出上限的部分进礼物箱" };

function rewardRow(remove) {
  const rid = h("input", { type: "text", list: "resources", size: 34, placeholder: "wallet.point.gold" });
  const amount = h("input", { type: "number", min: 1, value: 1 });
  const row = h("div", { class: "row" }, field("资源", rid), field("数量", amount), button("去掉这一项", () => remove(row)));
  row.order = () => ({ rid: rid.value.trim(), val: Number(amount.value) });
  return row;
}

export const player = {
  title: "玩家",
  async mount(root, argument) {
    const chosen = await chosenSave("player", argument);
    if (!chosen) {
      root.append(h("h2", {}, "玩家"), h("p", { class: "empty" }, "还没有存档可以编辑。"));
      return;
    }
    const { profile, picker } = chosen;
    const profiles = (await api.get("saves")).saves.map((row) => row.profile);
    const [summary, resources] = await Promise.all([api.get("player", { profile }), api.get("resources")]);

    async function reload(text) {
      await player.mount(fill(root), profile);
      receipt(text);
    }

    async function setBalance(row, input) {
      const body = { profile, rid: `wallet.${row.category}.${row.name}`, value: Number(input.value) };
      const outcome = await attempt(() => api.post("player/balance", body));
      if (outcome) reload(outcomeText(outcome));
    }

    const nickname = h("input", { type: "text", size: 20, value: summary.player.nickname });

    async function rename() {
      const outcome = await attempt(() => api.post("player/rename", { profile, nickname: nickname.value }));
      if (outcome) reload(`已改名为 ${nickname.value.trim()}。${outcomeText(outcome)}`);
    }

    const rewards = h("div", {});
    const addReward = () => rewards.append(rewardRow((row) => rewards.children.length > 1 && row.remove()));
    addReward();
    const delivery = h("select", {}, Object.entries(DELIVERIES).map(([value, text]) => h("option", { value }, text)));
    const message = h("input", { type: "text", size: 40, placeholder: "礼物箱里显示的说明，可不填" });
    const everyone = h("input", { type: "checkbox" });

    async function send() {
      const body = {
        profiles: everyone.checked ? profiles : [profile],
        orders: [...rewards.children].map((row) => row.order()),
        delivery: delivery.value,
        message: message.value,
      };
      const outcomes = await attempt(() => api.post("player/grant", body));
      if (outcomes) reload(`已发给 ${Object.keys(outcomes).length} 个存档。${outcomeText(outcomes[body.profiles[0]])}`);
    }

    const editor = h("textarea", { spellcheck: "false", "aria-label": "原始数据" });
    const section = h(
      "select",
      {},
      summary.sections.map((row) =>
        h("option", { value: row.name }, `${row.name}${row.size == null ? "" : `（${row.size} 项）`}${row.synced ? "" : "，不走同步"}`),
      ),
    );

    async function openSection() {
      const loaded = await attempt(() => api.get("player/section", { profile, name: section.value }));
      if (loaded) editor.value = JSON.stringify(loaded.value, null, 2);
    }

    async function saveSection() {
      let value;
      try {
        value = JSON.parse(editor.value);
      } catch (error) {
        receipt(`这不是合法的 JSON：${error.message}`, "error");
        return;
      }
      const outcome = await attempt(() => api.post("player/section", { profile, name: section.value, value }));
      if (outcome) receipt(outcomeText(outcome));
    }

    section.addEventListener("change", openSection);
    root.append(
      h("h2", {}, `${summary.player.nickname}（${summary.player.player_code}）`),
      h("div", { class: "row" }, picker, h("a", { href: `#roster/${encodeURIComponent(profile)}` }, "角色去「角色」页"), h("a", { href: `#shortcuts/${encodeURIComponent(profile)}` }, "整批改去「一键」页")),
      h("h3", {}, "昵称"),
      h("div", { class: "row" }, field("游戏里显示的名字", nickname), button("改名", rename)),
      h("h3", {}, "持有的货币与券"),
      table(
        [
          { title: "资源", cell: (row) => `wallet.${row.category}.${row.name}` },
          { title: "数量", cell: (row) => (row.input = h("input", { type: "number", min: 0, value: row.val })) },
          { title: "", cell: (row) => button("改成这个数", () => setBalance(row, row.input)) },
        ],
        summary.wallets,
        "这个存档还没有任何货币记录，可以在下面发放。",
      ),
      h("h3", {}, "发放奖励"),
      h("datalist", { id: "resources" }, resources.rows.map((row) => h("option", { value: row.rid }, row.name))),
      rewards,
      h("div", { class: "row" }, button("再加一项", addReward)),
      h("div", { class: "row" }, field("送达方式", delivery), field("说明", message), h("label", { class: "inline" }, everyone, "发给全部存档")),
      h("div", { class: "row" }, button("发放", send, "main")),
      h("h3", {}, "原始数据"),
      h("p", { class: "note" }, "直接改存档里的一整块数据。只检查它是不是合法 JSON、类型有没有变，内容对不对要自己把关；改之前建议先在「存档」页备份。"),
      h("div", { class: "row" }, field("数据块", section), button("保存这一块", saveSection, "main")),
      editor,
    );
    await openSection();
  },
};
