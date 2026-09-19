// The "Social" tab: two read-only panels sharing one screen --
// `/resident list|where|profile` (Residents) and a character's current
// `/talk`/`/engage`/`/scene` thread, if any (Engagement).
//
// The Engagement panel is deliberately read-only + a "Continue in
// Discord" deep link, not a full chat UI -- per the user's own choice
// when this feature was scoped: making it fully interactive here would
// need a dashboard -> Redis -> panem_bot relay (only the bot process
// holds a token and can post/create Discord threads), which is out of
// scope for this pass.
import { fetchJson, el } from "./_shared.js?v=3";

function engagementPanel(ctx) {
  const statusEl = el("p", { class: "tab-status" });
  const detailEl = el("div", {});
  const panel = el("div", { class: "panel" }, el("h2", { text: "Current Engagement" }), statusEl, detailEl);

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    detailEl.innerHTML = "";
    if (!characterId || !discordId) {
      statusEl.textContent = "Pick a character above first.";
      return;
    }
    try {
      const status = await ctx.apiFetch(
        `/activity/dashboard/social/${characterId}?discord_id=${encodeURIComponent(discordId)}`
      );
      if (!status.in_scene) {
        statusEl.textContent = "Not currently in a scene or engagement.";
        return;
      }
      statusEl.textContent = "";
      const lines = [
        el("p", {}, el("strong", { text: status.scene_title })),
        el("p", { class: "tab-status" }, `Kind: ${status.scene_kind}`),
      ];
      if (status.location_name) {
        lines.push(el("p", { class: "tab-status" }, `Location: ${status.location_name}`));
      }
      if (status.participant_character_names.length > 0) {
        lines.push(
          el(
            "p",
            { class: "tab-status" },
            `Characters: ${status.participant_character_names.join(", ")}`
          )
        );
      }
      if (status.participant_npc_names.length > 0) {
        lines.push(
          el("p", { class: "tab-status" }, `Residents: ${status.participant_npc_names.join(", ")}`)
        );
      }
      if (status.discord_thread_url) {
        lines.push(
          el(
            "a",
            { class: "btn", href: status.discord_thread_url, target: "_blank", rel: "noopener" },
            "Continue in Discord"
          )
        );
      }
      detailEl.append(...lines);
    } catch (err) {
      statusEl.textContent = `Could not load engagement status: ${err.message}`;
    }
  }

  refresh();
  return panel;
}

function residentsPanel(ctx) {
  const statusEl = el("p", { class: "tab-status" });
  const listHost = el("div", {});
  const profileHost = el("div", {});
  const panel = el(
    "div",
    { class: "panel" },
    el("h2", { text: "Residents" }),
    statusEl,
    listHost,
    profileHost
  );

  async function showProfile(name) {
    profileHost.innerHTML = "";
    try {
      const profile = await ctx.apiFetch(
        `/activity/dashboard/residents/${ctx.characterId()}/${encodeURIComponent(name)}` +
          `?discord_id=${encodeURIComponent(ctx.discordId())}`
      );
      const lines = [
        el("h3", { text: profile.name }),
        el("p", { class: "tab-status" }, `${profile.job_title}`),
      ];
      if (profile.location_name) {
        lines.push(el("p", { class: "tab-status" }, `At: ${profile.location_name}`));
      }
      lines.push(
        el("p", { class: "tab-status" }, `Traits: ${profile.traits.join(", ") || "unknown"}`)
      );
      lines.push(el("p", { class: "tab-status" }, `Speech: ${profile.tone}`));
      lines.push(el("p", { class: "tab-status" }, `Opinion of you: ${profile.stance}`));
      if (profile.appearance) lines.push(el("p", {}, profile.appearance));
      if (profile.backstory) lines.push(el("p", {}, profile.backstory));
      profileHost.append(...lines);
    } catch (err) {
      profileHost.append(el("p", { class: "result-line lose" }, err.message));
    }
  }

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    listHost.innerHTML = "";
    profileHost.innerHTML = "";
    if (!characterId || !discordId) {
      statusEl.textContent = "Pick a character above first.";
      return;
    }
    try {
      const data = await ctx.apiFetch(
        `/activity/dashboard/residents/${characterId}?discord_id=${encodeURIComponent(discordId)}`
      );
      statusEl.textContent = `Residents of ${data.district_name}:`;
      if (data.residents.length === 0) {
        listHost.append(el("p", { class: "tab-status" }, "Nobody lives here."));
        return;
      }
      const table = el(
        "table",
        { class: "data-table" },
        el(
          "thead",
          {},
          el("tr", {}, el("th", { text: "Name" }), el("th", { text: "Job" }), el("th", { text: "Where" }))
        )
      );
      const tbody = el("tbody", {});
      for (const r of data.residents) {
        const row = el(
          "tr",
          { class: "clickable-row", onClick: () => showProfile(r.name) },
          el("td", { text: r.name }),
          el("td", { text: r.job_title }),
          el("td", { text: r.location_name || "unknown" })
        );
        tbody.append(row);
      }
      table.append(tbody);
      listHost.append(table);
    } catch (err) {
      statusEl.textContent = `Could not load residents: ${err.message}`;
    }
  }

  refresh();
  return panel;
}

export function mount(root, ctx) {
  root.append(engagementPanel(ctx), residentsPanel(ctx));
  return {};
}
