// Shared GLTF loading for every map3d module (`city.js`, `interior.js`,
// `customizer.js`, `pets_panel.js`) -- one `GLTFLoader` instance, one
// per-URL cache of the parsed `gltf.scene` (never re-fetched/re-parsed for
// a second NPC/building/furniture piece using the same model), and a
// `cloneModel` helper every caller uses instead of `Object3D.clone()`
// directly: a plain clone does *not* correctly share/rebuild skinned-mesh
// bone bindings (a well-known three.js gotcha), which breaks every
// Blocky Characters animation the moment more than one instance of the
// same character exists on screen at once -- `SkeletonUtils.clone`
// (vendored alongside `GLTFLoader.js`) is the one that gets this right,
// and costs nothing extra for the many non-skinned models (buildings,
// roads, furniture, pets) that don't need it.
import * as THREE from "../vendor/three.module.min.js";
import { GLTFLoader } from "../vendor/GLTFLoader.js";
import { clone as cloneSkeleton } from "../vendor/SkeletonUtils.js";

const loader = new GLTFLoader();
const cache = new Map(); // url -> Promise<{scene, animations}>

export function loadModel(url) {
  let entry = cache.get(url);
  if (!entry) {
    entry = new Promise((resolve, reject) => {
      loader.load(
        url,
        (gltf) => resolve({ scene: gltf.scene, animations: gltf.animations }),
        undefined,
        (err) => reject(err instanceof Error ? err : new Error(`Failed to load ${url}`))
      );
    });
    cache.set(url, entry);
  }
  return entry;
}

export function cloneModel(scene) {
  return cloneSkeleton(scene);
}

export function loadTexture(url) {
  return new Promise((resolve, reject) => {
    new THREE.TextureLoader().load(
      url,
      (texture) => {
        texture.colorSpace = THREE.SRGBColorSpace;
        texture.flipY = false; // glTF convention, matches GLTFLoader's own textures
        resolve(texture);
      },
      undefined,
      () => reject(new Error(`Failed to load texture ${url}`))
    );
  });
}

const textureCache = new Map(); // url -> Promise<Texture>

export function loadTextureCached(url) {
  let entry = textureCache.get(url);
  if (!entry) {
    entry = loadTexture(url);
    textureCache.set(url, entry);
  }
  return entry;
}
