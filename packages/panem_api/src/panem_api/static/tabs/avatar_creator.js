// A Picrew-style layered-image portrait compositor for the Character
// tab's visual customizer. There is no rendering logic here at all --
// just DOM: every `LayerCategory` (panem_shared.layers) contributes at
// most one <img>, absolutely positioned and stacked by that category's
// `z_index` (lower renders further back). The actual artwork is entirely
// staff-uploaded through the Staff tab's Layers panel (`staff.js`) and
// served as plain static files; this module never draws anything itself.
//
// This replaces an earlier fixed-trait-palette 3D renderer (Three.js,
// procedural geometry) -- asked for an upload-your-own-art Picrew-style
// customizer instead, which structurally can't be "rendered" at all until
// staff have actually uploaded something. `mountAvatar` is built to
// render sensibly on a catalog with zero categories or a category with
// zero options (nothing shows for that layer), not just a populated one.
//
// `mountAvatar(container, categories, selection) -> { update(selection), dispose() }`
// `categories` is the catalog `GET /activity/dashboard/layers` returns
// (already ordered by `z_index`): `[{id, name, z_index, options: [{id,
// name, image_url}, ...]}, ...]`. `selection` is `{categoryId (as a
// string): optionId}` -- the same shape `Character.appearance_layers`
// round-trips as JSON. A category missing from `selection`, or whose
// selected option id no longer matches any current option (staff deleted
// it since), just renders as nothing for that layer rather than erroring.

export function mountAvatar(container, categories, selection) {
  const stack = document.createElement("div");
  stack.className = "layer-stack";
  container.appendChild(stack);

  const layers = categories.map((category) => {
    const img = document.createElement("img");
    img.className = "layer-image";
    img.style.zIndex = String(category.z_index);
    img.hidden = true;
    img.alt = "";
    stack.appendChild(img);
    return { category, img };
  });

  function render(sel) {
    for (const { category, img } of layers) {
      const optionId = sel ? sel[String(category.id)] : undefined;
      const option =
        optionId != null ? category.options.find((o) => o.id === optionId) : undefined;
      if (option) {
        if (img.src !== option.image_url) img.src = option.image_url;
        img.hidden = false;
      } else {
        img.hidden = true;
        img.removeAttribute("src");
      }
    }
  }

  render(selection);

  return {
    update(newSelection) {
      render(newSelection);
    },
    dispose() {
      if (stack.parentNode === container) container.removeChild(stack);
    },
  };
}
