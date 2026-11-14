import { api, attempt, button, field, fill, h, table, when } from "../app.js";

export const saves = {
  title: "存档",
  async mount(root) {
    async function reload(done) {
      const listed = await attempt(() => api.get("saves"), done);
      if (listed) draw(listed);
    }

    async function rebind(row, profiles) {
      const others = profiles.filter((profile) => profile !== row.profile);
      const target = prompt(
        `让登录身份 ${row.profile} 进入哪个存档？可选：${others.join("、") || "没有别的存档"}。填它自己则恢复原样。`,
        row.rebound_to ?? row.profile,
      );
      if (!target) return;
      const bound = await attempt(() => api.post("saves/bind", { login: row.profile, profile: target }));
      if (bound) reload(`登录身份 ${row.profile} 现在进入 ${target}。在线的客户端需要回标题重新登录。`);
    }

    async function create(input) {
      const profile = input.value.trim();
      if (await attempt(() => api.post("saves/create", { profile }))) reload(`已新建存档 ${profile}。用这个登录身份开始游戏就会进入它。`);
    }

    async function kick(row) {
      if (await attempt(() => api.post("saves/kick", { profile: row.profile }))) reload(`已让 ${row.nickname} 下线，客户端回标题重新登录即可再进。`);
    }

    async function remove(row) {
      if (!confirm(`删除 ${row.nickname}（${row.player_code}）的存档？删除前会先自动备份全部存档。`)) return;
      const removed = await attempt(() => api.post("saves/delete", { profile: row.profile }));
      if (removed) reload(`已删除。删除前的全部存档留在备份 ${removed.kept} 里。`);
    }

    async function restore(backup, save) {
      if (!confirm(`用备份 ${backup.name} 里的 ${save.nickname}（${save.player_code}）覆盖当前存档？覆盖前会先自动备份一次。`)) return;
      const kept = await attempt(() => api.post("saves/restore", { backup: backup.name, folder: save.folder }));
      if (kept) reload(`已还原。还原前的存档留在备份 ${kept.kept} 里；在线的客户端需要回标题重新登录。`);
    }

    function draw(listed) {
      const profiles = listed.saves.map((row) => row.profile);
      const label = h("input", { type: "text", placeholder: "可不填", maxlength: 32 });
      const fresh = h("input", { type: "text", placeholder: "dmm:second", size: 24 });
      fill(root, h("h2", {}, "存档"),
        table(
          [
            { title: "昵称", cell: (row) => row.nickname },
            { title: "玩家编号", cell: (row) => row.player_code },
            { title: "登录身份", cell: (row) => row.profile },
            {
              title: "进入这个存档的其他身份",
              cell: (row) => (row.rebound_to ? `自己已改去 ${row.rebound_to}` : row.logins.join("、") || "无"),
            },
            { title: "教程进度", cell: (row) => String(row.tutorial) },
            { title: "最后保存", cell: (row) => when(row.modified) },
            { title: "在线", cell: (row) => (row.online ? "在线" : "") },
            {
              title: "",
              cell: (row) => [
                h("a", { href: `#player/${encodeURIComponent(row.profile)}` }, "编辑"),
                " ",
                button("改绑", () => rebind(row, profiles)),
                " ",
                row.online && button("踢下线", () => kick(row)),
                " ",
                button("删除", () => remove(row), "danger"),
              ],
            },
          ],
          listed.saves,
          "还没有存档。在下面新建一个，或者直接开始游戏，第一次登录时会自动生成。",
        ),
        h(
          "div",
          { class: "row" },
          field("新存档的登录身份（dmm: 加字母、数字、点、- 或 _）", fresh),
          button("新建存档", () => create(fresh)),
        ),
        h("h2", {}, "备份"),
        h(
          "div",
          { class: "row" },
          field("备注（字母、数字、- 和 _）", label),
          button(
            "立即备份全部存档",
            async () => {
              const made = await attempt(() => api.post("saves/backup", { label: label.value }));
              if (made) reload(`已备份到 backups/${made.name}。`);
            },
            "main",
          ),
        ),
        table(
          [
            { title: "备份", cell: (backup) => backup.name },
            { title: "时间", cell: (backup) => when(backup.created) },
            {
              title: "包含的存档",
              cell: (backup) =>
                backup.saves.map((save) =>
                  h(
                    "div",
                    {},
                    `${save.nickname}（${save.player_code}，结构版本 ${save.schema_version}） `,
                    button("还原这个存档", () => restore(backup, save)),
                  ),
                ),
            },
          ],
          listed.backups,
          "backups 目录里还没有含存档的备份。",
        ),
      );
    }

    draw(await api.get("saves"));
  },
};
