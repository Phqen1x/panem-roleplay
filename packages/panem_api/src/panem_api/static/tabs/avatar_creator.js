// A real-time 3D portrait renderer for the Character tab's visual
// customizer. The body is a real downloaded, rigged human mesh (Xbot, from
// three.js's own bundled example assets -- originally a Mixamo character;
// see `vendor/models/xbot.LICENSE.md`), retextured and reshaped per-trait
// via its actual skeleton (bone scaling for build/height/face shape). Face
// features, hair, facial hair, jewelry, and the clothing overlay are still
// built from Three.js primitive geometries the same way the first version
// of this file did -- a fixed downloaded mesh can't vary its face shape or
// hairstyle across 5x10 trait combinations, but scaling/attaching
// procedural pieces onto its real skeleton can. Three.js itself, the
// GLTFLoader/SkeletonUtils addon modules, and the model are all vendored
// locally under `/vendor/` (see the LICENSE files next to each) rather
// than loaded from a CDN, mirroring `/vendor/discord-embedded-app-sdk.js`:
// a real Discord Activity iframe only ever fetches from this server's own
// origin.
//
// `traits` is the same shape `panem_shared.appearance.
// DEFAULT_APPEARANCE_TRAITS` returns and `validate_appearance_traits`
// normalizes to -- every field is always present by the time this is
// called from `character.js`.
//
// Unlike the old stateless `renderAvatar(canvas, traits)`, a 3D scene needs
// a persistent render loop (for the orbit/auto-rotate camera) and owns
// real GPU resources, so the API here is a mount/update/dispose lifecycle:
//   const handle = mountAvatar(container, traits);
//   handle.update(newTraits); // rebuilds the rig in place
//   handle.dispose();         // stops the render loop, frees the GL context
// `character.js` is responsible for calling `dispose()` whenever a mounted
// avatar's container leaves the DOM (tab unmount, card refresh) --
// browsers cap the number of live WebGL contexts per page, so a leaked
// context here would eventually break every other canvas on the tab.

import * as THREE from "/vendor/three.module.min.js";
import { GLTFLoader } from "/vendor/three/GLTFLoader.js";
import { clone as cloneSkeleton } from "/vendor/three/SkeletonUtils.js";

export const WIDTH = 220;
export const HEIGHT = 260;

const BODY_MODEL_URL = "/vendor/models/xbot.glb";

const BUILD_SCALE = { slim: 0.85, athletic: 0.95, average: 1.0, stocky: 1.14, heavyset: 1.3 };

// Bones whose x/z (not y, so bone-chain length/positioning is untouched)
// get scaled by BUILD_SCALE to fatten/slim the downloaded body mesh via
// its own skinning, rather than needing a different mesh per build.
const GIRTH_BONES = [
  "mixamorigSpine", "mixamorigSpine1", "mixamorigSpine2",
  "mixamorigLeftArm", "mixamorigRightArm", "mixamorigLeftForeArm", "mixamorigRightForeArm",
  "mixamorigLeftUpLeg", "mixamorigRightUpLeg", "mixamorigLeftLeg", "mixamorigRightLeg",
];

// Non-uniform scale applied to the head bone per face_shape -- the
// downloaded mesh has one fixed head shape, but scaling the bone it's
// skinned to reshapes it directly (wider/flatter for "square", stretched
// for "long", etc.) without any seam or second head mesh.
const FACE_SHAPE_HEAD_SCALE = {
  square: { x: 1.12, y: 0.95, z: 1.05 },
  heart: { x: 0.95, y: 1.05, z: 0.98 },
  long: { x: 0.88, y: 1.22, z: 0.9 },
  round: { x: 1.08, y: 0.92, z: 1.05 },
  oval: { x: 1, y: 1, z: 1 },
};

function shade(hex, factor) {
  return new THREE.Color(hex).multiplyScalar(factor);
}

function skinColorFor(traits) {
  return traits.age_look === "weathered" ? shade(traits.skin_tone, 0.92) : new THREE.Color(traits.skin_tone);
}

function hairMaterial(traits) {
  return new THREE.MeshStandardMaterial({ color: new THREE.Color(traits.hair_color), roughness: 0.7 });
}

function buildEyes(headRadius, traits) {
  const group = new THREE.Group();
  const eyeDx = headRadius * 0.42;
  const eyeY = headRadius * 0.08;
  const eyeZ = headRadius * 0.86;
  const scleraR = headRadius * (traits.age_look === "youthful" ? 0.16 : 0.13);
  const scleraMat = new THREE.MeshStandardMaterial({ color: 0xf4ece1, roughness: 0.4 });
  const irisMat = new THREE.MeshStandardMaterial({ color: new THREE.Color(traits.eye_color), roughness: 0.3 });
  const pupilMat = new THREE.MeshStandardMaterial({ color: 0x10141a, roughness: 0.2 });
  const browMat = new THREE.MeshStandardMaterial({ color: shade(traits.hair_color, 1.1), roughness: 0.8 });
  const browTilt =
    traits.expression === "fierce" ? 0.3 : traits.expression === "gentle" ? -0.2 : traits.expression === "serious" ? 0.12 : 0;

  for (const side of [-1, 1]) {
    const eyeX = side * eyeDx;
    const sclera = new THREE.Mesh(new THREE.SphereGeometry(scleraR, 8, 6), scleraMat);
    sclera.position.set(eyeX, eyeY, eyeZ);
    group.add(sclera);
    const iris = new THREE.Mesh(new THREE.SphereGeometry(scleraR * 0.62, 8, 6), irisMat);
    iris.position.set(eyeX, eyeY, eyeZ + scleraR * 0.55);
    group.add(iris);
    const pupil = new THREE.Mesh(new THREE.SphereGeometry(scleraR * 0.3, 6, 6), pupilMat);
    pupil.position.set(eyeX, eyeY, eyeZ + scleraR * 0.78);
    group.add(pupil);

    const brow = new THREE.Mesh(new THREE.BoxGeometry(scleraR * 2.4, scleraR * 0.5, scleraR * 0.4), browMat);
    brow.position.set(eyeX, eyeY + scleraR * 2.1, eyeZ - scleraR * 0.1);
    brow.rotation.z = side * browTilt;
    group.add(brow);
  }
  return group;
}

function buildMouth(headRadius, traits) {
  const mat = new THREE.MeshStandardMaterial({ color: 0x5a3226, roughness: 0.6 });
  const y = -headRadius * 0.52;
  const z = headRadius * 0.88;
  const width = headRadius * 0.38;
  const expr = traits.expression;
  let mesh;
  if (expr === "smiling" || expr === "gentle") {
    const curve = expr === "smiling" ? 0.55 : 0.3;
    const torus = new THREE.TorusGeometry(width, headRadius * 0.04, 6, 12, Math.PI * curve);
    mesh = new THREE.Mesh(torus, mat);
    mesh.rotation.z = Math.PI * (1 - curve / 2);
    mesh.position.set(0, y + headRadius * 0.06, z);
  } else if (expr === "fierce" || expr === "serious") {
    mesh = new THREE.Mesh(new THREE.BoxGeometry(width * 1.8, headRadius * 0.05, headRadius * 0.08), mat);
    mesh.rotation.z = expr === "fierce" ? 0.08 : 0;
    mesh.position.set(0, y, z);
  } else {
    mesh = new THREE.Mesh(new THREE.BoxGeometry(width * 1.7, headRadius * 0.045, headRadius * 0.08), mat);
    mesh.position.set(0, y, z);
  }
  return mesh;
}

function buildFacialHair(headRadius, traits) {
  if (traits.facial_hair === "none") return null;
  const group = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(traits.hair_color), roughness: 0.85 });
  if (traits.facial_hair === "stubble") {
    mat.transparent = true;
    mat.opacity = 0.35;
    const patch = new THREE.Mesh(new THREE.SphereGeometry(headRadius * 0.65, 10, 8, 0, Math.PI * 2, Math.PI * 0.55, Math.PI * 0.4), mat);
    patch.position.set(0, -headRadius * 0.15, 0);
    group.add(patch);
    return group;
  }
  if (traits.facial_hair === "mustache" || traits.facial_hair === "full") {
    const stache = new THREE.Mesh(new THREE.BoxGeometry(headRadius * 0.6, headRadius * 0.14, headRadius * 0.16), mat);
    stache.position.set(0, -headRadius * 0.4, headRadius * 0.9);
    group.add(stache);
  }
  if (traits.facial_hair === "beard" || traits.facial_hair === "full") {
    const beard = new THREE.Mesh(new THREE.SphereGeometry(headRadius * 0.72, 10, 8, 0, Math.PI * 2, Math.PI * 0.5, Math.PI * 0.55), mat);
    beard.position.set(0, -headRadius * 0.5, -headRadius * 0.05);
    beard.scale.set(1, 1.1, 0.9);
    group.add(beard);
  }
  return group;
}

// Hair -- one shape family per style, built from the same low-poly
// primitives as everything else. `capRadius` is the tight dome every
// non-bald/buzz style shares as its base.
function buildHair(headRadius, traits) {
  if (traits.hair_style === "bald") return null;
  const group = new THREE.Group();
  const mat = hairMaterial(traits);
  const capR = headRadius * 1.06;

  // thetaLength of 0.42*PI stops the dome just above brow height (eyes sit
  // near the head's vertical center) -- a wider sweep here previously
  // wrapped hair all the way down past the eyes on every style that shares
  // this cap, reading as a solid helmet rather than a hairline.
  function crownCap(radiusScale = 1) {
    const cap = new THREE.Mesh(
      new THREE.SphereGeometry(capR * radiusScale, 12, 8, 0, Math.PI * 2, 0, Math.PI * 0.42),
      mat
    );
    cap.position.y = headRadius * 0.05;
    return cap;
  }

  if (traits.hair_style === "buzz") {
    const cap = crownCap(1.02);
    cap.scale.set(1, 0.6, 1);
    group.add(cap);
    return group;
  }
  if (traits.hair_style === "mohawk") {
    const fin = new THREE.Mesh(new THREE.BoxGeometry(headRadius * 0.22, headRadius * 0.9, headRadius * 1.5), mat);
    fin.position.y = headRadius * 0.85;
    group.add(fin);
    return group;
  }
  if (traits.hair_style === "curly") {
    group.add(crownCap());
    const rng = (seed) => Math.abs(Math.sin(seed * 12.9898) * 43758.5453) % 1;
    for (let i = 0; i < 10; i += 1) {
      const angle = (i / 10) * Math.PI * 2;
      const puff = new THREE.Mesh(new THREE.IcosahedronGeometry(headRadius * 0.24, 0), mat);
      const wob = 0.08 + rng(i) * 0.06;
      puff.position.set(Math.cos(angle) * capR * 0.75, headRadius * (0.35 + wob), Math.sin(angle) * capR * 0.6);
      group.add(puff);
    }
    return group;
  }
  if (traits.hair_style === "bun") {
    group.add(crownCap());
    const bun = new THREE.Mesh(new THREE.SphereGeometry(headRadius * 0.32, 10, 8), mat);
    bun.position.set(0, headRadius * 0.55, -headRadius * 0.85);
    group.add(bun);
    return group;
  }
  if (traits.hair_style === "bob") {
    group.add(crownCap());
    for (const side of [-1, 1]) {
      const flap = new THREE.Mesh(new THREE.CapsuleGeometry(headRadius * 0.22, headRadius * 0.9, 3, 6), mat);
      flap.position.set(side * headRadius * 0.95, -headRadius * 0.35, headRadius * 0.05);
      group.add(flap);
    }
    return group;
  }
  // short / shoulder / long / braid all start from the same crown cap.
  group.add(crownCap());
  if (traits.hair_style === "shoulder" || traits.hair_style === "long") {
    const length = traits.hair_style === "long" ? 1.9 : 1.1;
    const back = new THREE.Mesh(new THREE.CapsuleGeometry(headRadius * 0.62, headRadius * length, 3, 8), mat);
    back.position.set(0, -headRadius * (0.15 + length * 0.42), -headRadius * 0.5);
    back.scale.set(1, 1, 0.55);
    group.add(back);
  } else if (traits.hair_style === "braid") {
    const segments = 5;
    for (let i = 0; i < segments; i += 1) {
      const seg = new THREE.Mesh(new THREE.CapsuleGeometry(headRadius * 0.16, headRadius * 0.32, 2, 6), mat);
      const wag = i % 2 === 0 ? headRadius * 0.08 : -headRadius * 0.08;
      seg.position.set(wag, -headRadius * (0.5 + i * 0.42), -headRadius * 0.75);
      group.add(seg);
    }
  }
  return group;
}

// All positions here are in the character root's local space -- callers
// pass `headCenterY`/`neckBaseY` (already-known world-ish Y offsets within
// that space) rather than this function reaching into the head/neck groups
// itself, so every piece can be placed with one `position.set(...)` and no
// post-hoc adjustment pass.
function buildJewelry(headRadius, headCenterY, neckBaseY, jewelry, accentColor) {
  if (jewelry.length === 0) return null;
  const group = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(accentColor), roughness: 0.3, metalness: 0.6 });
  const glassesMat = new THREE.MeshStandardMaterial({ color: 0x20242c, roughness: 0.4, metalness: 0.5 });

  if (jewelry.includes("earrings")) {
    for (const side of [-1, 1]) {
      const bead = new THREE.Mesh(new THREE.SphereGeometry(headRadius * 0.08, 8, 6), mat);
      bead.position.set(side * headRadius * 1.02, headCenterY - headRadius * 0.1, headRadius * 0.15);
      group.add(bead);
    }
  }
  if (jewelry.includes("nose_ring")) {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(headRadius * 0.08, headRadius * 0.015, 6, 10), mat);
    ring.position.set(headRadius * 0.15, headCenterY - headRadius * 0.62, headRadius * 0.92);
    group.add(ring);
  }
  if (jewelry.includes("headband")) {
    const band = new THREE.Mesh(new THREE.TorusGeometry(headRadius * 1.0, headRadius * 0.09, 6, 16), mat);
    band.position.y = headCenterY + headRadius * 0.25;
    band.rotation.x = Math.PI / 2;
    group.add(band);
  }
  if (jewelry.includes("glasses")) {
    const eyeDx = headRadius * 0.42;
    const eyeY = headCenterY + headRadius * 0.08;
    const z = headRadius * 0.92;
    for (const side of [-1, 1]) {
      const lens = new THREE.Mesh(new THREE.TorusGeometry(headRadius * 0.2, headRadius * 0.025, 6, 12), glassesMat);
      lens.position.set(side * eyeDx, eyeY, z);
      group.add(lens);
    }
    const bridge = new THREE.Mesh(new THREE.CylinderGeometry(headRadius * 0.02, headRadius * 0.02, eyeDx * 0.6, 6), glassesMat);
    bridge.rotation.z = Math.PI / 2;
    bridge.position.set(0, eyeY, z);
    group.add(bridge);
  }
  if (jewelry.includes("necklace")) {
    const necklace = new THREE.Mesh(new THREE.TorusGeometry(headRadius * 0.65, headRadius * 0.045, 6, 16, Math.PI * 1.3), mat);
    necklace.position.set(0, neckBaseY + 0.05, 0.05);
    necklace.rotation.set(Math.PI * 0.55, 0, Math.PI * 0.85);
    group.add(necklace);
  }
  return group;
}

// A procedural "shirt" shell wrapped around the downloaded body mesh's
// real torso -- that mesh has no separate clothing geometry, so
// clothing_style/clothing_color still need something to paint onto.
// `height` is the real torso span (hips-to-neck) read off the body's own
// skeleton, so this shell fits bodies of any height/build instead of
// assuming a fixed unit-tall torso.
function buildTorso(traits, buildScale, shoulderWidth, height) {
  const group = new THREE.Group();
  const clothColor = new THREE.Color(traits.clothing_color);
  const clothMat = new THREE.MeshStandardMaterial({ color: clothColor, roughness: 0.8 });
  const topR = height * 0.5 * shoulderWidth * buildScale;
  const bottomR = height * 0.42 * buildScale;
  const halfH = height / 2;

  // A revolved, tapered profile (shoulders wide, waist pulled in, hem
  // flaring back out) rather than a uniform tube -- reads as a worn
  // garment with an actual silhouette instead of a pipe around the torso.
  const profile = [
    new THREE.Vector2(bottomR * 1.05, -halfH),
    new THREE.Vector2(bottomR * 0.92, -halfH * 0.55),
    new THREE.Vector2(bottomR * 0.85, -halfH * 0.08),
    new THREE.Vector2(topR * 0.9, halfH * 0.45),
    new THREE.Vector2(topR, halfH * 0.85),
    new THREE.Vector2(topR * 0.68, halfH),
  ];
  const torso = new THREE.Mesh(new THREE.LatheGeometry(profile, 16), clothMat);
  group.add(torso);
  group.userData = { topR, bottomR, height };

  if (traits.clothing_style === "formal") {
    const collar = new THREE.Mesh(new THREE.ConeGeometry(topR * 0.28, 0.3, 4), new THREE.MeshStandardMaterial({ color: 0xf4ece1, roughness: 0.5 }));
    collar.position.set(0, height * 0.42, topR * 0.55);
    collar.rotation.x = Math.PI;
    group.add(collar);
  } else if (traits.clothing_style === "ragged") {
    const tearMat = new THREE.MeshStandardMaterial({ color: shade(traits.clothing_color, 0.6), roughness: 0.95 });
    for (let i = 0; i < 3; i += 1) {
      const tear = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.22, 0.03), tearMat);
      tear.position.set(-topR * 0.4 + i * topR * 0.35, -0.05 - i * 0.12, bottomR * 0.9);
      tear.rotation.z = 0.3;
      group.add(tear);
    }
  } else if (traits.clothing_style === "capitol_fashion") {
    const gem = new THREE.Mesh(new THREE.OctahedronGeometry(topR * 0.16, 0), new THREE.MeshStandardMaterial({ color: shade(traits.clothing_color, 1.6), metalness: 0.7, roughness: 0.2 }));
    gem.position.set(0, height * 0.3, topR * 0.85);
    group.add(gem);
  } else if (traits.clothing_style === "hunter") {
    const strap = new THREE.Mesh(new THREE.BoxGeometry(0.09, height * 1.05, 0.06), new THREE.MeshStandardMaterial({ color: shade(traits.clothing_color, 0.65), roughness: 0.9 }));
    strap.position.set(topR * 0.15, 0, bottomR * 0.9);
    strap.rotation.z = 0.5;
    group.add(strap);
  } else if (traits.clothing_style === "merchant") {
    const apron = new THREE.Mesh(new THREE.CylinderGeometry(bottomR * 1.02, bottomR * 1.02, 0.16, 10, 1, true), new THREE.MeshStandardMaterial({ color: shade(traits.clothing_color, 1.4), roughness: 0.8, side: THREE.DoubleSide }));
    apron.position.y = -height * 0.28;
    group.add(apron);
  }
  return group;
}

// Clones the downloaded base body (independent skeleton + materials per
// instance -- SkeletonUtils.clone() shares geometry/materials by default,
// which would let one character's retint/scale bleed into every other
// mounted avatar) and applies this character's skin tone, clothing-accent
// tint, build (bone girth), and face shape (head bone scale).
function buildBody(baseModel, traits) {
  const body = cloneSkeleton(baseModel.scene);
  const buildScale = BUILD_SCALE[traits.build] ?? 1;
  const skinColor = skinColorFor(traits);

  // The model's raw bind pose is a T-pose (arms out horizontally) -- fine
  // for retargeting animations onto, useless for a static portrait. The
  // bundled "idle" clip's first frame is a natural standing stance, so it's
  // applied once as a static pose (not played/advanced) rather than left
  // in the bind pose or hand-tuned bone rotations guessed from scratch.
  const idleClip = THREE.AnimationClip.findByName(baseModel.animations, "idle");
  if (idleClip) {
    const mixer = new THREE.AnimationMixer(body);
    mixer.clipAction(idleClip).play();
    mixer.update(0);
  }

  body.traverse((node) => {
    if (node.isSkinnedMesh) {
      // Geometry stays shared across every mounted avatar (it's identical,
      // sizable, and never mutated) -- only the material is per-instance,
      // so disposeObject() must not free geometry it doesn't own.
      node.userData.sharedGeometry = true;
      node.material = node.material.clone();
      // Both skinned meshes are body/skin (the second is a joint-accent
      // overlay baked into the source mesh, not separate clothing) --
      // tinting them differently read as an odd diaper-and-cuffs patchwork
      // when this was tried, so both just get the skin tone; clothing
      // color/style live entirely on the procedural shirt shell below.
      node.material.color.copy(skinColor);
      node.material.roughness = traits.age_look === "weathered" ? 0.95 : 0.8;
      node.material.metalness = 0;
    } else if (node.isBone && GIRTH_BONES.includes(node.name)) {
      node.scale.set(buildScale, 1, buildScale);
    }
  });

  const headBone = body.getObjectByName("mixamorigHead");
  if (headBone) {
    const s = FACE_SHAPE_HEAD_SCALE[traits.face_shape] ?? FACE_SHAPE_HEAD_SCALE.oval;
    headBone.scale.set(s.x, s.y, s.z);
  }

  body.updateMatrixWorld(true);
  return body;
}

// Where to anchor the procedural face/hair/jewelry pieces, read from the
// body's own skeleton rather than guessed -- bone world positions are
// unaffected by the girth/face-shape bone scaling above (scale only moves
// the vertices skinned to a bone, not the bone's own transform), so this
// stays stable regardless of build/face_shape.
function computeHeadAnchor(body) {
  const headBone = body.getObjectByName("mixamorigHead");
  const neckBone = body.getObjectByName("mixamorigNeck");
  const headPos = headBone.getWorldPosition(new THREE.Vector3());
  const neckPos = neckBone.getWorldPosition(new THREE.Vector3());

  // The HeadTop_End *bone* sits wherever the original rig's author put it,
  // which turned out to be noticeably lower than the mesh's actual scalp --
  // using it as "the top of the head" pushed every face feature down onto
  // the lower half of the real head, looking like a huge bare forehead.
  // The mesh's own true (posed) top, from computeBoundingBox(), tracks the
  // actual visible skull instead of a rig-author's placement of a marker.
  let crownY = headPos.y + 0.2;
  body.traverse((node) => {
    if (node.isSkinnedMesh && node.name === "Beta_Surface") {
      node.computeBoundingBox();
      crownY = node.boundingBox.clone().applyMatrix4(node.matrixWorld).max.y;
    }
  });

  const radius = Math.max((crownY - neckPos.y) * 0.5, 0.1);
  const center = new THREE.Vector3(headPos.x, neckPos.y + radius, headPos.z + radius * 0.15);
  return { center, radius, neckY: neckPos.y };
}

function computeTorsoAnchor(body) {
  const hips = body.getObjectByName("mixamorigHips").getWorldPosition(new THREE.Vector3());
  const neck = body.getObjectByName("mixamorigNeck").getWorldPosition(new THREE.Vector3());
  const center = new THREE.Vector3(hips.x, (hips.y + neck.y) / 2, hips.z + 0.02);
  return { center, height: neck.y - hips.y };
}

// Builds the full rig around the downloaded, rigged body mesh. Total
// height still varies with `height_cm`/hair style/face shape, so
// `mountAvatar` frames the camera from the rig's actual bounds rather than
// assuming a fixed size.
function buildCharacter(baseModel, traits) {
  const root = new THREE.Group();
  const body = buildBody(baseModel, traits);
  root.add(body);

  const headInfo = computeHeadAnchor(body);
  const headR = headInfo.radius;

  const hair = buildHair(headR, traits);
  if (hair) {
    hair.position.copy(headInfo.center);
    root.add(hair);
  }
  const facialHair = buildFacialHair(headR, traits);
  if (facialHair) {
    facialHair.position.copy(headInfo.center);
    root.add(facialHair);
  }
  const eyes = buildEyes(headR, traits);
  eyes.position.copy(headInfo.center);
  root.add(eyes);
  const mouth = buildMouth(headR, traits);
  mouth.position.add(headInfo.center);
  root.add(mouth);

  const jewelry = buildJewelry(headR, headInfo.center.y, headInfo.neckY, traits.jewelry, "#d9c58a");
  if (jewelry) root.add(jewelry);

  const buildScale = BUILD_SCALE[traits.build] ?? 1;
  const shoulderWidth =
    traits.gender_presentation === "feminine" ? 0.85 : traits.gender_presentation === "masculine" ? 1.08 : 0.95;
  const torsoInfo = computeTorsoAnchor(body);
  const torso = buildTorso(traits, buildScale, shoulderWidth, torsoInfo.height);
  torso.position.copy(torsoInfo.center);
  root.add(torso);

  const heightScale = 0.82 + ((traits.height_cm - 140) / (210 - 140)) * 0.36; // 0.82..1.18
  root.scale.set(1, heightScale, 1);

  return root;
}

function disposeObject(object) {
  object.traverse((node) => {
    if (node.geometry && !node.userData.sharedGeometry) node.geometry.dispose();
    if (node.isSkinnedMesh && node.skeleton) node.skeleton.dispose();
    if (node.material) {
      const materials = Array.isArray(node.material) ? node.material : [node.material];
      for (const mat of materials) mat.dispose();
    }
  });
}

// A plain-text stand-in used when a WebGL context can't be created --
// observed in practice once enough contexts are already live in the page
// (the browser/GPU driver caps how many can exist at once; software
// rendering in particular can cap this quite low). A Discord Activity runs
// inside an embedded webview that may share that budget with other UI, so
// this is a real possibility in production, not just a test artifact. The
// returned handle matches mountAvatar's shape so callers never need to
// special-case it.
// The base body model is fetched once per page load and reused (cloned)
// by every mounted avatar -- it's a ~3MB same-origin asset, not something
// to re-download per character card.
let baseModelPromise = null;
function loadBaseModel() {
  if (!baseModelPromise) {
    const loader = new GLTFLoader();
    baseModelPromise = new Promise((resolve, reject) => {
      loader.load(BODY_MODEL_URL, (gltf) => resolve({ scene: gltf.scene, animations: gltf.animations }), undefined, reject);
    });
  }
  return baseModelPromise;
}

// A plain Box3.setFromObject(root) silently ignores GPU skinning: a
// SkinnedMesh's geometry.boundingBox reflects its raw, un-posed vertex
// buffer (skinning is applied on the GPU at render time, not to the CPU-
// side geometry), which for this body mesh is a small, distorted shape
// nothing like its actual T-pose silhouette -- discovered by comparing it
// against the body's own bone positions, which are unaffected by skinning
// and read as a normal ~1.8-unit-tall figure. SkinnedMesh.computeBoundingBox()
// (added in recent three.js) evaluates the true posed shape per vertex, so
// bounds for the body come from that instead, unioned with plain
// geometry.boundingBox for every ordinary (non-skinned) procedural mesh.
function computeCharacterBounds(root) {
  root.updateMatrixWorld(true);
  const box = new THREE.Box3();
  root.traverse((node) => {
    if (node.isSkinnedMesh && typeof node.computeBoundingBox === "function") {
      node.computeBoundingBox();
      box.union(node.boundingBox.clone().applyMatrix4(node.matrixWorld));
    } else if (node.isMesh) {
      if (!node.geometry.boundingBox) node.geometry.computeBoundingBox();
      box.union(node.geometry.boundingBox.clone().applyMatrix4(node.matrixWorld));
    }
  });
  return box;
}

function mountFallback(container) {
  const note = document.createElement("p");
  note.className = "tab-status";
  note.textContent = "3D preview unavailable (no graphics context free).";
  container.appendChild(note);
  return { update() {}, dispose() {} };
}

// Mounts a live, orbit-able 3D preview into `container` and returns a
// handle to update the traits (rebuilds the rig) or tear the scene down.
export function mountAvatar(container, traits) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true });
  } catch {
    return mountFallback(container);
  }
  if (!renderer.getContext()) {
    renderer.dispose();
    return mountFallback(container);
  }

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x171a20);

  const camera = new THREE.PerspectiveCamera(32, WIDTH / HEIGHT, 0.1, 100);
  const target = new THREE.Vector3(0, 1.5, 0);
  let theta = 0.15;
  let phi = 1.35;
  let radius = 4.4;

  function positionCamera() {
    camera.position.set(
      target.x + radius * Math.sin(phi) * Math.sin(theta),
      target.y + radius * Math.cos(phi),
      target.z + radius * Math.sin(phi) * Math.cos(theta)
    );
    camera.lookAt(target);
  }

  // Trait combinations vary the rig's real height/width a lot (a tall,
  // heavyset build with long hair vs. a short slim one with none), so the
  // camera is framed from the actual bounding box rather than a guessed
  // fixed distance -- otherwise a fixed distance either clips the extremes
  // or leaves everyone else tiny in the middle of the frame.
  function frame(object) {
    const box = computeCharacterBounds(object);
    const size = box.getSize(new THREE.Vector3());
    const vFov = (camera.fov * Math.PI) / 180;
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect);
    const distanceForHeight = size.y / 2 / Math.tan(vFov / 2);
    const distanceForWidth = size.x / 2 / Math.tan(hFov / 2);
    target.copy(box.getCenter(new THREE.Vector3()));
    radius = Math.max(distanceForHeight, distanceForWidth) * 1.25;
    positionCamera();
  }

  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(WIDTH, HEIGHT);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.domElement.style.cursor = "grab";
  renderer.domElement.style.aspectRatio = `${WIDTH} / ${HEIGHT}`;
  container.appendChild(renderer.domElement);

  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const key = new THREE.DirectionalLight(0xfff2e0, 1.1);
  key.position.set(2.5, 4, 3);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xaac8ff, 0.35);
  fill.position.set(-3, 1.5, -2);
  scene.add(fill);

  // The body model is a same-origin fetch, cached after the first mount,
  // but still asynchronous -- rebuild() re-runs on the (already-resolved,
  // after the first call) promise for both the initial build and every
  // later update() so there's exactly one code path, and a rapid sequence
  // of update() calls before the model has ever loaded just replaces
  // `latestTraits` until it resolves rather than racing several builds.
  let character = null;
  let disposed = false;
  let latestTraits = traits;
  function rebuild(newTraits) {
    latestTraits = newTraits;
    loadBaseModel()
      .then((baseModel) => {
        if (disposed) return;
        if (character) {
          scene.remove(character);
          disposeObject(character);
        }
        character = buildCharacter(baseModel, latestTraits);
        scene.add(character);
        frame(character);
      })
      .catch((error) => {
        console.error("avatar_creator: failed to load base body model", error);
      });
  }
  rebuild(traits);

  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  let idleSince = performance.now();

  function onPointerDown(event) {
    dragging = true;
    lastX = event.clientX;
    lastY = event.clientY;
    renderer.domElement.style.cursor = "grabbing";
    renderer.domElement.setPointerCapture(event.pointerId);
  }
  function onPointerMove(event) {
    if (!dragging) return;
    const dx = event.clientX - lastX;
    const dy = event.clientY - lastY;
    lastX = event.clientX;
    lastY = event.clientY;
    theta += dx * 0.01;
    phi = Math.min(Math.max(phi - dy * 0.01, 0.55), 2.4);
    positionCamera();
  }
  function onPointerUp(event) {
    dragging = false;
    idleSince = performance.now();
    renderer.domElement.style.cursor = "grab";
    try {
      renderer.domElement.releasePointerCapture(event.pointerId);
    } catch {
      // Capture may already have been released by the browser; harmless.
    }
  }

  renderer.domElement.addEventListener("pointerdown", onPointerDown);
  renderer.domElement.addEventListener("pointermove", onPointerMove);
  renderer.domElement.addEventListener("pointerup", onPointerUp);
  renderer.domElement.addEventListener("pointerleave", onPointerUp);

  // A live WebGL context can be lost well after construction, not just
  // fail to create in the first place -- most browsers cap how many can
  // exist at once and evict older ones once a page asks for too many at
  // the same time. That cap is normally out of reach here (a handful of
  // cards, not dozens), but a Discord Activity shares its embedding
  // webview's budget with whatever else is on screen, so this is real
  // defensive coverage, not just handling for our own stress tests.
  // `renderer.render()` throws once its context is gone, so both the
  // event and a try/catch around the actual render call are needed --
  // the event can fire after a frame is already in flight.
  let contextLost = false;
  let rafId = null;
  function onContextLost(event) {
    event.preventDefault();
    contextLost = true;
    if (rafId !== null) cancelAnimationFrame(rafId);
    rafId = null;
  }
  renderer.domElement.addEventListener("webglcontextlost", onContextLost);

  function tick(now) {
    if (contextLost) return;
    if (!dragging && now - idleSince > 2200) {
      theta += 0.0032;
      positionCamera();
    }
    try {
      renderer.render(scene, camera);
    } catch {
      contextLost = true;
      return;
    }
    rafId = requestAnimationFrame(tick);
  }
  rafId = requestAnimationFrame(tick);

  return {
    update(newTraits) {
      rebuild(newTraits);
    },
    dispose() {
      disposed = true;
      if (rafId !== null) cancelAnimationFrame(rafId);
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("pointermove", onPointerMove);
      renderer.domElement.removeEventListener("pointerup", onPointerUp);
      renderer.domElement.removeEventListener("pointerleave", onPointerUp);
      renderer.domElement.removeEventListener("webglcontextlost", onContextLost);
      if (character) disposeObject(character);
      renderer.dispose();
      if (renderer.domElement.parentNode === container) container.removeChild(renderer.domElement);
    },
  };
}
