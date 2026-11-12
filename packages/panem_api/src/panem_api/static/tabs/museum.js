// The "Museum" tab -- donate collectibles found via hobbies/events,
// browse the result by wing, and re-organize it by hand or by preset
// (mirrors `/museum donate|organize|preset`, `panem_shared.museum`).
// Viewing *another* character's museum stays a Discord-only `/museum
// view` feature for now -- this tab only ever shows your own.
import { dropdown, el, setStatusText } from "./_shared.js?v=7";

function undonatedRow(ctx, item, resultLine, onChanged) {
  const donateBtn = el("button", { class: "btn", type: "button" }, "Donate");
  donateBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const result = await ctx.apiFetch(`/activity/dashboard/museum/${ctx.characterId()}/donate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), instance_id: item.instance_id }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = `Donated ${result.name} -- received ${result.reward}. It's part of the collection for good now.`;
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });
  return el(
    "tr",
    {},
    el("td", { text: `${item.name} (${item.rarity})` }),
    el("td", { text: `found ${item.source}` }),
    el("td", {}, donateBtn)
  );
}

function slotRow(ctx, slot, resultLine, onChanged) {
  const wingInput = el("input", { type: "text", value: slot.wing, maxlength: "32" });
  const positionInput = el("input", { type: "number", value: String(slot.position) });
  const moveBtn = el("button", { class: "btn secondary", type: "button" }, "Move");
  moveBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      await ctx.apiFetch(`/activity/dashboard/museum/${ctx.characterId()}/organize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          slot_id: slot.slot_id,
          wing: wingInput.value || slot.wing,
          position: Number(positionInput.value || 0),
        }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = `Moved ${slot.name}.`;
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });
  return el(
    "tr",
    {},
    el("td", { text: `${slot.name} (${slot.rarity})` }),
    el("td", {}, wingInput),
    el("td", {}, positionInput),
    el("td", {}, moveBtn)
  );
}

export function mount(root, ctx) {
  const statusLine = el("p", { class: "tab-status" });
  const resultLine = el("p", { class: "result-line" });
  const host = el("div", {});
  root.append(statusLine, host, resultLine);

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    host.innerHTML = "";
    if (!characterId || !discordId) {
      setStatusText(statusLine, "Pick a character above first.");
      return;
    }
    try {
      const status = await ctx.apiFetch(
        `/activity/dashboard/museum/${characterId}/status?discord_id=${encodeURIComponent(discordId)}`
      );
      setStatusText(statusLine, "");

      const presetSelect = dropdown(
        status.presets.map((p) => ({ value: p, label: p.replace(/_/g, " ") }))
      );
      const presetBtn = el("button", { class: "btn secondary", type: "button" }, "Apply preset");
      presetBtn.addEventListener("click", async () => {
        resultLine.textContent = "";
        try {
          await ctx.apiFetch(`/activity/dashboard/museum/${characterId}/preset`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ discord_id: discordId, preset: presetSelect.value }),
          });
          resultLine.className = "result-line win";
          resultLine.textContent = `Museum organized ${presetSelect.value.replace(/_/g, " ")}.`;
          await refresh();
        } catch (err) {
          resultLine.className = "result-line lose";
          resultLine.textContent = err.message;
        }
      });

      const collectionPanel = el(
        "div",
        { class: "panel" },
        el("h2", { text: "Your Museum" }),
        el("div", { class: "field-row" }, el("label", { text: "Organize by" }), presetSelect, presetBtn),
        status.slots.length
          ? el(
              "table",
              { class: "data-table" },
              el(
                "thead",
                {},
                el(
                  "tr",
                  {},
                  el("th", { text: "Piece" }),
                  el("th", { text: "Wing" }),
                  el("th", { text: "Position" }),
                  el("th", { text: "" })
                )
              ),
              el(
                "tbody",
                {},
                ...status.slots.map((slot) => slotRow(ctx, slot, resultLine, refresh))
              )
            )
          : el("p", { class: "tab-status" }, "Nothing donated yet.")
      );

      const undonatedPanel = el(
        "div",
        { class: "panel" },
        el("h2", { text: "Undonated Finds" }),
        status.undonated.length
          ? el(
              "table",
              { class: "data-table" },
              el("tbody", {}, ...status.undonated.map((item) => undonatedRow(ctx, item, resultLine, refresh)))
            )
          : el("p", { class: "tab-status" }, "Nothing waiting to be donated right now.")
      );

      host.append(collectionPanel, undonatedPanel);
    } catch (err) {
      setStatusText(statusLine, `Could not load the museum: ${err.message}`, { error: true });
    }
  }

  refresh();

  return {};
}
