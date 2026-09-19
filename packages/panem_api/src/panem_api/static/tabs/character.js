// The "Character" tab: list/create/edit/retire, mirroring `/character
// list|create|avatar|tag|retire`. Character *creation* here writes the DB
// row directly and waits on panem_bot's `_announce_pending_characters`
// background poll to post the staff-approval embed (panem_api has no bot
// token to post it itself) -- see dashboard_routes.py's build_characters_
// router docstring. District is a plain select here rather than inferred
// from a Discord guild role, the one deliberate simplification from the
// Discord flow.
import { fetchJson, el, dropdown } from "./_shared.js?v=3";

const SHIFT_PHASES = ["morning", "afternoon", "evening", "night"];

function characterCard(ctx, character, { onChanged }) {
  const statusLine = el(
    "p",
    { class: "tab-status" },
    `${character.status}${character.jailed_until_tick ? " -- jailed" : ""}`
  );
  const avatarInput = el("input", { type: "text", value: character.avatar_url || "", placeholder: "https://..." });
  const tagInput = el("input", { type: "text", value: character.proxy_tag || "", placeholder: "tag::" });
  const resultLine = el("p", { class: "result-line" });

  const saveBtn = el("button", { class: "btn secondary", type: "button" }, "Save");
  saveBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      await ctx.apiFetch(`/activity/dashboard/characters/${character.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          avatar_url: avatarInput.value || null,
          proxy_tag: tagInput.value || null,
        }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = "Saved.";
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  const retireBtn = el("button", { class: "btn secondary", type: "button" }, "Retire");
  retireBtn.hidden = character.status !== "approved";
  retireBtn.addEventListener("click", async () => {
    if (!confirm(`Retire ${character.name}? This can't be undone.`)) return;
    try {
      await ctx.apiFetch(`/activity/dashboard/characters/${character.id}/retire`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId() }),
      });
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: `${character.name} (${character.district_name})` }),
    statusLine,
    el("p", { class: "tab-status" }, `${character.job_title || "no job"} -- ${character.money} money`),
    el("div", { class: "field-row" }, el("label", { text: "Avatar URL" }), avatarInput),
    el("div", { class: "field-row" }, el("label", { text: "Proxy tag" }), tagInput),
    el("div", { class: "field-row" }, saveBtn, retireBtn),
    resultLine
  );
}

function createForm(ctx, { onCreated }) {
  const nameInput = el("input", { type: "text", maxlength: "32" });
  const ageInput = el("input", { type: "number", value: "16", min: "12", max: "99" });
  const districtInput = el("input", { type: "number", value: "1", min: "0", max: "12" });
  const jobInput = el("input", { type: "text", maxlength: "80", placeholder: "Miner, Baker, ..." });
  const phaseSelect = dropdown(SHIFT_PHASES.map((phase) => ({ value: phase, label: phase })));
  const illicitInput = el("input", { type: "checkbox" });
  const appearanceInput = el("textarea", { rows: "2", maxlength: "400" });
  const backstoryInput = el("textarea", { rows: "3", maxlength: "1500" });
  const resultLine = el("p", { class: "result-line" });

  const submitBtn = el("button", { class: "btn", type: "button" }, "Create character");
  submitBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      await ctx.apiFetch("/activity/dashboard/characters", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          district_id: Number(districtInput.value),
          name: nameInput.value,
          age: Number(ageInput.value),
          appearance: appearanceInput.value,
          backstory: backstoryInput.value,
          job_title: jobInput.value,
          shift_phase: phaseSelect.value,
          job_is_illicit: illicitInput.checked,
        }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = "Application submitted -- awaiting staff approval.";
      nameInput.value = "";
      appearanceInput.value = "";
      backstoryInput.value = "";
      onCreated();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "New character" }),
    el("div", { class: "field-row" }, el("label", { text: "Name" }), nameInput),
    el("div", { class: "field-row" }, el("label", { text: "Age" }), ageInput),
    el("div", { class: "field-row" }, el("label", { text: "District" }), districtInput),
    el("div", { class: "field-row" }, el("label", { text: "Job title" }), jobInput),
    el("div", { class: "field-row" }, el("label", { text: "Shift" }), phaseSelect),
    el("div", { class: "field-row" }, el("label", { text: "Illicit job?" }), illicitInput),
    el("div", { class: "field-row" }, el("label", { text: "Appearance" }), appearanceInput),
    el("div", { class: "field-row" }, el("label", { text: "Backstory" }), backstoryInput),
    submitBtn,
    resultLine
  );
}

export function mount(root, ctx) {
  const listEl = el("div", {});
  const statusEl = el("p", { class: "tab-status" });

  async function refresh() {
    listEl.innerHTML = "";
    const discordId = ctx.discordId();
    if (!discordId) {
      statusEl.textContent = "Enter a Discord ID above to see your characters.";
      return;
    }
    statusEl.textContent = "Loading…";
    try {
      const body = await ctx.apiFetch(
        `/activity/dashboard/characters?discord_id=${encodeURIComponent(discordId)}`
      );
      statusEl.textContent = "";
      for (const character of body.characters) {
        listEl.append(characterCard(ctx, character, { onChanged: refresh }));
      }
      if (body.characters.length === 0) {
        listEl.append(el("p", { class: "tab-status" }, "No characters yet."));
      }
    } catch (err) {
      statusEl.textContent = `Could not load characters: ${err.message}`;
    }
  }

  root.append(statusEl, listEl, createForm(ctx, { onCreated: refresh }));
  refresh();

  return {};
}
