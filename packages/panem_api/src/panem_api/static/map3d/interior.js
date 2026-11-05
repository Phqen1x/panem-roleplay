// A property's interior: walked into from `city.js`'s residential-building
// prompt. Renders in its own full-size overlay panel with its own
// renderer/camera (an `OrbitControls`-driven look-around, not the city's
// own follow camera) -- simplest way to keep this fully independent of
// whatever `city.js`'s render loop is doing in the background while this
// is open.
//
// "Decorate" mode is nudge-and-rotate rather than free mouse-dragging:
// pick an item from the catalog to add it at the room's center, click any
// placed (non-structural) item to select it, then nudge it a fixed step
// at a time and rotate it in 90-degree turns. A real drag-across-the-
// floor placement tool would need camera-ray/floor-plane intersection
// math this feature didn't need to get right to deliver "allowed to do
// interior design" -- nudge controls are a complete, keyboard-and-mouse-
// click-only interior designer, just not a fancy one; swapping in true
// dragging later only touches this one file.
import * as THREE from "../vendor/three.module.min.js";
import { OrbitControls } from "../vendor/OrbitControls.js";
import { el } from "../tabs/_shared.js?v=7";
import { loadModel, cloneModel } from "./loader.js";

const ASSETS_BASE = "/assets3d";
const NUDGE_STEP = 0.25;

function furnitureUrl(itemKey) {
  return `${ASSETS_BASE}/furniture/${itemKey}.glb`;
}

export async function enterInterior(overlayHost, ctx, propertyId, onExit) {
  overlayHost.innerHTML = "";
  const statusLine = el("p", { class: "tab-status" }, "Loading the interior…");
  const canvas = el("canvas", { width: "900", height: "560", class: "map3d-canvas" });
  const sidebar = el("div", { class: "map3d-interior-sidebar" });
  const panel = el(
    "div",
    { class: "panel map3d-popup map3d-interior" },
    el("h2", { text: "Interior" }),
    statusLine,
    el("div", { class: "map3d-interior-layout" }, canvas, sidebar),
    el(
      "button",
      {
        class: "btn secondary",
        type: "button",
        onclick: () => {
          cleanup();
          onExit();
        },
      },
      "Exit"
    )
  );
  overlayHost.append(panel);

  let data;
  try {
    data = await ctx.apiFetch(
      `/activity/dashboard/housing3d/${propertyId}?discord_id=${encodeURIComponent(ctx.discordId())}`
    );
  } catch (err) {
    statusLine.className = "tab-status error";
    statusLine.textContent = err.message;
    return;
  }
  statusLine.textContent = "";

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(900, 560, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x30343c);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 1.3));
  const lamp = new THREE.DirectionalLight(0xffffff, 0.8);
  lamp.position.set(5, 8, 5);
  scene.add(lamp);

  const camera = new THREE.PerspectiveCamera(50, 900 / 560, 0.1, 100);
  const roomSize = Math.max(data.half_width, data.half_depth) * 2 + 4;
  camera.position.set(roomSize * 0.6, roomSize * 0.6, roomSize * 0.6);
  const controls = new OrbitControls(camera, canvas);
  controls.target.set(0, 0, 0);
  controls.update();

  const raycaster = new THREE.Raycaster();
  const placed = new Map(); // id (incrementing) -> {object3D, item_key, pos_x, pos_z, rotation_y, structural}
  let nextId = 1;
  let selectedId = null;

  async function addPlacedItem({ item_key, pos_x, pos_z, rotation_y, structural }) {
    const id = nextId++;
    try {
      const { scene: modelScene } = await loadModel(furnitureUrl(item_key));
      const instance = cloneModel(modelScene);
      instance.position.set(pos_x, 0, pos_z);
      instance.rotation.y = THREE.MathUtils.degToRad(rotation_y);
      instance.userData.placedId = id;
      scene.add(instance);
      placed.set(id, { object3D: instance, item_key, pos_x, pos_z, rotation_y, structural });
    } catch {
      // Missing/broken furniture model -- skip it rather than fail the
      // whole room.
    }
    return id;
  }

  for (const item of data.items) {
    await addPlacedItem(item);
  }

  let editMode = false;

  function renderSidebar() {
    sidebar.innerHTML = "";
    const modeButton = el(
      "button",
      { class: "btn secondary", type: "button" },
      editMode ? "Done Decorating" : "Decorate"
    );
    modeButton.addEventListener("click", () => {
      editMode = !editMode;
      selectedId = null;
      renderSidebar();
    });
    sidebar.append(modeButton);
    if (!editMode) {
      if (!data.can_edit) {
        sidebar.append(el("p", { class: "tab-status" }, "You don't live here, so you can't redecorate."));
      }
      return;
    }

    const catalogSelect = el("select", {});
    for (const piece of data.catalog) {
      catalogSelect.append(el("option", { value: piece.item_key }, piece.name));
    }
    const addButton = el("button", { class: "btn", type: "button" }, "Add");
    addButton.addEventListener("click", async () => {
      const piece = data.catalog.find((p) => p.item_key === catalogSelect.value);
      if (!piece) return;
      await addPlacedItem({ item_key: piece.item_key, pos_x: 0, pos_z: 0, rotation_y: 0 });
    });
    sidebar.append(el("div", { class: "field-row" }, catalogSelect, addButton));

    const selected = selectedId != null ? placed.get(selectedId) : null;
    if (selected) {
      const nudge = (dx, dz) => {
        selected.pos_x = THREE.MathUtils.clamp(selected.pos_x + dx, -data.half_width, data.half_width);
        selected.pos_z = THREE.MathUtils.clamp(selected.pos_z + dz, -data.half_depth, data.half_depth);
        selected.object3D.position.set(selected.pos_x, 0, selected.pos_z);
      };
      const rotate = () => {
        selected.rotation_y = (selected.rotation_y + 90) % 360;
        selected.object3D.rotation.y = THREE.MathUtils.degToRad(selected.rotation_y);
      };
      const remove = () => {
        scene.remove(selected.object3D);
        placed.delete(selectedId);
        selectedId = null;
        renderSidebar();
      };
      sidebar.append(
        el("h3", { text: `Selected: ${selected.item_key}` }),
        el(
          "div",
          { class: "map3d-nudge-grid" },
          el("button", { class: "btn secondary", type: "button", onclick: () => nudge(0, -NUDGE_STEP) }, "↑"),
          el("button", { class: "btn secondary", type: "button", onclick: () => nudge(-NUDGE_STEP, 0) }, "←"),
          el("button", { class: "btn secondary", type: "button", onclick: rotate }, "⟳"),
          el("button", { class: "btn secondary", type: "button", onclick: () => nudge(NUDGE_STEP, 0) }, "→"),
          el("button", { class: "btn secondary", type: "button", onclick: () => nudge(0, NUDGE_STEP) }, "↓")
        ),
        el("button", { class: "btn secondary", type: "button", onclick: remove }, "Remove")
      );
    } else {
      sidebar.append(el("p", { class: "tab-status" }, "Click a piece of furniture to select it."));
    }

    const resultLine = el("p", { class: "result-line" });
    const saveButton = el("button", { class: "btn", type: "button" }, "Save Layout");
    saveButton.addEventListener("click", async () => {
      resultLine.textContent = "";
      const items = [...placed.values()]
        .filter((p) => !p.structural)
        .map((p) => ({
          item_key: p.item_key,
          pos_x: p.pos_x,
          pos_z: p.pos_z,
          rotation_y: p.rotation_y,
        }));
      try {
        await ctx.apiFetch(`/activity/dashboard/housing3d/${propertyId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: ctx.discordId(), items }),
        });
        resultLine.className = "result-line win";
        resultLine.textContent = "Saved!";
      } catch (err) {
        resultLine.className = "result-line lose";
        resultLine.textContent = err.message;
      }
    });
    sidebar.append(saveButton, resultLine);
  }
  renderSidebar();

  function onCanvasClick(event) {
    if (!editMode) return;
    const rect = canvas.getBoundingClientRect();
    const pointer = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObjects(
      [...placed.values()].filter((p) => !p.structural).map((p) => p.object3D),
      true
    );
    if (hits.length === 0) {
      selectedId = null;
      renderSidebar();
      return;
    }
    let node = hits[0].object;
    while (node && node.userData.placedId == null) node = node.parent;
    selectedId = node ? node.userData.placedId : null;
    renderSidebar();
  }
  canvas.addEventListener("click", onCanvasClick);

  let rafHandle = 0;
  function frame() {
    rafHandle = requestAnimationFrame(frame);
    controls.update();
    renderer.render(scene, camera);
  }
  rafHandle = requestAnimationFrame(frame);

  function cleanup() {
    cancelAnimationFrame(rafHandle);
    canvas.removeEventListener("click", onCanvasClick);
    controls.dispose();
    renderer.dispose();
  }
}
