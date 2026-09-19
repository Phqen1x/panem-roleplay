// The "Housing" tab: mirrors every `/housing` subcommand plus `/sleep`.
// Three panels: your own status (home, fatigue, sleep controls), what
// you own (per-property sell/refinance/rent-out/auction actions), and
// what's for sale/rent in your current district (buy/rent/inn-stay).
import { fetchJson, el } from "./_shared.js?v=2";

function priceLabelSuffix(label) {
  if (label === "night") return "/night";
  if (label === "day_rent") return "/day rent";
  return "";
}

function sleepPanel(ctx, status, resultLine, onChanged) {
  const ticksInput = el("input", { type: "number", placeholder: "ticks (blank = rest of night)" });
  const sleepBtn = el("button", { class: "btn", type: "button" }, "Sleep");
  sleepBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const body = await ctx.apiFetch(`/activity/dashboard/housing/${ctx.characterId()}/sleep`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: Number(ctx.discordId()),
          ticks: ticksInput.value ? Number(ticksInput.value) : null,
        }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = `Slept ${body.ticks} ticks, restored ${body.restored} fatigue.`;
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });
  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Status" }),
    el("p", { class: "tab-status" }, `${status.character_name} -- fatigue ${status.fatigue}`),
    el(
      "p",
      { class: "tab-status" },
      status.home_property_id
        ? `Home: ${status.home_kind} #${status.home_property_id} in ${status.home_district_name}`
        : "No fixed home."
    ),
    el("div", { class: "field-row" }, el("label", { text: "Sleep" }), ticksInput, sleepBtn)
  );
}

function ownedRow(ctx, property_, resultLine, onChanged) {
  const priceInput = el("input", {
    type: "number",
    value: property_.asking_price != null ? String(property_.asking_price) : "",
    placeholder: "price",
  });
  const amountInput = el("input", { type: "number", placeholder: "amount" });

  async function post(path, body) {
    resultLine.textContent = "";
    try {
      const result = await ctx.apiFetch(
        `/activity/dashboard/housing/${ctx.characterId()}/${property_.id}/${path}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: Number(ctx.discordId()), ...body }),
        }
      );
      resultLine.className = "result-line win";
      resultLine.textContent = "Done.";
      onChanged();
      return result;
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
      return null;
    }
  }

  const listBtn = el("button", { class: "btn secondary", type: "button" }, "List for sale");
  listBtn.addEventListener("click", () =>
    post("sell", { price: priceInput.value ? Number(priceInput.value) : null })
  );
  const delistBtn = el("button", { class: "btn secondary", type: "button" }, "Delist");
  delistBtn.addEventListener("click", () => post("sell", { price: null }));

  const refinanceBtn = el("button", { class: "btn secondary", type: "button" }, "Refinance");
  refinanceBtn.addEventListener("click", () =>
    post("refinance", { amount: Number(amountInput.value || 0) })
  );

  const auctionBtn = el("button", { class: "btn secondary", type: "button" }, "Start auction");
  auctionBtn.addEventListener("click", () =>
    post("auction-start", { minimum_bid: Number(amountInput.value || 0) })
  );

  const rentOutBtn = el("button", { class: "btn secondary", type: "button" }, "Set rent");
  rentOutBtn.addEventListener("click", () =>
    post("rent-out", { price: Number(priceInput.value || 0) })
  );

  const controls = [listBtn, delistBtn, priceInput];
  if (property_.kind === "apartment") {
    controls.push(rentOutBtn);
  } else {
    controls.push(amountInput, refinanceBtn, auctionBtn);
  }

  return el(
    "div",
    { class: "field-row" },
    el(
      "span",
      {},
      `#${property_.id} ${property_.kind} (${property_.tier}) -- ${property_.district_name}` +
        (property_.mortgage_principal > 0
          ? ` -- mortgage ${property_.mortgage_principal} (${property_.mortgage_payment}/installment)`
          : "") +
        (property_.has_open_auction ? " -- auction open" : "")
    ),
    ...controls
  );
}

function ownedPanel(ctx, status, resultLine, onChanged) {
  if (status.owned.length === 0) {
    return el(
      "div",
      { class: "panel" },
      el("h2", { text: "Your Properties" }),
      el("p", { class: "tab-status" }, "You don't own any property.")
    );
  }
  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Your Properties" }),
    ...status.owned.map((p) => ownedRow(ctx, p, resultLine, onChanged))
  );
}

function listingRow(ctx, listing, resultLine, onChanged) {
  const buyBtn = el("button", { class: "btn", type: "button" }, "Buy");
  const financedCheckbox = el("input", { type: "checkbox" });
  const rentBtn = el("button", { class: "btn", type: "button" }, "Rent");
  const innBtn = el("button", { class: "btn", type: "button" }, "Stay the night");

  async function post(path, body) {
    resultLine.textContent = "";
    try {
      await ctx.apiFetch(
        `/activity/dashboard/housing/${ctx.characterId()}/${listing.id}/${path}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: Number(ctx.discordId()), ...body }),
        }
      );
      resultLine.className = "result-line win";
      resultLine.textContent = "Done.";
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  }

  buyBtn.addEventListener("click", () => post("buy", { financed: financedCheckbox.checked }));
  rentBtn.addEventListener("click", () => post("rent", {}));
  innBtn.addEventListener("click", () => post("inn-stay", {}));

  const controls = [];
  if (listing.kind === "apartment") {
    controls.push(rentBtn);
  } else if (listing.kind === "inn") {
    controls.push(innBtn);
  } else {
    controls.push(
      buyBtn,
      el("label", {}, financedCheckbox, " financed")
    );
  }

  return el(
    "tr",
    {},
    el("td", { text: `#${listing.id}` }),
    el("td", { text: `${listing.kind} (${listing.tier})` }),
    el("td", { text: `${listing.price}${priceLabelSuffix(listing.price_label)}` }),
    el("td", {}, ...controls)
  );
}

function listingsPanel(ctx, status, resultLine, onChanged) {
  if (status.listings.length === 0) {
    return el(
      "div",
      { class: "panel" },
      el("h2", { text: "For Sale / Rent Here" }),
      el("p", { class: "tab-status" }, "Nothing available here right now.")
    );
  }
  const table = el(
    "table",
    { class: "data-table" },
    el(
      "thead",
      {},
      el(
        "tr",
        {},
        el("th", { text: "ID" }),
        el("th", { text: "Property" }),
        el("th", { text: "Price" }),
        el("th", { text: "" })
      )
    )
  );
  const tbody = el("tbody", {});
  for (const listing of status.listings) {
    tbody.append(listingRow(ctx, listing, resultLine, onChanged));
  }
  table.append(tbody);
  return el("div", { class: "panel" }, el("h2", { text: "For Sale / Rent Here" }), table);
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
      statusLine.textContent = "Pick a character above first.";
      return;
    }
    try {
      const status = await ctx.apiFetch(
        `/activity/dashboard/housing/${characterId}?discord_id=${encodeURIComponent(discordId)}`
      );
      statusLine.textContent = "";
      host.append(
        sleepPanel(ctx, status, resultLine, refresh),
        ownedPanel(ctx, status, resultLine, refresh),
        listingsPanel(ctx, status, resultLine, refresh)
      );
    } catch (err) {
      statusLine.textContent = `Could not load housing status: ${err.message}`;
    }
  }

  refresh();

  return {};
}
