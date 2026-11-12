// The Capitol's 3D map: `tabs/map.js` mounts this instead of its usual 2D
// schematic canvas whenever the selected district is the Capitol (id 0).
// Movement/positions are server-authoritative and arrive over one
// WebSocket (`/ws/map3d/{district_id}/room`, `panem_api.map3d_routes.
// Map3DRoom`) exactly the way `tabs/party.js`/`party_routes.PartyRoom`
// already do it for the Party Room -- same full-snapshot-at-a-steady-
// cadence shape, same client-side position easing, same WASD-via-
// setInterval input loop. The building/road layout itself is fetched
// once over plain REST (`GET .../map3d/{district_id}/layout`); it never
// changes mid-session, unlike the players/NPCs walking around in it.
//
// Interaction is proximity-gated, not click-to-select -- walk up to a
// building or NPC and press E, the same "walk up to it" posture
// `party_routes.py`'s own `_near()` uses for its stations. Only three
// outcomes exist today: the Capitol Market opens the pet store panel, a
// residential building (if you own/lease a property in this district)
// lets you step into its interior (`interior.js`), and everything else
// (government, workplace, station, jail) just names itself -- "map
// interaction can be added later" was the feature request's own words,
// and this is the groundwork that later work plugs into.
import * as THREE from "../vendor/three.module.min.js";
import { el } from "../tabs/_shared.js?v=8";
import { loadModel, cloneModel } from "./loader.js";
import { createCharacter, ASSETS_BASE } from "./character.js";
import { openCustomizer } from "./customizer.js";
import { openPetStore } from "./pets_panel.js";
import { enterInterior } from "./interior.js";

const CANVAS_WIDTH = 960;
const CANVAS_HEIGHT = 540;
const MOVE_SEND_INTERVAL_MS = 80;
const INTERACT_RADIUS = 6.0;

const MOVE_KEYS = {
  w: "up",
  arrowup: "up",
  s: "down",
  arrowdown: "down",
  a: "left",
  arrowleft: "left",
  d: "right",
  arrowright: "right",
};

function wsUrlFor(districtId) {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/ws/map3d/${districtId}/room`;
}

function distance2D(ax, az, bx, bz) {
  return Math.hypot(ax - bx, az - bz);
}

export async function mountCity3D(container, ctx, districtId) {
  const character = ctx.characters().find((c) => c.id === ctx.characterId());
  if (!character) {
    container.append(el("p", { class: "tab-status" }, "Pick a character above first."));
    return { unmount() {} };
  }
  const discordId = ctx.discordId();
  const characterId = character.id;

  const statusLine = el("p", { class: "tab-status" }, "Loading the Capitol…");
  const hud = el("div", { class: "map3d-hud" });
  const canvasWrap = el("div", { class: "map3d-canvas-wrap" });
  const canvas = el("canvas", {
    width: String(CANVAS_WIDTH),
    height: String(CANVAS_HEIGHT),
    class: "map3d-canvas",
  });
  canvasWrap.append(canvas, hud);
  const overlayHost = el("div", { class: "map3d-overlay-host" });
  const toolbar = el(
    "div",
    { class: "map3d-toolbar" },
    el(
      "button",
      {
        class: "btn secondary",
        type: "button",
        onclick: () =>
          openCustomizer(overlayHost, ctx, characterId, (parts) => {
            if (localPlayer) localPlayer.setAppearance(parts);
          }),
      },
      "Customize Character"
    )
  );
  const wrapper = el(
    "div",
    { class: "map3d-wrap" },
    statusLine,
    toolbar,
    canvasWrap,
    el("p", { class: "tab-status" }, "Move with WASD or the arrow keys. Press E to interact."),
    overlayHost
  );
  container.append(wrapper);

  // ------------------------------------------------------------- scene

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(CANVAS_WIDTH, CANVAS_HEIGHT, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  const skyColor = 0x9fb8c8;
  scene.background = new THREE.Color(skyColor);
  // A city this dense needs a distance cue, or the far edge of the grid
  // just pops into view against flat sky -- cheap fog does the job
  // without needing a skybox/horizon asset this pack doesn't have.
  scene.fog = new THREE.Fog(skyColor, 45, 150);

  const camera = new THREE.PerspectiveCamera(58, CANVAS_WIDTH / CANVAS_HEIGHT, 0.1, 400);

  scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.3));
  const sun = new THREE.DirectionalLight(0xffffff, 1.3);
  sun.position.set(30, 60, 20);
  scene.add(sun);

  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(400, 400),
    new THREE.MeshStandardMaterial({ color: 0x75797e })
  );
  ground.rotation.x = -Math.PI / 2;
  scene.add(ground);
  // A faint pavement grid -- the ground plane alone reads as an empty
  // void even with buildings on it; this gives it a sense of surface
  // without needing a dedicated pavement texture.
  const groundGrid = new THREE.GridHelper(400, 200, 0x5a5e63, 0x5a5e63);
  groundGrid.position.y = 0.01;
  groundGrid.material.opacity = 0.25;
  groundGrid.material.transparent = true;
  scene.add(groundGrid);

  let contextLost = false;
  canvas.addEventListener("webglcontextlost", (event) => {
    event.preventDefault();
    contextLost = true;
    statusLine.textContent = "The 3D view was lost -- switch tabs and back to reload it.";
  });

  // ------------------------------------------------------------ layout

  let layout;
  try {
    layout = await ctx.apiFetch(`/activity/dashboard/map3d/${districtId}/layout`);
  } catch (err) {
    statusLine.textContent = `Could not load the city: ${err.message}`;
    return { unmount() {} };
  }

  await Promise.all(
    layout.buildings.map(async (building) => {
      if (!building.model) return; // PUBLIC location or empty filler plot -- open ground, no mesh
      try {
        const { scene: modelScene } = await loadModel(`${ASSETS_BASE}/city/${building.model}.glb`);
        const instance = cloneModel(modelScene);
        instance.position.set(building.x, 0, building.z);
        instance.rotation.y = THREE.MathUtils.degToRad(building.rotation_deg);
        scene.add(instance);
      } catch {
        // Missing/broken asset shouldn't take the whole city down -- that
        // location just has no visible building, same as a PUBLIC one.
      }
    })
  );

  await Promise.all(
    layout.roads.map(async (road) => {
      try {
        const { scene: modelScene } = await loadModel(`${ASSETS_BASE}/roads/${road.model}.glb`);
        const instance = cloneModel(modelScene);
        instance.position.set(road.x, 0, road.z);
        instance.rotation.y = THREE.MathUtils.degToRad(road.rotation_deg);
        scene.add(instance);
      } catch {
        // Same posture as a missing building model above.
      }
    })
  );

  // ----------------------------------------------------------- player

  let localPlayer = null;
  try {
    const appearance = await ctx.apiFetch(
      `/activity/dashboard/map3d/character/${characterId}?discord_id=${encodeURIComponent(discordId)}`
    );
    const built = await createCharacter(appearance);
    built.object3D.position.set(layout.spawn_x, 0, layout.spawn_z);
    scene.add(built.object3D);
    localPlayer = {
      ...built,
      x: layout.spawn_x,
      z: layout.spawn_z,
      facingDeg: 0,
      async setAppearance(parts) {
        const rebuilt = await createCharacter(parts);
        rebuilt.object3D.position.copy(this.object3D.position);
        rebuilt.object3D.rotation.y = this.object3D.rotation.y;
        scene.remove(this.object3D);
        this.dispose();
        this.object3D = rebuilt.object3D;
        this.update = rebuilt.update;
        this.setMoving = rebuilt.setMoving;
        this.dispose = rebuilt.dispose;
        scene.add(this.object3D);
      },
    };
  } catch (err) {
    statusLine.textContent = `Could not load your character: ${err.message}`;
  }

  // ------------------------------------------------------- remote NPCs

  const npcEntities = new Map(); // id -> {character, targetX, targetZ, meta}
  await Promise.all(
    layout.npcs.map(async (npc) => {
      try {
        const built = await createCharacter(npc.parts);
        built.object3D.position.set(npc.home_x, 0, npc.home_z);
        scene.add(built.object3D);
        npcEntities.set(npc.id, {
          character: built,
          x: npc.home_x,
          z: npc.home_z,
          targetX: npc.home_x,
          targetZ: npc.home_z,
          meta: npc,
        });
      } catch {
        // No model loaded for this NPC -- it just doesn't appear, rather
        // than blocking every other NPC/the whole scene from loading.
      }
    })
  );

  // -------------------------------------------------------- remote players

  const remotePlayers = new Map(); // character_id -> {character, x, z, targetX, targetZ, meta}

  async function ensureRemotePlayer(meta) {
    let remote = remotePlayers.get(meta.character_id);
    if (remote) return remote;
    const built = await createCharacter(meta.parts);
    built.object3D.position.set(meta.x, 0, meta.z);
    scene.add(built.object3D);
    remote = { character: built, x: meta.x, z: meta.z, targetX: meta.x, targetZ: meta.z, meta };
    remotePlayers.set(meta.character_id, remote);
    return remote;
  }

  // --------------------------------------------------------------- ws

  let ws = null;
  let wsOpen = false;
  try {
    ws = new WebSocket(wsUrlFor(districtId));
    ws.addEventListener("open", () => {
      wsOpen = true;
      ws.send(JSON.stringify({ type: "join", discord_id: discordId, character_id: characterId }));
      statusLine.textContent = "";
    });
    ws.addEventListener("close", () => {
      wsOpen = false;
      statusLine.textContent = "Disconnected from the live map -- switch tabs and back to reconnect.";
    });
    ws.addEventListener("error", () => {
      statusLine.textContent = "Couldn't reach the live map.";
    });
    ws.addEventListener("message", (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      if (!payload || payload.type !== "state") return;
      handleState(payload);
    });
  } catch {
    statusLine.textContent = "Couldn't connect to the live map.";
  }

  function handleState(state) {
    const seenPlayerIds = new Set();
    for (const p of state.players) {
      if (p.character_id === characterId) continue; // that's us -- server-authoritative but we draw locally
      seenPlayerIds.add(p.character_id);
      const remote = remotePlayers.get(p.character_id);
      if (remote) {
        remote.targetX = p.x;
        remote.targetZ = p.z;
        remote.meta = p;
        remote.character.setMoving(p.moving);
      } else {
        ensureRemotePlayer(p);
      }
    }
    for (const [id, remote] of remotePlayers) {
      if (!seenPlayerIds.has(id)) {
        scene.remove(remote.character.object3D);
        remote.character.dispose();
        remotePlayers.delete(id);
      }
    }
    for (const n of state.npcs) {
      const entity = npcEntities.get(n.id);
      if (!entity) continue;
      entity.targetX = n.x;
      entity.targetZ = n.z;
    }
  }

  // ------------------------------------------------------------- input

  const heldDirections = new Set();
  function onKeyDown(event) {
    const dir = MOVE_KEYS[event.key.toLowerCase()];
    if (dir) {
      heldDirections.add(dir);
      event.preventDefault();
      return;
    }
    if (event.key.toLowerCase() === "e") {
      handleInteract();
    }
  }
  function onKeyUp(event) {
    const dir = MOVE_KEYS[event.key.toLowerCase()];
    if (dir) heldDirections.delete(dir);
  }
  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", onKeyUp);

  function currentDirection() {
    let dx = 0;
    let dz = 0;
    if (heldDirections.has("left")) dx -= 1;
    if (heldDirections.has("right")) dx += 1;
    if (heldDirections.has("up")) dz -= 1;
    if (heldDirections.has("down")) dz += 1;
    return { dx, dz };
  }

  const sendTimer = setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const { dx, dz } = currentDirection();
    ws.send(JSON.stringify({ type: "move", dx, dz }));
    if (localPlayer && (dx !== 0 || dz !== 0)) {
      const length = Math.hypot(dx, dz);
      const speed = 3.2 * (MOVE_SEND_INTERVAL_MS / 1000);
      localPlayer.x += (dx / length) * speed;
      localPlayer.z += (dz / length) * speed;
      localPlayer.facingDeg = THREE.MathUtils.radToDeg(Math.atan2(dx / length, dz / length));
      localPlayer.setMoving(true);
    } else if (localPlayer) {
      localPlayer.setMoving(false);
    }
  }, MOVE_SEND_INTERVAL_MS);

  // -------------------------------------------------------- interaction

  let nearestLabel = "";

  function updateNearest() {
    if (!localPlayer) return null;
    let best = null;
    let bestDist = INTERACT_RADIUS;
    for (const building of layout.buildings) {
      if (!building.location_id) continue; // filler building -- nothing to interact with
      const d = distance2D(localPlayer.x, localPlayer.z, building.x, building.z);
      if (d <= bestDist) {
        best = { kind: "building", data: building };
        bestDist = d;
      }
    }
    for (const [, entity] of npcEntities) {
      const d = distance2D(localPlayer.x, localPlayer.z, entity.x, entity.z);
      if (d <= bestDist) {
        best = { kind: "npc", data: entity.meta };
        bestDist = d;
      }
    }
    return best;
  }

  async function handleInteract() {
    const nearest = updateNearest();
    if (!nearest) return;
    if (nearest.kind === "npc") {
      overlayHost.innerHTML = "";
      overlayHost.append(
        el(
          "div",
          { class: "panel map3d-popup" },
          el("h2", { text: nearest.data.name }),
          el(
            "p",
            { class: "tab-status" },
            nearest.data.job_title ? `Works as: ${nearest.data.job_title}` : "No known occupation."
          ),
          el(
            "p",
            { class: "tab-status" },
            "Talking to residents on the map is coming soon -- for now, use /talk in Discord."
          ),
          el(
            "button",
            { class: "btn secondary", type: "button", onclick: () => (overlayHost.innerHTML = "") },
            "Close"
          )
        )
      );
      return;
    }
    const building = nearest.data;
    if (building.kind === "market") {
      openPetStore(overlayHost, ctx, characterId);
      return;
    }
    if (building.kind === "residential") {
      await openResidentialPicker(building);
      return;
    }
    overlayHost.innerHTML = "";
    overlayHost.append(
      el(
        "div",
        { class: "panel map3d-popup" },
        el("h2", { text: building.name }),
        el("p", { class: "tab-status" }, "There's nothing to do here yet -- more is coming soon."),
        el(
          "button",
          { class: "btn secondary", type: "button", onclick: () => (overlayHost.innerHTML = "") },
          "Close"
        )
      )
    );
  }

  async function openResidentialPicker(building) {
    overlayHost.innerHTML = "";
    const body = el("div", {}, el("p", { class: "tab-status" }, "Loading your properties…"));
    overlayHost.append(
      el(
        "div",
        { class: "panel map3d-popup" },
        el("h2", { text: building.name }),
        body,
        el(
          "button",
          { class: "btn secondary", type: "button", onclick: () => (overlayHost.innerHTML = "") },
          "Close"
        )
      )
    );
    try {
      const status = await ctx.apiFetch(
        `/activity/dashboard/housing/${characterId}?discord_id=${encodeURIComponent(discordId)}`
      );
      const mine = status.owned.filter(
        (p) => p.district_name === ctx.districtName(districtId) && p.kind !== "inn"
      );
      body.innerHTML = "";
      if (mine.length === 0) {
        body.append(
          el(
            "p",
            { class: "tab-status" },
            "You don't own or rent a home here yet -- visit the Housing tab to buy or rent one."
          )
        );
        return;
      }
      for (const property of mine) {
        body.append(
          el(
            "div",
            { class: "field-row" },
            el("span", {}, `#${property.id} ${property.kind} (${property.tier})`),
            el(
              "button",
              {
                class: "btn",
                type: "button",
                onclick: () =>
                  enterInterior(overlayHost, ctx, property.id, () => {
                    overlayHost.innerHTML = "";
                  }),
              },
              "Go inside"
            )
          )
        );
      }
    } catch (err) {
      body.innerHTML = "";
      body.append(el("p", { class: "result-line lose" }, err.message));
    }
  }

  // ------------------------------------------------------------- render

  let rafHandle = 0;
  let lastFrameAt = performance.now();

  function frame(now) {
    rafHandle = requestAnimationFrame(frame);
    if (contextLost) return;
    const dt = Math.min((now - lastFrameAt) / 1000, 0.25);
    lastFrameAt = now;
    const ease = 1 - Math.exp(-dt * 10);

    for (const remote of remotePlayers.values()) {
      remote.x += (remote.targetX - remote.x) * ease;
      remote.z += (remote.targetZ - remote.z) * ease;
      remote.character.object3D.position.set(remote.x, 0, remote.z);
      remote.character.update(dt);
    }
    for (const entity of npcEntities.values()) {
      entity.x += (entity.targetX - entity.x) * ease;
      entity.z += (entity.targetZ - entity.z) * ease;
      entity.character.object3D.position.set(entity.x, 0, entity.z);
      entity.character.update(dt);
    }
    if (localPlayer) {
      localPlayer.object3D.position.set(localPlayer.x, 0, localPlayer.z);
      localPlayer.object3D.rotation.y = THREE.MathUtils.degToRad(localPlayer.facingDeg);
      localPlayer.update(dt);

      camera.position.set(localPlayer.x, 11, localPlayer.z + 11);
      camera.lookAt(localPlayer.x, 1.2, localPlayer.z);

      const nearest = updateNearest();
      const label = nearest
        ? nearest.kind === "npc"
          ? `Press E: Talk to ${nearest.data.name}`
          : `Press E: ${nearest.data.kind === "market" ? "Visit the pet store" : nearest.data.kind === "residential" ? "Enter a home" : `Look at ${nearest.data.name}`}`
        : "";
      if (label !== nearestLabel) {
        nearestLabel = label;
        hud.textContent = label;
      }
    }

    renderer.render(scene, camera);
  }
  rafHandle = requestAnimationFrame(frame);

  return {
    unmount() {
      cancelAnimationFrame(rafHandle);
      clearInterval(sendTimer);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      if (ws) {
        try {
          ws.close();
        } catch {
          // Already closed.
        }
      }
      renderer.dispose();
    },
  };
}
