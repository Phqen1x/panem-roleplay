// The Capitol Market's pet store popup -- opened from `city.js` when the
// player interacts with the market building. Lists every `PetSpecies`
// (`data/pets.yaml`) with a buy button; `tabs/pets.js` is the fuller,
// always-available version of the same catalog (for browsing/buying
// without having to walk up to the Market first) and reuses this same
// backend, just not this popup UI.
import { el } from "../tabs/_shared.js?v=7";

export function openPetStore(overlayHost, ctx, characterId) {
  overlayHost.innerHTML = "";
  const body = el("div", {}, el("p", { class: "tab-status" }, "Loading the pet store…"));
  const resultLine = el("p", { class: "result-line" });
  overlayHost.append(
    el(
      "div",
      { class: "panel map3d-popup" },
      el("h2", { text: "Capitol Market -- Pet Store" }),
      body,
      resultLine,
      el(
        "button",
        { class: "btn secondary", type: "button", onclick: () => (overlayHost.innerHTML = "") },
        "Close"
      )
    )
  );

  async function refresh() {
    try {
      const catalog = await ctx.apiFetch("/activity/dashboard/pets/catalog");
      body.innerHTML = "";
      for (const species of catalog.species) {
        const nameInput = el("input", { type: "text", placeholder: "Name your pet", maxlength: "32" });
        const buyButton = el("button", { class: "btn", type: "button" }, `Buy -- ${species.price}`);
        buyButton.addEventListener("click", async () => {
          resultLine.textContent = "";
          try {
            await ctx.apiFetch(`/activity/dashboard/pets/${characterId}/buy`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                discord_id: ctx.discordId(),
                species_id: species.id,
                name: nameInput.value || species.name,
              }),
            });
            resultLine.className = "result-line win";
            resultLine.textContent = `You bought a ${species.name}!`;
          } catch (err) {
            resultLine.className = "result-line lose";
            resultLine.textContent = err.message;
          }
        });
        body.append(
          el(
            "div",
            { class: "field-row" },
            el("span", {}, `${species.name} -- ${species.description}`),
            nameInput,
            buyButton
          )
        );
      }
    } catch (err) {
      body.innerHTML = "";
      body.append(el("p", { class: "result-line lose" }, err.message));
    }
  }

  refresh();
}
