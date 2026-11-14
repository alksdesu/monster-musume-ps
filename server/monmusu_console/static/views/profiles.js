import { api, field, h } from "../app.js";

// Every page that edits one save chooses it the same way and remembers it in the address.
export async function chosenSave(view, argument) {
  const { saves } = await api.get("saves");
  if (!saves.length) return null;
  const found = saves.find((row) => row.profile === argument) ?? saves[0];
  const chooser = h(
    "select",
    { "aria-label": "正在编辑的存档" },
    saves.map((row) => h("option", { value: row.profile, selected: row === found }, `${row.nickname}（${row.profile}）`)),
  );
  chooser.addEventListener("change", () => (location.hash = `${view}/${encodeURIComponent(chooser.value)}`));
  return { profile: found.profile, save: found, picker: field("存档", chooser) };
}
