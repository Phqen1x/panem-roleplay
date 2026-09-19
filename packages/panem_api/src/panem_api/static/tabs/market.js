// The "Market" tab: mirrors /market prices|buy|sell, /inventory, and
// /blackmarket prices|buy|sell in one place (a legal/illicit toggle
// instead of two separate tabs) -- both are instant-resolve, no
// minigame/iframe involved, same as their bot commands.
import { fetchJson, el, dropdown } from "./_shared.js?v=2";

function priceTable(prices) {
  const table = el(
    "table",
    { class: "data-table" },
    el("thead", {}, el("tr", {}, el("th", { text: "Good" }), el("th", { text: "Price" })))
  );
  const tbody = el("tbody", {});
  for (const p of prices) {
    tbody.append(el("tr", {}, el("td", { text: p.name }), el("td", { text: p.price.toFixed(2) })));
  }
  table.append(tbody);
  return table;
}

function inventoryList(inventory) {
  if (inventory.length === 0) {
    return el("p", { class: "tab-status" }, "Inventory: empty.");
  }
  return el(
    "p",
    { class: "tab-status" },
    "Inventory: " + inventory.map((i) => `${i.name} x${i.qty}`).join(", ")
  );
}

function tradeForm(ctx, { basePath, resultLine, onTraded }) {
  const goodInput = el("input", { type: "text", placeholder: "good id" });
  const qtyInput = el("input", { type: "number", value: "1", min: "1" });
  const buyBtn = el("button", { class: "btn", type: "button" }, "Buy");
  const sellBtn = el("button", { class: "btn secondary", type: "button" }, "Sell");

  async function trade(side) {
    resultLine.textContent = "";
    try {
      const body = await ctx.apiFetch(`${basePath}/${side}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: Number(ctx.discordId()),
          good_id: goodInput.value,
          qty: Number(qtyInput.value),
        }),
      });
      resultLine.className = body.caught ? "result-line lose" : "result-line win";
      resultLine.textContent = body.caught
        ? `${side === "buy" ? "Bought" : "Sold"} ${body.qty}x ${body.good_name} -- but got caught!`
        : `${side === "buy" ? "Bought" : "Sold"} ${body.qty}x ${body.good_name} for ${body.total}.`;
      onTraded();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  }

  buyBtn.addEventListener("click", () => trade("buy"));
  sellBtn.addEventListener("click", () => trade("sell"));

  return el(
    "div",
    { class: "field-row" },
    el("label", { text: "Good id" }),
    goodInput,
    el("label", { text: "Qty" }),
    qtyInput,
    buyBtn,
    sellBtn
  );
}

export function mount(root, ctx) {
  const modeSelect = dropdown([
    { value: "market", label: "Legal market" },
    { value: "blackmarket", label: "Black market" },
  ]);
  const statusEl = el("p", { class: "tab-status" });
  const trustEl = el("p", { class: "tab-status" });
  const pricesHost = el("div", {});
  const inventoryHost = el("div", {});
  const resultLine = el("p", { class: "result-line" });
  const formHost = el("div", {});

  root.append(
    el(
      "div",
      { class: "panel" },
      el("h2", { text: "Market" }),
      el("div", { class: "field-row" }, el("label", { text: "Mode" }), modeSelect),
      statusEl,
      trustEl,
      pricesHost,
      inventoryHost,
      formHost,
      resultLine
    )
  );

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    pricesHost.innerHTML = "";
    inventoryHost.innerHTML = "";
    formHost.innerHTML = "";
    trustEl.textContent = "";
    if (!characterId || !discordId) {
      statusEl.textContent = "Pick a character above first.";
      return;
    }
    const mode = modeSelect.value;
    const basePath = `/activity/dashboard/${mode}/${characterId}`;
    try {
      const status = await ctx.apiFetch(`${basePath}?discord_id=${encodeURIComponent(discordId)}`);
      statusEl.textContent = "";
      if (mode === "blackmarket") {
        trustEl.textContent = status.trusted
          ? "The fence trusts you."
          : "The fence doesn't trust you yet -- buy/sell will be refused.";
      }
      if (status.prices.length === 0) {
        pricesHost.append(el("p", { class: "tab-status" }, "Nothing traded here."));
      } else {
        pricesHost.append(priceTable(status.prices));
      }
      inventoryHost.append(inventoryList(status.inventory));
      formHost.append(tradeForm(ctx, { basePath, resultLine, onTraded: refresh }));
    } catch (err) {
      statusEl.textContent = `Could not load market: ${err.message}`;
    }
  }

  modeSelect.addEventListener("change", refresh);
  refresh();

  return {};
}
