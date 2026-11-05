// Builds one Blocky Characters instance assembled from four independently
// chosen body parts (head/torso/arms/legs), each cut from any of the 18
// vendored outfit meshes (`character-{letter}.glb`) and independently
// recolored from any of the 18 vendored palette textures (`texture-
// {letter}.png`).
//
// This only works because every outfit file shares one rig -- confirmed
// by actually parsing several outfit files with this exact vendored
// GLTFLoader before this was built, not assumed: each one's scene graph
// is `root` -> [`leg-left`, `leg-right`, `torso`], `torso` -> [`arm-left`,
// `arm-right`, `head`], six plain (non-skinned) meshes with *identical*
// local position/quaternion/scale on every outfit letter. So a part
// "plugs in" correctly regardless of which outfit file its geometry came
// from -- this module just builds a fresh `root` hierarchy with that same
// shape and names, grafting a cloned mesh from the right source file onto
// each slot, textured with the right palette. Animation clips drive
// nodes by name (`"torso.quaternion"`, etc. -- also confirmed by parsing,
// not assumed), so as long as the assembled hierarchy keeps those same
// names, any outfit's animation set drives it correctly; this always
// pulls its animations from one fixed reference file (`REFERENCE_SHAPE`)
// rather than whichever shapes happen to be selected, since they're
// identical across every outfit anyway and that way there's only ever one
// copy of the 27 clips in memory.
import * as THREE from "../vendor/three.module.min.js";
import { loadModel, loadTextureCached } from "./loader.js";

export const ASSETS_BASE = "/assets3d";
const REFERENCE_SHAPE = "a";

function characterModelUrl(letter) {
  return `${ASSETS_BASE}/characters/character-${letter}.glb`;
}

function paletteTextureUrl(letter) {
  return `${ASSETS_BASE}/characters/Textures/texture-${letter}.png`;
}

// A handful of named clips every Blocky Characters model ships (27 total;
// see `static/assets3d/characters/LICENSE.txt`'s pack for the rest) --
// the only two this feature's movement actually needs.
export const CLIP_IDLE = "idle";
export const CLIP_WALK = "walk";

function findNamed(root, name) {
  let found = null;
  root.traverse((node) => {
    if (!found && node.name === name) found = node;
  });
  return found;
}

// Cuts `nodeName` out of `sourceScene` as an independent mesh (no shared
// material, no inherited children) wearing `texture`. `clone(false)` is
// load-bearing for `torso` specifically: a *recursive* clone would also
// drag along that source file's own `arm-left`/`arm-right`/`head`
// children, which this deliberately replaces with parts from whichever
// files those slots actually selected.
function cutPart(sourceScene, nodeName, texture) {
  const source = findNamed(sourceScene, nodeName);
  const part = source.clone(false);
  const material = source.material.clone();
  material.map = texture;
  material.needsUpdate = true;
  part.material = material;
  return part;
}

async function loadShapes(letters) {
  const entries = await Promise.all(
    [...letters].map(async (letter) => [letter, (await loadModel(characterModelUrl(letter))).scene])
  );
  return new Map(entries);
}

async function loadPalettes(letters) {
  const entries = await Promise.all(
    [...letters].map(async (letter) => [letter, await loadTextureCached(paletteTextureUrl(letter))])
  );
  return new Map(entries);
}

/**
 * `parts`: `{head, head_color, torso, torso_color, arms, arms_color,
 * legs, legs_color}` -- the exact shape `Character.block_parts`/
 * `panem_shared.block_models.DEFAULT_APPEARANCE` use, each value a single
 * `a`-`r` letter.
 */
export async function createCharacter(parts) {
  const shapeLetters = new Set([parts.head, parts.torso, parts.arms, parts.legs, REFERENCE_SHAPE]);
  const colorLetters = new Set([
    parts.head_color,
    parts.torso_color,
    parts.arms_color,
    parts.legs_color,
  ]);

  const [shapes, colors, reference] = await Promise.all([
    loadShapes(shapeLetters),
    loadPalettes(colorLetters),
    loadModel(characterModelUrl(REFERENCE_SHAPE)),
  ]);

  const referenceRoot = findNamed(shapes.get(REFERENCE_SHAPE), "root");
  const root = new THREE.Object3D();
  root.name = "root";
  root.position.copy(referenceRoot.position);
  root.quaternion.copy(referenceRoot.quaternion);
  root.scale.copy(referenceRoot.scale);

  const legLeft = cutPart(shapes.get(parts.legs), "leg-left", colors.get(parts.legs_color));
  const legRight = cutPart(shapes.get(parts.legs), "leg-right", colors.get(parts.legs_color));
  const torso = cutPart(shapes.get(parts.torso), "torso", colors.get(parts.torso_color));
  const armLeft = cutPart(shapes.get(parts.arms), "arm-left", colors.get(parts.arms_color));
  const armRight = cutPart(shapes.get(parts.arms), "arm-right", colors.get(parts.arms_color));
  const head = cutPart(shapes.get(parts.head), "head", colors.get(parts.head_color));

  root.add(legLeft, legRight, torso);
  torso.add(armLeft, armRight, head);

  const mixer = new THREE.AnimationMixer(root);
  const clips = new Map(reference.animations.map((clip) => [clip.name, clip]));
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
    object3D: root,
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
