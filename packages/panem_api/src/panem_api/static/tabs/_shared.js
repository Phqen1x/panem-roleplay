// Small helpers shared by every dashboard tab module under `tabs/*.js`.
// Unlike `work.js`/`crime.js` (standalone pages, each intentionally
// self-contained/copy-paste per their own docstrings), the tabs all live
// inside one shell (`app.js`) that already imports them, so a shared
// module here is worth the coupling.
//
// Every importer uses a static `import ... from "./_shared.js?v=N"` (or
// `"./tabs/_shared.js?v=N"` from `app.js`) rather than a bare specifier --
// this file is fetched as its own URL by the browser's module loader,
// independently of whatever cache-busting the *importing* file's own URL
// carries, and Discord's Activity iframe embedding is known to cache
// static assets aggressively at its proxy layer regardless of this
// server's own response headers. Bump the `?v=` literal in every one of
// those import statements any time this file's exports change -- a stale
// cached copy missing a new export surfaces as "The requested module
// './_shared.js' does not provide an export named '...'" in whichever
// tabs import it, exactly the bug this convention exists to prevent.

// Dashboard error responses carry the service layer's raw `reason_key`
// (e.g. "bail_insufficient_funds") as `detail` -- the same key `strings.py`
// looks up for a Discord reply, reused here rather than a second set of
// messages (see dashboard_routes.py's `_http_from_service_error`). Turning
// underscores into spaces is a cheap, generic readability pass so every
// tab's error text is presentable without hand-writing a translation for
// each one.
//
// `detail` isn't always that string, though: a request FastAPI itself
// rejects before reaching a route (e.g. a non-numeric character id in the
// URL, which happens if a tab fires a request with no character selected)
// comes back with `detail` as a *list* of `{loc, msg, type}` validation-
// error objects, not a string. The old `typeof text === "string" ? ... :
// text` fallback returned that list/object as-is, and `new Error(...)`
// coerces a non-string argument with `String(...)` -- for a plain object
// or an array of them, that's the ever-informative "[object Object]".
// Always return a string (or a falsy value the caller's own `||` fallback
// catches) instead.
function humanize(text) {
  if (typeof text === "string") return text.replaceAll("_", " ");
  if (Array.isArray(text)) {
    const messages = text
      .map((item) => (item && typeof item.msg === "string" ? item.msg : null))
      .filter((msg) => msg !== null);
    if (messages.length > 0) return messages.join("; ");
  }
  return undefined;
}

export async function fetchJson(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(humanize(body.detail) || `${path} -> ${response.status}`);
  }
  return body;
}

// A hand-rolled dropdown standing in for a native <select> -- Discord
// scales/transforms an Activity's iframe content for its own embedding,
// which is known to break Chromium's native select-popup positioning in
// that context (the popup either fails to open or renders somewhere
// invisible/unclickable). This mimics just enough of <select>'s surface
// (a `value` getter/setter, a `disabled` setter, a real "change" event)
// that call sites barely have to change: swap `el("select", {})` for
// `dropdown()`, and repeated `.append(el("option", {value}, label))`
// calls for one `.setOptions([{value, label}, ...])` call.
export function dropdown(initialOptions) {
  const wrapper = el("div", { class: "custom-select" });
  const toggle = el("button", { type: "button", class: "custom-select-toggle" });
  const menu = el("ul", { class: "custom-select-menu" });
  menu.hidden = true;
  wrapper.append(toggle, menu);

  let opts = [];
  let current = "";

  function close() {
    menu.hidden = true;
  }

  function renderMenu() {
    menu.innerHTML = "";
    for (const opt of opts) {
      const button = el(
        "button",
        {
          type: "button",
          class: opt.value === current ? "active" : "",
          onclick: () => {
            current = opt.value;
            toggle.textContent = opt.label;
            close();
            wrapper.dispatchEvent(new Event("change"));
          },
        },
        opt.label
      );
      menu.append(el("li", {}, button));
    }
  }

  toggle.addEventListener("click", () => {
    if (toggle.disabled) return;
    menu.hidden = !menu.hidden;
  });
  document.addEventListener("click", (event) => {
    if (!menu.hidden && !event.composedPath().includes(wrapper)) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });

  Object.defineProperty(wrapper, "value", {
    get: () => current,
    set(v) {
      current = String(v);
      const match = opts.find((o) => o.value === current);
      toggle.textContent = match ? match.label : "";
      renderMenu();
    },
  });
  Object.defineProperty(wrapper, "disabled", {
    get: () => toggle.disabled,
    set(v) {
      toggle.disabled = v;
    },
  });

  wrapper.setOptions = (entries) => {
    opts = entries.map((entry) => ({ value: String(entry.value), label: entry.label }));
    if (!opts.some((o) => o.value === current)) {
      current = opts.length > 0 ? opts[0].value : "";
    }
    const match = opts.find((o) => o.value === current);
    toggle.textContent = match ? match.label : "No options";
    toggle.disabled = opts.length === 0;
    renderMenu();
  };

  if (initialOptions) wrapper.setOptions(initialOptions);
  return wrapper;
}

export function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "text") {
      node.textContent = value;
    } else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value !== undefined && value !== null) {
      node.setAttribute(key, value);
    }
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function renderTabIcon(tabName) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.classList.add("tab-icon");
  svg.style.width = "16px";
  svg.style.height = "16px";
  svg.style.maxWidth = "16px";
  svg.style.maxHeight = "16px";
  svg.style.minWidth = "16px";
  svg.style.minHeight = "16px";
  svg.style.flexShrink = "0";
  svg.style.display = "inline-block";
  svg.style.verticalAlign = "middle";

  const icons = {
    map: '<polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"></polygon><line x1="8" y1="2" x2="8" y2="18"></line><line x1="16" y1="6" x2="16" y2="22"></line>',
    character: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle>',
    work: '<rect x="2" y="7" width="20" height="14" rx="2" ry="2"></rect><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path>',
    market: '<circle cx="9" cy="21" r="1"></circle><circle cx="20" cy="21" r="1"></circle><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"></path>',
    travel: '<circle cx="12" cy="12" r="10"></circle><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"></polygon>',
    social: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path>',
    jail: '<rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/>',
    crime: '<polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline>',
    housing: '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline>',
    staff: '<circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>',
    games: '<rect x="2" y="7" width="20" height="14" rx="3" ry="3"></rect><line x1="7" y1="12" x2="7" y2="16"></line><line x1="5" y1="14" x2="9" y2="14"></line><circle cx="15" cy="12" r="1"></circle><circle cx="18" cy="15" r="1"></circle>',
  };

  svg.innerHTML = icons[tabName] || icons.map;
  return svg;
}

export function renderIcon(iconName, size = 18) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.classList.add("ui-icon", `icon-${iconName}`);
  svg.style.width = `${size}px`;
  svg.style.height = `${size}px`;
  svg.style.maxWidth = `${size}px`;
  svg.style.maxHeight = `${size}px`;
  svg.style.minWidth = `${size}px`;
  svg.style.minHeight = `${size}px`;
  svg.style.flexShrink = "0";
  svg.style.display = "inline-block";
  svg.style.verticalAlign = "middle";

  const icons = {
    search: '<circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line>',
    pin: '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle>',
    wave: '<path d="M2 12h2M6 8h2M10 4h2M14 6h2M18 10h2M22 12h2M6 16h2M10 20h2M14 18h2M18 14h2"></path>',
    user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle>',
    users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path>',
    crest: '<path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>',
    discord: '<path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994.021-.041.001-.09-.041-.106a13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.929 1.793 8.18 1.793 12.061 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.893.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.028zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z"/>',
  };

  if (iconName === "discord") {
    svg.setAttribute("fill", "currentColor");
    svg.setAttribute("stroke", "none");
  }

  svg.innerHTML = icons[iconName] || "";
  return svg;
}
