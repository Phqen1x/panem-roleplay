// The "Travel" tab: mirrors `/travel` (both its location and
// cross-district sub-flows) and `/where` (folded into the status panel
// below rather than a separate action).
import { fetchJson, el, dropdown } from "./_shared.js";

export function mount(root, ctx) {
  const statusEl = el("p", { class: "tab-status" });
  const whereEl = el("p", { class: "tab-status" });
  const locationSelect = dropdown();
  const goLocationBtn = el("button", { class: "btn", type: "button" }, "Go");
  const districtSelect = dropdown();
  const goDistrictBtn = el("button", { class: "btn", type: "button" }, "Board train");
  const resultLine = el("p", { class: "result-line" });

  root.append(
    el(
      "div",
      { class: "panel" },
      el("h2", { text: "Travel" }),
      statusEl,
      whereEl,
      el(
        "div",
        { class: "field-row" },
        el("label", { text: "Location" }),
        locationSelect,
        goLocationBtn
      ),
      el(
        "div",
        { class: "field-row" },
        el("label", { text: "District" }),
        districtSelect,
        goDistrictBtn
      ),
      resultLine
    )
  );

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    if (!characterId || !discordId) {
      statusEl.textContent = "Pick a character above first.";
      whereEl.textContent = "";
      return;
    }
    try {
      const status = await ctx.apiFetch(
        `/activity/dashboard/travel/${characterId}?discord_id=${encodeURIComponent(discordId)}`
      );
      statusEl.textContent = `${status.character_name} is in ${status.current_district_name}.`;
      whereEl.textContent = status.in_transit
        ? "Currently in transit -- can't travel again until arrival."
        : status.location_name
          ? `Currently at ${status.location_name}.`
          : "Not at any particular location right now.";
      locationSelect.setOptions(status.locations.map((loc) => ({ value: loc.id, label: loc.name })));
      if (status.location_id) locationSelect.value = status.location_id;
      districtSelect.setOptions(status.districts.map((d) => ({ value: d.id, label: d.name })));
      const disabled = status.in_transit;
      goLocationBtn.disabled = disabled;
      goDistrictBtn.disabled = disabled;
    } catch (err) {
      statusEl.textContent = `Could not load travel status: ${err.message}`;
    }
  }

  goLocationBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const body = await ctx.apiFetch(
        `/activity/dashboard/travel/${ctx.characterId()}/location`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            discord_id: Number(ctx.discordId()),
            location_id: locationSelect.value,
          }),
        }
      );
      resultLine.className = "result-line win";
      resultLine.textContent = `${body.character_name} heads to ${body.location_name}.`;
      await refresh();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  goDistrictBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const body = await ctx.apiFetch(
        `/activity/dashboard/travel/${ctx.characterId()}/district`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            discord_id: Number(ctx.discordId()),
            destination_id: Number(districtSelect.value),
          }),
        }
      );
      resultLine.className = "result-line win";
      resultLine.textContent =
        `${body.character_name} boards the train to ${body.destination_district_name} ` +
        `-- arriving in ${body.transit_ticks} ticks.`;
      await refresh();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  refresh();

  return {};
}
