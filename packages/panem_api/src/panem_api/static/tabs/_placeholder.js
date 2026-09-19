// Shared "not built yet" stub for a tab module -- replaced with the real
// implementation as each dashboard milestone lands (see the plan/README).
import { el } from "./_shared.js?v=3";

export function mountPlaceholder(root, title, commands) {
  root.append(
    el(
      "div",
      { class: "tab-placeholder" },
      el("h2", { text: title }),
      el("p", { text: `This tab is coming soon. For now, use ${commands} in Discord.` })
    )
  );
  return {};
}
