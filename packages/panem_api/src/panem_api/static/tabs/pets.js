// The "Pets" tab -- browse the Capitol Market's pet catalog and buy/view
// a character's pets, without having to walk up to the Market on the
// Capitol's 3D map first (`../map3d/pets_panel.js` is the in-map popup
// version of this same flow, reached that way instead).
import { el, setStatusText } from "./_shared.js?v=7";

function ownedPanel(pets) {
  if (pets.length === 0) {
    return el(
      "div",
      { class: "panel" },
      el("h2", { text: "Your Pets" }),
      el("p", { class: "tab-status" }, "You don't own any pets yet.")
    );
  }
  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Your Pets" }),
    ...pets.map((pet) => el("p", { class: "tab-status" }, `${pet.name} the ${pet.species_id}`))
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
      const [catalog, owned] = await Promise.all([
        ctx.apiFetch("/activity/dashboard/pets/catalog"),
        ctx.apiFetch(
          `/activity/dashboard/pets/${characterId}?discord_id=${encodeURIComponent(discordId)}`
        ),
      ]);
      setStatusText(statusLine, "");

      const table = el(
        "table",
        { class: "data-table" },
        el(
          "thead",
          {},
          el(
            "tr",
            {},
            el("th", { text: "Species" }),
            el("th", { text: "Price" }),
            el("th", { text: "" })
          )
        )
      );
      const tbody = el("tbody", {});
      for (const species of catalog.species) {
        const nameInput = el("input", { type: "text", placeholder: species.name, maxlength: "32" });
        const buyBtn = el("button", { class: "btn", type: "button" }, "Buy");
        buyBtn.addEventListener("click", async () => {
          resultLine.textContent = "";
          try {
            await ctx.apiFetch(`/activity/dashboard/pets/${characterId}/buy`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                discord_id: discordId,
                species_id: species.id,
                name: nameInput.value || species.name,
              }),
            });
            resultLine.className = "result-line win";
            resultLine.textContent = `You bought a ${species.name}!`;
            await refresh();
          } catch (err) {
            resultLine.className = "result-line lose";
            resultLine.textContent = err.message;
          }
        });
        tbody.append(
          el(
            "tr",
            {},
            el("td", { text: `${species.name} -- ${species.description}` }),
            el("td", { text: String(species.price) }),
            el("td", {}, nameInput, buyBtn)
          )
        );
      }
      table.append(tbody);

      host.append(
        ownedPanel(owned.pets),
        el("div", { class: "panel" }, el("h2", { text: "Pet Store" }), table)
      );
    } catch (err) {
      setStatusText(statusLine, `Could not load pets: ${err.message}`, { error: true });
    }
  }

  refresh();

  return {};
}
