// The "Bulletin" tab -- the district board: system announcements
// (weather, global events, rumors -- posted by `panem_bot.cogs.weather`'s
// poll, `panem_bot.cogs.rumors`'s spawn loop, `/staff event create|
// conclude`) alongside player/NPC posts, with per-viewer interest
// marking, a DM reminder, and drag-free "move to position" organizing
// (mirrors `/bulletin ...`, `panem_shared.bulletin`).
import { el, setStatusText } from "./_shared.js?v=7";

function entryRow(ctx, entry, resultLine, onChanged) {
  const interestBtn = el(
    "button",
    { class: "btn secondary", type: "button" },
    entry.interested ? "Remove interest" : "Mark interested"
  );
  interestBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      await ctx.apiFetch(`/activity/dashboard/bulletin/${ctx.characterId()}/interest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          entry_id: entry.entry_id,
          interested: !entry.interested,
        }),
      });
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  const children = [
    el("h2", { text: `${entry.title} (${entry.tag})` }),
    entry.body ? el("p", { class: "tab-status" }, entry.body) : null,
    entry.requirements ? el("p", { class: "tab-status" }, `Requires: ${entry.requirements}`) : null,
    entry.danger_level != null
      ? el("p", { class: "tab-status" }, `Danger: category ${entry.danger_level}`)
      : null,
    entry.event_tick != null
      ? el("p", { class: "tab-status" }, `Happens around tick ${entry.event_tick}.`)
      : null,
  ].filter(Boolean);

  const actions = [interestBtn];

  if (entry.event_tick != null) {
    const remindBtn = el("button", { class: "btn secondary", type: "button" }, "Remind me");
    remindBtn.addEventListener("click", async () => {
      resultLine.textContent = "";
      try {
        await ctx.apiFetch(`/activity/dashboard/bulletin/${ctx.characterId()}/remind`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: ctx.discordId(), entry_id: entry.entry_id, lead_ticks: 1 }),
        });
        resultLine.className = "result-line win";
        resultLine.textContent = `You'll get a DM about "${entry.title}".`;
      } catch (err) {
        resultLine.className = "result-line lose";
        resultLine.textContent = err.message;
      }
    });
    actions.push(remindBtn);
  }

  if (entry.tag === "Player hosted" || entry.tag === "NPC hosted") {
    const retractBtn = el("button", { class: "btn secondary", type: "button" }, "Retract");
    retractBtn.addEventListener("click", async () => {
      resultLine.textContent = "";
      try {
        await ctx.apiFetch(`/activity/dashboard/bulletin/${ctx.characterId()}/retract`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: ctx.discordId(), entry_id: entry.entry_id }),
        });
        resultLine.className = "result-line win";
        resultLine.textContent = "Entry retracted.";
        onChanged();
      } catch (err) {
        resultLine.className = "result-line lose";
        resultLine.textContent = err.message;
      }
    });
    actions.push(retractBtn);
  }

  children.push(el("div", { class: "field-row" }, ...actions));
  return el("div", { class: "panel" }, ...children);
}

function postPanel(ctx, resultLine, onChanged) {
  const titleInput = el("input", { type: "text", maxlength: "120", placeholder: "Title" });
  const bodyInput = el("textarea", { rows: "3", placeholder: "Details" });
  const eventTickInput = el("input", { type: "number", placeholder: "When it happens (tick), optional" });
  const requirementsInput = el("input", { type: "text", placeholder: "Requirements, optional" });
  const npcHostedInput = el("input", { type: "checkbox" });
  const worldWideInput = el("input", { type: "checkbox" });

  const postBtn = el("button", { class: "btn", type: "button" }, "Post");
  postBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    if (!titleInput.value.trim()) {
      resultLine.className = "result-line lose";
      resultLine.textContent = "A bulletin post needs a title.";
      return;
    }
    try {
      await ctx.apiFetch(`/activity/dashboard/bulletin/${ctx.characterId()}/post`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          title: titleInput.value.trim(),
          body: bodyInput.value.trim(),
          event_tick: eventTickInput.value ? Number(eventTickInput.value) : null,
          requirements: requirementsInput.value.trim() || null,
          npc_hosted: npcHostedInput.checked,
          world_wide: worldWideInput.checked,
        }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = "Posted to the bulletin.";
      titleInput.value = "";
      bodyInput.value = "";
      eventTickInput.value = "";
      requirementsInput.value = "";
      npcHostedInput.checked = false;
      worldWideInput.checked = false;
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Post Something" }),
    el("div", { class: "field-row" }, el("label", { text: "Title" }), titleInput),
    el("div", { class: "field-row" }, el("label", { text: "Details" }), bodyInput),
    el("div", { class: "field-row" }, el("label", { text: "When" }), eventTickInput),
    el("div", { class: "field-row" }, el("label", { text: "Requirements" }), requirementsInput),
    el("div", { class: "field-row" }, el("label", { text: "NPC hosted?" }), npcHostedInput),
    el("div", { class: "field-row" }, el("label", { text: "World-wide?" }), worldWideInput),
    postBtn
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
      const board = await ctx.apiFetch(
        `/activity/dashboard/bulletin/${characterId}/board?discord_id=${encodeURIComponent(discordId)}`
      );
      setStatusText(statusLine, "");
      const entryPanels = board.entries.length
        ? board.entries.map((entry) => entryRow(ctx, entry, resultLine, refresh))
        : [
            el(
              "div",
              { class: "panel" },
              el("h2", { text: "Board" }),
              el("p", { class: "tab-status" }, "Nothing on the board right now.")
            ),
          ];
      host.append(postPanel(ctx, resultLine, refresh), ...entryPanels);
    } catch (err) {
      setStatusText(statusLine, `Could not load the bulletin: ${err.message}`, { error: true });
    }
  }

  refresh();

  return {};
}
