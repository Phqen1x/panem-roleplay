// The "Market" tab: mirrors /market prices|buy|sell, /inventory, and
// /blackmarket prices|buy|sell in one place (a legal/illicit toggle
// instead of two separate tabs) -- both are instant-resolve, no
// minigame/iframe involved, same as their bot commands.
import { fetchJson, el, dropdown } from "./_shared.js?v=5";

// Each row carries its own qty input + action button, so buying/selling a
// good never requires typing its id -- the id only ever travels in the
// request body, read straight off the row's own data.
function priceTable(prices, onBuy) {
  const table = el(
    "table",
    { class: "data-table" },
    el(
      "thead",
      {},
      el("tr", {}, el("th", { text: "Good" }), el("th", { text: "Price" }), el("th", { text: "Qty" }), el("th", {}))
    )
  );
  const tbody = el("tbody", {});
  for (const p of prices) {
    const qtyInput = el("input", { type: "number", value: "1", min: "1", class: "qty-input" });
    const buyBtn = el("button", { class: "btn", type: "button" }, "Buy");
    buyBtn.addEventListener("click", () => onBuy(p, Number(qtyInput.value)));
    tbody.append(
      el(
        "tr",
        {},
        el("td", { text: p.name }),
        el("td", { text: p.price.toFixed(2) }),
        el("td", {}, qtyInput),
        el("td", {}, buyBtn)
      )
    );
  }
  table.append(tbody);
  return table;
}

function inventoryTable(inventory, onSell) {
  if (inventory.length === 0) {
    return el("p", { class: "tab-status" }, "Inventory: empty.");
  }
  const table = el(
    "table",
    { class: "data-table" },
    el(
      "thead",
      {},
      el("tr", {}, el("th", { text: "Owned" }), el("th", { text: "Have" }), el("th", { text: "Qty" }), el("th", {}))
    )
  );
  const tbody = el("tbody", {});
  for (const item of inventory) {
    const qtyInput = el("input", {
      type: "number",
      value: "1",
      min: "1",
      max: String(item.qty),
      class: "qty-input",
    });
    const sellBtn = el("button", { class: "btn secondary", type: "button" }, "Sell");
    sellBtn.addEventListener("click", () => onSell(item, Number(qtyInput.value)));
    tbody.append(
      el(
        "tr",
        {},
        el("td", { text: item.name }),
        el("td", { text: String(item.qty) }),
        el("td", {}, qtyInput),
        el("td", {}, sellBtn)
      )
    );
  }
  table.append(tbody);
  return table;
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

  root.append(
    el(
      "div",
      { class: "panel" },
      el("h2", { text: "Market" }),
      el("div", { class: "field-row" }, el("label", { text: "Mode" }), modeSelect),
      statusEl,
      trustEl,
      el("h3", { text: "Prices" }),
      pricesHost,
      el("h3", { text: "Inventory" }),
      inventoryHost,
      resultLine
    )
  );

  async function trade(basePath, side, good, qty) {
    resultLine.textContent = "";
    if (!Number.isFinite(qty) || qty < 1) {
      resultLine.className = "result-line lose";
      resultLine.textContent = "Enter a quantity of at least 1.";
      return;
    }
    try {
      const body = await ctx.apiFetch(`${basePath}/${side}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), good_id: good.good_id, qty }),
      });
      resultLine.className = body.caught ? "result-line lose" : "result-line win";
      resultLine.textContent = body.caught
        ? `${side === "buy" ? "Bought" : "Sold"} ${body.qty}x ${body.good_name} -- but got caught!`
        : `${side === "buy" ? "Bought" : "Sold"} ${body.qty}x ${body.good_name} for ${body.total}.`;
      await refresh();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  }

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    pricesHost.innerHTML = "";
    inventoryHost.innerHTML = "";
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
        pricesHost.append(priceTable(status.prices, (good, qty) => trade(basePath, "buy", good, qty)));
      }
      inventoryHost.append(
        inventoryTable(status.inventory, (item, qty) => trade(basePath, "sell", item, qty))
      );
    } catch (err) {
      statusEl.textContent = `Could not load market: ${err.message}`;
    }
  }

  modeSelect.addEventListener("change", refresh);
  refresh();

  return {};
}
