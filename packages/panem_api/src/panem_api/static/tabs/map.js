// The "Map" tab -- the live schematic district map that used to be the
// whole of index.html/app.js before the dashboard grew tabs. Behavior is
// unchanged from that original version: a schematic layout drawn from each
// location's map coordinates (no real district map art yet -- see the
// README), NPCs/characters pushed over a polling WebSocket.
import { fetchJson, el } from "./_shared.js";

function wsUrlFor(districtId) {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/ws/districts/${districtId}/positions`;
}

export function mount(root, _ctx) {
  const statusEl = el("p", { class: "tab-status" }, "Loading districts…");
  const districtSelect = el("select", { disabled: "" });
  const countsEl = el("span", { class: "tab-counts" });
  const canvas = el("canvas", { width: "960", height: "600" });
  root.append(
    el("div", { class: "map-toolbar" }, districtSelect, countsEl),
    statusEl,
    canvas
  );

  const canvasCtx = canvas.getContext("2d");
  let districts = [];
  let currentDistrict = null;
  let socket = null;

  function toCanvasX(x) {
    return (x / currentDistrict.map_width) * canvas.width;
  }
  function toCanvasY(y) {
    return (y / currentDistrict.map_height) * canvas.height;
  }

  function render(positions) {
    const d = currentDistrict;
    if (!d) return;
    canvasCtx.fillStyle = "#1b1f27";
    canvasCtx.fillRect(0, 0, canvas.width, canvas.height);
    canvasCtx.strokeStyle = "#3a4150";
    canvasCtx.fillStyle = "#8b93a3";
    canvasCtx.font = "12px sans-serif";
    canvasCtx.textAlign = "center";
    for (const loc of d.locations) {
      const x = toCanvasX(loc.x);
      const y = toCanvasY(loc.y);
      canvasCtx.beginPath();
      canvasCtx.arc(x, y, 28, 0, Math.PI * 2);
      canvasCtx.stroke();
      canvasCtx.fillText(loc.name, x, y - 34);
    }
    canvasCtx.fillStyle = "#7d8597";
    for (const npc of positions.npcs) {
      if (npc.x == null || npc.y == null) continue;
      canvasCtx.beginPath();
      canvasCtx.arc(toCanvasX(npc.x), toCanvasY(npc.y), 3, 0, Math.PI * 2);
      canvasCtx.fill();
    }
    for (const character of positions.characters) {
      if (character.x == null || character.y == null) continue;
      const x = toCanvasX(character.x);
      const y = toCanvasY(character.y);
      canvasCtx.fillStyle = "#e0a72e";
      canvasCtx.beginPath();
      canvasCtx.arc(x, y, 5, 0, Math.PI * 2);
      canvasCtx.fill();
      canvasCtx.fillText(character.name, x, y + 16);
    }
    countsEl.textContent = `${positions.npcs.length} NPCs, ${positions.characters.length} characters`;
  }

  function connectToDistrict(districtId) {
    if (socket) socket.close();
    currentDistrict = districts.find((d) => d.id === districtId) ?? null;
    if (!currentDistrict) return;
    socket = new WebSocket(wsUrlFor(districtId));
    socket.onmessage = (event) => render(JSON.parse(event.data));
    socket.onerror = () => {
      statusEl.textContent = "Lost connection to the district feed -- retrying on reconnect.";
    };
  }

  (async () => {
    try {
      districts = await fetchJson("/districts");
    } catch (err) {
      statusEl.textContent = `Could not load districts: ${err}`;
      return;
    }
    statusEl.textContent = "";
    districtSelect.innerHTML = "";
    districtSelect.disabled = false;
    for (const d of districts) {
      districtSelect.append(el("option", { value: String(d.id) }, d.name));
    }
    districtSelect.addEventListener("change", () => connectToDistrict(Number(districtSelect.value)));
    if (districts.length > 0) {
      districtSelect.value = String(districts[0].id);
      connectToDistrict(districts[0].id);
    }
  })();

  return {
    unmount() {
      if (socket) socket.close();
    },
  };
}
