import { api, attempt, button, fill, h, login, order, watchStatus, when } from "../app.js";

const RUNNING = "running";

function count(number) {
  return number.toLocaleString("zh-CN");
}

function library(row) {
  if (!row) return h("p", { class: "problem" }, "译文文件读不出来。");
  return h(
    "dl",
    {},
    h("dt", {}, "界面与说明文字"),
    h("dd", {}, `${count(row.texts)} 条`),
    h("dt", {}, "剧情对话"),
    h("dd", {}, `${count(row.lines)} / ${count(row.all_lines)} 行，${count(row.scenes)} 场`),
    h("dt", {}, "译文更新于"),
    h("dd", {}, when(row.updated_at)),
  );
}

export const translation = {
  title: "翻译",
  async mount(root) {
    const box = h("section", {});
    const restart = h("div", { class: "row" });
    let gameRunning = false;

    function drawRestart() {
      fill(
        restart,
        gameRunning &&
          button("重启游戏，让改动生效", () =>
            order("client", { action: "restart", viewer: login.name, shipped_dates: login.shippedDates }, "正在重启游戏。", "游戏已重新启动。"),
          ),
      );
    }

    async function draw() {
      const spoken = await attempt(() => api.get("language"));
      if (!spoken) return;
      const chosen = spoken.choices.find((row) => row.code === spoken.current) ?? null;
      const offered = spoken.choices[0] ?? null;

      async function choose(code, done) {
        const reply = await attempt(() => api.post("language", { code }), done);
        if (reply) await draw();
      }

      fill(
        box,
        h("h2", {}, "翻译"),
        !offered && h("p", { class: "problem" }, "localization/ 下没有译文文件，游戏只能显示日文。"),
        offered &&
          h(
            "div",
            { class: "row" },
            chosen
              ? button("切回日文原文", () => choose(null, "已切回日文原文。重开游戏后生效。"))
              : button(`全部翻译成${offered.name}`, () => choose(offered.code, `已切到${offered.name}。重开游戏后，界面、说明、角色名和剧情全部显示译文。`), "main"),
          ),
        offered && h("p", { class: "note" }, chosen ? `游戏现在显示${chosen.name}。` : "游戏现在显示日文原文。"),
        offered && spoken.libraries && h("h3", {}, "译文库"),
        offered && spoken.libraries && library(spoken.libraries[(chosen ?? offered).code]),
        h(
          "p",
          { class: "note" },
          "译文由接入插件在游戏启动时拉取、在内存里替换，存档和服务器数据始终是日文；所以开关后要重开游戏才看得到。界面上缺的字由系统字体补齐，烤进图片里的按钮文字仍是日文。",
        ),
      );
    }

    root.append(box, restart);
    await draw();
    const unwatch = watchStatus((app) => {
      gameRunning = app.client.state === RUNNING;
      drawRestart();
    });
    const app = await api.get("app").catch(() => null);
    if (app) {
      gameRunning = app.client.state === RUNNING;
      drawRestart();
    }
    return unwatch;
  },
};
