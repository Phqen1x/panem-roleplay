# Xbot ("X Bot") character model

Source: `examples/models/gltf/Xbot.glb` in the official [three.js](https://github.com/mrdoob/three.js)
repository (fetched at the `master` ref), where it has shipped as a bundled example asset for
years and is used across the majority of three.js's own official skinned-character/animation
demos (e.g. `webgl_animation_skinning_blending`, `webgl_animation_multiple`).

Origin: Mixamo's default "X Bot" auto-rigged character (Adobe/Mixamo). Mixamo characters are
free to download and use, including embedded in an application like this one; see Adobe's
Mixamo FAQ/terms at the time of writing (redistributing the raw character as a standalone asset
pack is restricted, but using it as part of a larger creative work — which is exactly how it is
used here, as one graphical component of this project's own character renderer — is the
intended, permitted use case).

Used here as the base humanoid mesh + skeleton for the Character tab's 3D avatar renderer
(`static/tabs/avatar_creator.js`). Not modified beyond the retexturing/retinting and per-trait
bone scaling `avatar_creator.js` applies at runtime.
