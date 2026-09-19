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
function humanize(text) {
  return typeof text === "string" ? text.replaceAll("_", " ") : text;
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
