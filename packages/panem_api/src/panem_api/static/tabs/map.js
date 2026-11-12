// The "Map" tab -- the live district map. Every district except the
// Capitol still gets the original schematic: a layout drawn from each
// location's map coordinates (no real district map art yet -- see the
// README), NPCs/characters pushed over a polling WebSocket. The Capitol
// (district 0) instead mounts a real 3D city (`../map3d/city.js`) built
// from the exact same `location_coords` the schematic draws circles from
// (`panem_shared.map3d_layout`) -- "this city should be the map on the
// map screen", in the feature request's own words. Other districts get
// their own 3D city "later" (also the request's own words); the 3D
// module is written generically enough to support that without changes
// to it, just a frontend switch here once art direction decides to turn
// it on for them too.
//
// `../map3d/city.js` (and everything it imports -- three.js, GLTFLoader,
// the vendored Kenney assets) is dynamically imported only the first time
// the Capitol is actually selected, not on every Map tab open -- most
// sessions never touch the Capitol, and there's no reason to make them
// pay for ~1MB of 3D engine + assets just for a schematic they'll never see.
import { fetchJson, el, dropdown, setStatusText } from "./_shared.js?v=8";

const CAPITOL_DISTRICT_ID = 0;

function wsUrlFor(districtId) {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/ws/districts/${districtId}/positions`;
}

// The district a freshly-opened Map tab should default to: the selected
// character's current location if they're mid-travel/away from home,
// else their home district -- mirrors `app.js`'s own `updateTelemetry`
// fallback chain for the same two fields. Falls back to whatever
// `/districts` happens to list first (previously this tab's only
// behavior) when no character is selected or its district isn't in the
// list for some reason.
function defaultDistrictId(ctx, districts) {
  const character = ctx.characters().find((c) => c.id === ctx.characterId());
  const preferred = character ? (character.current_district_id ?? character.district_id) : null;
  if (preferred != null && districts.some((d) => d.id === preferred)) return preferred;
  return districts[0].id;
}

export function mount(root, ctx) {
  const statusEl = el("p", { class: "tab-status" }, "Loading districts…");
  const districtSelect = dropdown();
  districtSelect.disabled = true;
  const countsEl = el("span", { class: "tab-counts" });
  const canvas = el("canvas", { width: "960", height: "600" });
  const city3DContainer = el("div", {});
  city3DContainer.hidden = true;
  const panel = el(
    "div",
    { class: "panel map-panel" },
    el("div", { class: "map-toolbar", style: "margin-bottom: 14px;" }, districtSelect, countsEl),
    statusEl,
    canvas,
    city3DContainer
  );
  root.append(panel);

  const canvasCtx = canvas.getContext("2d");
  let districts = [];
  let currentDistrict = null;
  let socket = null;
  let city3DHandle = null;
  let switching = false;

  function toCanvasX(x) {
    return (x / currentDistrict.map_width) * canvas.width;
  }
  function toCanvasY(y) {
    return (y / currentDistrict.map_height) * canvas.height;
  }

  function render(positions) {
    const d = currentDistrict;
    if (!d) return;
    canvasCtx.fillStyle = "#0a0c10";
    canvasCtx.fillRect(0, 0, canvas.width, canvas.height);
    canvasCtx.strokeStyle = "rgba(197, 160, 89, 0.4)";
    canvasCtx.fillStyle = "#e5c07b";
    canvasCtx.font = "12px 'Cinzel', serif";
    canvasCtx.textAlign = "center";
    for (const loc of d.locations) {
      const x = toCanvasX(loc.x);
      const y = toCanvasY(loc.y);
      canvasCtx.beginPath();
      canvasCtx.arc(x, y, 28, 0, Math.PI * 2);
      canvasCtx.stroke();
      canvasCtx.fillText(loc.name, x, y - 34);
    }
    canvasCtx.fillStyle = "#8b93a4";
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
      canvasCtx.fillStyle = "#c5a059";
      canvasCtx.beginPath();
      canvasCtx.arc(x, y, 5, 0, Math.PI * 2);
      canvasCtx.fill();
      canvasCtx.fillText(character.name, x, y + 16);
    }
    countsEl.textContent = `${positions.npcs.length} NPCs, ${positions.characters.length} characters`;
  }

  function teardown3D() {
    if (city3DHandle) {
      city3DHandle.unmount();
      city3DHandle = null;
    }
    city3DContainer.hidden = true;
    city3DContainer.innerHTML = "";
  }

  async function connectToDistrict(districtId) {
    if (switching) return;
    switching = true;
    try {
      if (socket) socket.close();
      socket = null;
      currentDistrict = districts.find((d) => d.id === districtId) ?? null;
      if (!currentDistrict) return;

      if (districtId === CAPITOL_DISTRICT_ID) {
        teardown3D();
        canvas.hidden = true;
        city3DContainer.hidden = false;
        setStatusText(statusEl, "");
        try {
          const { mountCity3D } = await import("../map3d/city.js");
          city3DHandle = await mountCity3D(city3DContainer, ctx, districtId);
        } catch (err) {
          setStatusText(statusEl, `Could not load the Capitol's 3D map: ${err.message || err}`, {
            error: true,
          });
        }
        return;
      }

      teardown3D();
      canvas.hidden = false;
      socket = new WebSocket(wsUrlFor(districtId));
      socket.onmessage = (event) => render(JSON.parse(event.data));
      socket.onerror = () => {
        setStatusText(statusEl, "Lost connection to the district feed -- retrying on reconnect.", {
          error: true,
        });
      };
    } finally {
      switching = false;
    }
  }

  (async () => {
    try {
      districts = await fetchJson("/districts");
    } catch (err) {
      setStatusText(statusEl, `Could not load districts: ${err}`, { error: true });
      return;
    }
    setStatusText(statusEl, "");
    districtSelect.setOptions(districts.map((d) => ({ value: d.id, label: d.name })));
    districtSelect.addEventListener("change", () => connectToDistrict(Number(districtSelect.value)));
    if (districts.length > 0) {
      const startId = defaultDistrictId(ctx, districts);
      districtSelect.value = String(startId);
      connectToDistrict(startId);
    }
  })();

  return {
    unmount() {
      if (socket) socket.close();
      teardown3D();
    },
  };
}
