import { api, ApiError, attempt, button, field, fill, h, receipt, refreshMarks, table, when } from "../app.js";

const CONFLICT = 409;

function editor({ route, title, heading, intro, listing, base, saved, history }) {
  return {
    title,
    async mount(root, argument) {
      const files = (await api.get(listing)).files;
      const name = files.includes(argument) ? argument : files[0];
      const chooser = h("select", {}, files.map((file) => h("option", { selected: file === name }, file)));
      chooser.addEventListener("change", () => (location.hash = `${route}/${chooser.value}`));
      const text = h("textarea", { spellcheck: "false", "aria-label": name });
      const versions = h("div", {});
      const problem = h("p", { class: "problem" });

      function draw(opened, done) {
        text.value = opened.text;
        problem.textContent = opened.error ? `磁盘上的这份文件没能生效，服务器在用上一份：${opened.error}` : "";
        fill(
          versions,
          table(
            [
              { title: "保存前留下的版本", cell: (row) => row.version },
              { title: "留存时间", cell: (row) => when(row.modified) },
              { title: "大小", number: true, cell: (row) => `${row.size} 字节` },
              {
                title: "",
                cell: (row) => [button("载入到编辑框", () => load(row.version)), " ", button("直接回退", () => restore(row.version))],
              },
            ],
            opened.versions,
            history,
          ),
        );
        if (done) receipt(done);
        refreshMarks();
      }

      async function load(version) {
        const kept = await attempt(() => api.get(`${base}/version`, { name, version }));
        if (kept) {
          text.value = kept.text;
          receipt(`已把 ${version} 的内容放进编辑框，保存后才生效。`);
        }
      }

      async function restore(version) {
        if (!confirm(`把 ${name} 回退到 ${version}？当前内容会先留一份。`)) return;
        const opened = await attempt(() => api.post(`${base}/restore`, { name, version }));
        if (opened) draw(opened, `已回退到 ${version}。`);
      }

      async function save(allowMissing) {
        try {
          draw(await api.post(base, { name, text: text.value, allow_missing: allowMissing }), saved);
        } catch (error) {
          const removed = error instanceof ApiError && error.status === CONFLICT ? error.body.missing : null;
          if (removed && confirm(`这次保存会删掉这些键：\n${removed.join("\n")}\n确定它们不再需要吗？`)) await save(true);
          else receipt(removed ? "没有保存。" : error.message, "error");
        }
      }

      root.append(
        h("h2", {}, heading),
        h("p", {}, intro),
        h("div", { class: "row" }, field("文件", chooser), button("保存", () => save(false), "main")),
        problem,
        text,
        h("h3", {}, "历史版本"),
        versions,
      );
      draw(await api.get(base, { name }));
    },
  };
}

export const configs = editor({
  route: "configs",
  title: "配置",
  heading: "配置文件",
  intro: "运营规则都在这几份文件里。保存前会检查 JSON 是否合法、各项的类型有没有变；排期和内容覆盖还会按各自的规则整份校验。监听地址和端口改了以后要重启服务器。",
  listing: "configs",
  base: "config",
  saved: "已保存，下一次请求起生效。",
  history: "每次保存前会把旧文件留一份在 backups/config 里，目前还没有。",
});

export const shared = editor({
  route: "shared",
  title: "共享文件",
  heading: "存档之间共用的文件",
  intro: "平台账号与余额、好友关系、数据连携的绑定和登录凭据不属于任何一个存档，放在存档目录的这几份文件里。保存前只检查 JSON 是否合法、各项的类型有没有变；改之前建议先在「存档」页备份。",
  listing: "shared-files",
  base: "shared-file",
  saved: "已保存。好友关系立即生效，其余的在下一次读取时生效。",
  history: "每次保存前会把旧文件留一份在 backups/shared 里，目前还没有。",
});
