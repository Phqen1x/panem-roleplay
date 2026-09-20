// A real-time 3D portrait renderer for the Character tab's visual
// customizer. Built entirely from Three.js primitive geometries (spheres,
// capsules, cones, tori, boxes) shaded with plain MeshStandardMaterial
// colors -- no external model/texture assets, same "original art only"
// precedent this codebase already established for the /work and /lockpick
// minigames (`static/games/*.js`) and the previous 2D canvas version of
// this file. Three.js itself is vendored at `/vendor/three.module.min.js`
// (see `three.LICENSE.md` next to it) rather than loaded from a CDN,
// mirroring `/vendor/discord-embedded-app-sdk.js`: a real Discord Activity
// iframe only ever fetches from this server's own origin.
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

export const WIDTH = 220;
export const HEIGHT = 260;

const BUILD_SCALE = { slim: 0.85, athletic: 0.95, average: 1.0, stocky: 1.14, heavyset: 1.3 };

function shade(hex, factor) {
  return new THREE.Color(hex).multiplyScalar(factor);
}

function skinMaterial(traits) {
  const weathered = traits.age_look === "weathered";
  return new THREE.MeshStandardMaterial({
    color: weathered ? shade(traits.skin_tone, 0.92) : new THREE.Color(traits.skin_tone),
    roughness: weathered ? 0.95 : 0.75,
    metalness: 0,
  });
}

function hairMaterial(traits) {
  return new THREE.MeshStandardMaterial({ color: new THREE.Color(traits.hair_color), roughness: 0.7 });
}

// Head geometry varies by `face_shape` -- rather than one mesh type
// stretched every which way, each shape gets the primitive that actually
// reads as that shape at low poly counts (a box for "square" looks like a
// jaw; a sphere+cone reads as a chin for "heart").
function buildHead(traits) {
  const group = new THREE.Group();
  const material = skinMaterial(traits);
  const r = 0.5 * (traits.age_look === "youthful" ? 1.08 : 1);
  let head;
  if (traits.face_shape === "square") {
    head = new THREE.Mesh(new THREE.BoxGeometry(r * 1.7, r * 1.9, r * 1.6, 2, 2, 2), material);
  } else if (traits.face_shape === "heart") {
    head = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 9), material);
    head.scale.set(1, 0.95, 0.92);
    const chin = new THREE.Mesh(new THREE.ConeGeometry(r * 0.55, r * 0.7, 10), material);
    chin.position.y = -r * 0.75;
    chin.rotation.x = Math.PI;
    group.add(chin);
  } else if (traits.face_shape === "long") {
    head = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 10), material);
    head.scale.set(0.82, 1.3, 0.85);
  } else if (traits.face_shape === "round") {
    head = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 10), material);
    head.scale.set(1.05, 0.95, 1.0);
  } else {
    head = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 10), material); // oval
    head.scale.set(0.88, 1.08, 0.92);
  }
  group.add(head);
  group.userData.radius = r;
  return group;
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

function buildTorso(traits, buildScale, shoulderWidth) {
  const group = new THREE.Group();
  const clothColor = new THREE.Color(traits.clothing_color);
  const clothMat = new THREE.MeshStandardMaterial({ color: clothColor, roughness: 0.8 });
  const topR = 0.5 * shoulderWidth * buildScale;
  const bottomR = 0.42 * buildScale;
  const height = 1.0;
  const torso = new THREE.Mesh(new THREE.CylinderGeometry(topR, bottomR, height, 10), clothMat);
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

function buildLimb({ radius, length, color, segments = 6 }) {
  const mat = new THREE.MeshStandardMaterial({ color, roughness: 0.8 });
  return new THREE.Mesh(new THREE.CapsuleGeometry(radius, length, 2, segments), mat);
}

// Builds the full rig, positioned so the group's local origin sits at the
// character's feet. Total height still varies with `height_cm`/hair
// style/face shape, so `mountAvatar` frames the camera from this group's
// actual bounding box rather than assuming a fixed size.
function buildCharacter(traits) {
  const root = new THREE.Group();
  const buildScale = BUILD_SCALE[traits.build] ?? 1;
  const shoulderWidth =
    traits.gender_presentation === "feminine" ? 0.85 : traits.gender_presentation === "masculine" ? 1.08 : 0.95;
  const skinColor = new THREE.Color(traits.age_look === "weathered" ? shade(traits.skin_tone, 0.92) : traits.skin_tone);
  const clothColor = new THREE.Color(traits.clothing_color);
  const pantsColor = shade(traits.clothing_color, 0.68);

  const legLength = 1.05;
  const legRadius = 0.17 * buildScale;
  for (const side of [-1, 1]) {
    const leg = buildLimb({ radius: legRadius, length: legLength * 0.6, color: pantsColor });
    leg.position.set(side * 0.22 * buildScale, legLength / 2 + 0.05, 0);
    root.add(leg);
  }

  const hipY = legLength + 0.05;
  const torso = buildTorso(traits, buildScale, shoulderWidth);
  torso.position.y = hipY + torso.userData.height / 2;
  root.add(torso);

  const shoulderY = hipY + torso.userData.height;
  const armLength = 0.95;
  const armRadius = 0.13 * buildScale;
  const sleeveColor = traits.clothing_style === "ragged" ? shade(traits.clothing_color, 0.75) : clothColor;
  for (const side of [-1, 1]) {
    const upperArm = buildLimb({ radius: armRadius, length: armLength * 0.55, color: sleeveColor });
    upperArm.position.set(side * (torso.userData.topR + armRadius * 0.9), shoulderY - armLength * 0.32, 0);
    upperArm.rotation.z = side * 0.08;
    root.add(upperArm);
    const hand = new THREE.Mesh(new THREE.SphereGeometry(armRadius * 0.85, 8, 6), new THREE.MeshStandardMaterial({ color: skinColor, roughness: 0.75 }));
    hand.position.set(side * (torso.userData.topR + armRadius * 0.9), shoulderY - armLength * 0.72, 0);
    root.add(hand);
  }

  const neckY = shoulderY + 0.08;
  const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.18, 0.16, 8), new THREE.MeshStandardMaterial({ color: skinColor, roughness: 0.75 }));
  neck.position.y = neckY;
  root.add(neck);

  const headGroup = buildHead(traits);
  const headR = headGroup.userData.radius;
  headGroup.position.y = neckY + 0.1 + headR;
  root.add(headGroup);

  const hair = buildHair(headR, traits);
  if (hair) {
    hair.position.copy(headGroup.position);
    root.add(hair);
  }
  const facialHair = buildFacialHair(headR, traits);
  if (facialHair) {
    facialHair.position.copy(headGroup.position);
    root.add(facialHair);
  }
  const eyes = buildEyes(headR, traits);
  eyes.position.copy(headGroup.position);
  root.add(eyes);
  const mouth = buildMouth(headR, traits);
  mouth.position.add(headGroup.position);
  root.add(mouth);

  const jewelry = buildJewelry(headR, headGroup.position.y, shoulderY, traits.jewelry, "#d9c58a");
  if (jewelry) root.add(jewelry);

  const heightScale = 0.82 + ((traits.height_cm - 140) / (210 - 140)) * 0.36; // 0.82..1.18
  root.scale.set(1, heightScale, 1);

  return root;
}

function disposeObject(object) {
  object.traverse((node) => {
    if (node.geometry) node.geometry.dispose();
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
    const box = new THREE.Box3().setFromObject(object);
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

  let character = buildCharacter(traits);
  scene.add(character);
  frame(character);

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
      scene.remove(character);
      disposeObject(character);
      character = buildCharacter(newTraits);
      scene.add(character);
      frame(character);
    },
    dispose() {
      if (rafId !== null) cancelAnimationFrame(rafId);
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("pointermove", onPointerMove);
      renderer.domElement.removeEventListener("pointerup", onPointerUp);
      renderer.domElement.removeEventListener("pointerleave", onPointerUp);
      renderer.domElement.removeEventListener("webglcontextlost", onContextLost);
      disposeObject(character);
      renderer.dispose();
      if (renderer.domElement.parentNode === container) container.removeChild(renderer.domElement);
    },
  };
}
