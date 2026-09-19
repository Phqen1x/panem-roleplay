// The "Staff" tab: only offered by `app.js` when `/identify` reports
// `is_staff` -- currently just the dashboard equivalent of `/staff jail`.
// Acts on any character by exact name (not the logged-in player's own
// characters -- `ctx.characters()` doesn't apply here), same as the bot
// command's own `character:` field. Every write is re-checked server-side
// (`build_staff_router`'s `_require_staff`) regardless of what `/identify`
// said, so this tab being reachable is convenience, not the actual gate.
import { fetchJson, el } from "./_shared.js?v=3";

export function mount(root, ctx) {
  const resultLine = el("p", { class: "result-line" });
  const nameInput = el("input", { type: "text", placeholder: "Character name" });
  const ticksInput = el("input", { type: "number", value: "50", min: "1" });
  const reasonInput = el("input", { type: "text", placeholder: "Optional reason" });
  const jailBtn = el("button", { class: "btn", type: "button" }, "Jail");

  jailBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    const characterName = nameInput.value.trim();
    const ticks = Number(ticksInput.value);
    if (!characterName || !Number.isFinite(ticks) || ticks < 1) {
      resultLine.className = "result-line lose";
      resultLine.textContent = "Enter a character name and a positive tick count.";
      return;
    }
    try {
      const body = await fetchJson("/activity/dashboard/staff/jail", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          character_name: characterName,
          ticks,
          reason: reasonInput.value.trim() || null,
        }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent =
        `${body.character_name} jailed for ${body.applied_ticks} ticks ` +
        `(until tick ${body.jailed_until_tick}).`;
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  root.append(
    el(
      "div",
      { class: "panel" },
      el("h2", { text: "Jail a character" }),
      el(
        "div",
        { class: "field-row" },
        el("label", { text: "Character" }),
        nameInput
      ),
      el(
        "div",
        { class: "field-row" },
        el("label", { text: "Ticks" }),
        ticksInput
      ),
      el(
        "div",
        { class: "field-row" },
        el("label", { text: "Reason" }),
        reasonInput
      ),
      el("div", { class: "field-row" }, jailBtn),
      resultLine
    )
  );
}
