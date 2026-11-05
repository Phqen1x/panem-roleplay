// Builds one Blocky Characters instance: the `outfit` mesh (`character-
// {outfit}.glb`, one of 18) wearing the `palette` recolor texture
// (`texture-{palette}.png`, one of 18) -- see `ASSETS_BASE` below for why
// mixing outfit and palette independently works at all (every outfit
// mesh shares the same UV layout, confirmed against the vendored assets
// before this feature was built). Each instance gets its own cloned
// material before the texture swap -- sharing a material between two
// character instances would make them repaint each other the moment
// either one's palette changes.
import * as THREE from "../vendor/three.module.min.js";
import { loadModel, cloneModel, loadTextureCached } from "./loader.js";

export const ASSETS_BASE = "/assets3d";

function characterModelUrl(outfit) {
  return `${ASSETS_BASE}/characters/character-${outfit}.glb`;
}

function paletteTextureUrl(palette) {
  return `${ASSETS_BASE}/characters/Textures/texture-${palette}.png`;
}

// A handful of named clips every Blocky Characters model ships (27 total;
// see `static/assets3d/characters/LICENSE.txt`'s pack for the rest) --
// the only two this feature's movement actually needs.
export const CLIP_IDLE = "idle";
export const CLIP_WALK = "walk";

export async function createCharacter(outfit, palette) {
  const [{ scene, animations }, texture] = await Promise.all([
    loadModel(characterModelUrl(outfit)),
    loadTextureCached(paletteTextureUrl(palette)),
  ]);
  const instance = cloneModel(scene);
  instance.traverse((node) => {
    if (!node.isMesh || !node.material) return;
    const material = node.material.clone();
    material.map = texture;
    material.needsUpdate = true;
    node.material = material;
  });

  const mixer = new THREE.AnimationMixer(instance);
  const clips = new Map(animations.map((clip) => [clip.name, clip]));
  let currentAction = null;

  function play(name) {
    const clip = clips.get(name) || clips.values().next().value;
    if (!clip) return;
    const action = mixer.clipAction(clip);
    if (currentAction === action) return;
    action.reset().fadeIn(0.15).play();
    if (currentAction) currentAction.fadeOut(0.15);
    currentAction = action;
  }

  if (clips.has(CLIP_IDLE)) play(CLIP_IDLE);

  return {
    object3D: instance,
    update(deltaSeconds) {
      mixer.update(deltaSeconds);
    },
    setMoving(moving) {
      play(moving && clips.has(CLIP_WALK) ? CLIP_WALK : CLIP_IDLE);
    },
    dispose() {
      mixer.stopAllAction();
    },
  };
}
