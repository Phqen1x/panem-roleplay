// Small helpers shared by every dashboard tab module under `tabs/*.js`.
// Unlike `work.js`/`crime.js` (standalone pages, each intentionally
// self-contained/copy-paste per their own docstrings), the tabs all live
// inside one shell (`app.js`) that already imports them, so a shared
// module here is worth the coupling.

export async function fetchJson(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || `${path} -> ${response.status}`);
  }
  return body;
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
