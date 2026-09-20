// An original layered-shape portrait renderer for the Character tab's
// visual customizer -- canvas primitives only (arcs/paths/polygons), no
// external art assets, same "original art" precedent this codebase
// already established for the /work and /lockpick minigames
// (`static/games/*.js`). `renderAvatar(canvas, traits)` is a pure
// stateless draw: call it again with new `traits` any time a selector
// changes and it redraws from scratch -- there's no animation loop here,
// a portrait doesn't need one.
//
// `traits` is the same shape `panem_shared.appearance.
// DEFAULT_APPEARANCE_TRAITS` returns and `validate_appearance_traits`
// normalizes to -- every field is always present by the time this is
// called from `character.js` (which always merges onto the options
// endpoint's `defaults`), so this file trusts that and doesn't re-check
// each field's presence.

export const WIDTH = 220;
export const HEIGHT = 260;

function darken(hex, amount) {
  const n = Number.parseInt(hex.slice(1), 16);
  const r = Math.max(0, ((n >> 16) & 255) - amount);
  const g = Math.max(0, ((n >> 8) & 255) - amount);
  const b = Math.max(0, (n & 255) - amount);
  return `rgb(${r}, ${g}, ${b})`;
}

function headPath(ctx, cx, cy, rx, ry, faceShape) {
  ctx.beginPath();
  if (faceShape === "square") {
    const r = rx * 0.35;
    const x0 = cx - rx;
    const y0 = cy - ry;
    ctx.moveTo(x0 + r, y0);
    ctx.arcTo(x0 + rx * 2, y0, x0 + rx * 2, y0 + ry * 2, r);
    ctx.arcTo(x0 + rx * 2, y0 + ry * 2, x0, y0 + ry * 2, r);
    ctx.arcTo(x0, y0 + ry * 2, x0, y0, r);
    ctx.arcTo(x0, y0, x0 + rx * 2, y0, r);
    ctx.closePath();
  } else if (faceShape === "heart") {
    ctx.ellipse(cx, cy - ry * 0.25, rx, ry * 0.8, 0, Math.PI, 0, true);
    ctx.quadraticCurveTo(cx + rx * 0.95, cy + ry * 0.35, cx, cy + ry);
    ctx.quadraticCurveTo(cx - rx * 0.95, cy + ry * 0.35, cx - rx, cy - ry * 0.25);
    ctx.closePath();
  } else if (faceShape === "long") {
    ctx.ellipse(cx, cy, rx * 0.85, ry * 1.2, 0, 0, Math.PI * 2);
  } else if (faceShape === "round") {
    ctx.ellipse(cx, cy, rx * 1.05, ry * 0.95, 0, 0, Math.PI * 2);
  } else {
    ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2); // oval
  }
}

function drawHairBack(ctx, cx, headTop, headBottom, rx, style, color) {
  if (!["shoulder", "long", "braid"].includes(style)) return;
  ctx.fillStyle = color;
  const bottom = style === "long" || style === "braid" ? headBottom + rx * 2.4 : headBottom + rx * 0.6;
  ctx.beginPath();
  ctx.moveTo(cx - rx * 0.95, headTop + rx * 0.3);
  ctx.quadraticCurveTo(cx - rx * 1.3, (headTop + bottom) / 2, cx - rx * 0.7, bottom);
  ctx.lineTo(cx - rx * 0.35, bottom - rx * 0.2);
  ctx.quadraticCurveTo(cx - rx * 0.6, (headTop + bottom) / 2, cx - rx * 0.55, headTop + rx * 0.2);
  ctx.closePath();
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(cx + rx * 0.95, headTop + rx * 0.3);
  ctx.quadraticCurveTo(cx + rx * 1.3, (headTop + bottom) / 2, cx + rx * 0.7, bottom);
  ctx.lineTo(cx + rx * 0.35, bottom - rx * 0.2);
  ctx.quadraticCurveTo(cx + rx * 0.6, (headTop + bottom) / 2, cx + rx * 0.55, headTop + rx * 0.2);
  ctx.closePath();
  ctx.fill();
  if (style === "braid") {
    ctx.strokeStyle = color;
    ctx.lineWidth = rx * 0.28;
    ctx.beginPath();
    ctx.moveTo(cx, bottom - rx * 0.3);
    for (let i = 1; i <= 4; i += 1) {
      const y = bottom - rx * 0.3 + i * rx * 0.35;
      ctx.lineTo(cx + (i % 2 === 0 ? rx * 0.18 : -rx * 0.18), y);
    }
    ctx.stroke();
  }
}

function drawHairFront(ctx, cx, cy, headTop, rx, ry, style, color) {
  ctx.fillStyle = color;
  if (style === "bald") return;
  if (style === "buzz" || style === "stubble-only") {
    ctx.globalAlpha = 0.55;
    ctx.beginPath();
    ctx.ellipse(cx, headTop + ry * 0.35, rx * 0.92, ry * 0.5, 0, Math.PI, 0);
    ctx.fill();
    ctx.globalAlpha = 1;
    return;
  }
  if (style === "mohawk") {
    ctx.beginPath();
    ctx.moveTo(cx - rx * 0.14, headTop - ry * 0.1);
    ctx.lineTo(cx + rx * 0.14, headTop - ry * 0.1);
    ctx.lineTo(cx + rx * 0.08, headTop + ry * 0.55);
    ctx.lineTo(cx - rx * 0.08, headTop + ry * 0.55);
    ctx.closePath();
    ctx.fill();
    return;
  }
  if (style === "bob") {
    // Same tight crown cap as every other style, plus two side flaps that
    // hang past the ears down to the jaw -- kept clear of the eye line the
    // same way the shared cap below is (a single low dome here used to dip
    // into the eyes; the fix is to only extend hair *beside* the face, not
    // lower across the front of it).
    ctx.save();
    ctx.beginPath();
    ctx.rect(cx - rx * 1.15, headTop - ry * 0.2, rx * 2.3, ry * 0.75);
    ctx.clip();
    ctx.beginPath();
    ctx.ellipse(cx, headTop + ry * 0.18, rx * 1.04, ry * 0.62, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    const flapTop = headTop + ry * 0.35;
    const flapBottom = headTop + ry * 1.85;
    for (const side of [-1, 1]) {
      ctx.beginPath();
      ctx.moveTo(cx + side * rx * 0.85, flapTop);
      ctx.quadraticCurveTo(cx + side * rx * 1.15, (flapTop + flapBottom) / 2, cx + side * rx * 0.95, flapBottom);
      ctx.lineTo(cx + side * rx * 0.55, flapBottom - ry * 0.15);
      ctx.quadraticCurveTo(cx + side * rx * 0.75, (flapTop + flapBottom) / 2, cx + side * rx * 0.65, flapTop);
      ctx.closePath();
      ctx.fill();
    }
    return;
  }
  // Every other style (short/shoulder/long/braid/curly/bun) gets the same
  // high, tight crown cap -- `drawHairBack` is what carries shoulder/
  // long/braid's extra length behind the head, so the front cap only ever
  // needs to cover the top of the head. Clipped to a rect well above the
  // brow line so it can never visually merge with the eyebrows below it,
  // regardless of head proportions (a bug the previous quadratic-curve
  // version had: its bulge could dip low enough to touch the brow).
  ctx.save();
  ctx.beginPath();
  ctx.rect(cx - rx * 1.15, headTop - ry * 0.2, rx * 2.3, ry * 0.75);
  ctx.clip();
  ctx.beginPath();
  ctx.ellipse(cx, headTop + ry * 0.18, rx * 1.04, ry * 0.62, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
  if (style === "curly") {
    ctx.beginPath();
    for (let i = -3; i <= 3; i += 1) {
      ctx.moveTo(cx + i * rx * 0.32 + rx * 0.16, headTop + ry * 0.05);
      ctx.arc(cx + i * rx * 0.32, headTop + ry * 0.05, rx * 0.16, 0, Math.PI * 2);
    }
    ctx.fill();
  }
  if (style === "bun") {
    ctx.beginPath();
    ctx.arc(cx, headTop - ry * 0.05, rx * 0.32, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawFacialHair(ctx, cx, cy, rx, ry, style, color) {
  if (style === "none") return;
  const mouthY = cy + ry * 0.45;
  if (style === "stubble") {
    ctx.globalAlpha = 0.3;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.ellipse(cx, cy + ry * 0.55, rx * 0.72, ry * 0.55, 0, 0, Math.PI);
    ctx.fill();
    ctx.globalAlpha = 1;
    return;
  }
  ctx.fillStyle = color;
  if (style === "mustache" || style === "full") {
    ctx.beginPath();
    ctx.ellipse(cx, mouthY - ry * 0.08, rx * 0.32, ry * 0.09, 0, 0, Math.PI * 2);
    ctx.fill();
  }
  if (style === "beard" || style === "full") {
    ctx.beginPath();
    ctx.moveTo(cx - rx * 0.75, cy + ry * 0.05);
    ctx.quadraticCurveTo(cx - rx * 0.7, cy + ry * 1.05, cx, cy + ry * 1.15);
    ctx.quadraticCurveTo(cx + rx * 0.7, cy + ry * 1.05, cx + rx * 0.75, cy + ry * 0.05);
    ctx.quadraticCurveTo(cx, cy + ry * 0.35, cx - rx * 0.75, cy + ry * 0.05);
    ctx.closePath();
    ctx.fill();
  }
}

function drawFace(ctx, cx, cy, rx, ry, traits) {
  const eyeY = cy - ry * 0.08;
  const eyeDx = rx * 0.42;
  const eyeW = rx * (traits.age_look === "youthful" ? 0.22 : 0.18);
  const eyeH = ry * 0.13;
  const browColor = darken(traits.hair_color, -20);
  const expr = traits.expression;

  // Eyes (sclera + iris).
  for (const side of [-1, 1]) {
    const ex = cx + side * eyeDx;
    ctx.fillStyle = "#f4ece1";
    ctx.beginPath();
    ctx.ellipse(ex, eyeY, eyeW, eyeH, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = traits.eye_color;
    ctx.beginPath();
    ctx.arc(ex, eyeY, eyeH * 0.85, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#10141a";
    ctx.beginPath();
    ctx.arc(ex, eyeY, eyeH * 0.4, 0, Math.PI * 2);
    ctx.fill();
  }

  // Eyebrows, angled by expression.
  ctx.strokeStyle = browColor;
  ctx.lineWidth = Math.max(2, rx * 0.05);
  ctx.lineCap = "round";
  const browY = eyeY - eyeH * 2.1;
  const browTilt = expr === "fierce" ? 0.16 : expr === "gentle" ? -0.1 : expr === "serious" ? 0.05 : 0;
  for (const side of [-1, 1]) {
    const bx = cx + side * eyeDx;
    ctx.beginPath();
    ctx.moveTo(bx - eyeW * 0.9, browY + side * browTilt * rx * 0.3);
    ctx.lineTo(bx + eyeW * 0.9, browY - side * browTilt * rx * 0.3);
    ctx.stroke();
  }

  // Mouth, curved by expression.
  const mouthY = cy + ry * 0.5;
  const mouthW = rx * 0.34;
  ctx.strokeStyle = "#5a3226";
  ctx.lineWidth = Math.max(2, rx * 0.045);
  ctx.beginPath();
  if (expr === "smiling") {
    ctx.moveTo(cx - mouthW, mouthY);
    ctx.quadraticCurveTo(cx, mouthY + ry * 0.22, cx + mouthW, mouthY);
  } else if (expr === "gentle") {
    ctx.moveTo(cx - mouthW * 0.8, mouthY);
    ctx.quadraticCurveTo(cx, mouthY + ry * 0.1, cx + mouthW * 0.8, mouthY);
  } else if (expr === "fierce" || expr === "serious") {
    ctx.moveTo(cx - mouthW, mouthY + (expr === "fierce" ? -ry * 0.06 : 0));
    ctx.quadraticCurveTo(cx, mouthY - ry * 0.05, cx + mouthW, mouthY + (expr === "fierce" ? -ry * 0.06 : 0));
  } else {
    ctx.moveTo(cx - mouthW * 0.85, mouthY);
    ctx.lineTo(cx + mouthW * 0.85, mouthY);
  }
  ctx.stroke();
}

function drawJewelry(ctx, cx, cy, rx, ry, neckY, jewelry, accent) {
  ctx.fillStyle = accent;
  ctx.strokeStyle = accent;
  if (jewelry.includes("earrings")) {
    for (const side of [-1, 1]) {
      ctx.beginPath();
      ctx.arc(cx + side * rx * 1.0, cy + ry * 0.25, rx * 0.06, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  if (jewelry.includes("nose_ring")) {
    ctx.beginPath();
    ctx.arc(cx + rx * 0.12, cy + ry * 0.42, rx * 0.05, 0, Math.PI * 2);
    ctx.stroke();
  }
  if (jewelry.includes("necklace")) {
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(cx - rx * 0.55, neckY + ry * 0.15);
    ctx.quadraticCurveTo(cx, neckY + ry * 0.55, cx + rx * 0.55, neckY + ry * 0.15);
    ctx.stroke();
  }
  if (jewelry.includes("headband")) {
    ctx.lineWidth = ry * 0.16;
    ctx.beginPath();
    ctx.ellipse(cx, cy - ry * 0.55, rx * 0.98, ry * 0.5, 0, Math.PI * 1.1, Math.PI * 1.9);
    ctx.stroke();
  }
  if (jewelry.includes("glasses")) {
    ctx.strokeStyle = "#20242c";
    ctx.lineWidth = Math.max(2, rx * 0.05);
    const eyeDx = rx * 0.42;
    const eyeY = cy - ry * 0.08;
    const lensR = ry * 0.2;
    for (const side of [-1, 1]) {
      ctx.beginPath();
      ctx.arc(cx + side * eyeDx, eyeY, lensR, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.moveTo(cx - eyeDx + lensR, eyeY);
    ctx.lineTo(cx + eyeDx - lensR, eyeY);
    ctx.stroke();
  }
}

export function renderAvatar(canvas, traits) {
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, WIDTH, HEIGHT);

  ctx.fillStyle = "#171a20";
  ctx.fillRect(0, 0, WIDTH, HEIGHT);

  const heightScale = 0.85 + ((traits.height_cm - 140) / (210 - 140)) * 0.3; // 0.85..1.15
  const buildScale = { slim: 0.85, athletic: 0.95, average: 1.0, stocky: 1.12, heavyset: 1.28 }[
    traits.build
  ];
  const shoulderWidth = traits.gender_presentation === "feminine" ? 0.82 : traits.gender_presentation === "masculine" ? 1.05 : 0.93;

  const cx = WIDTH / 2;
  const rx = 46 * (0.94 + (buildScale - 1) * 0.35);
  const ry = 54 * heightScale;
  const cy = 96;
  const headTop = cy - ry;
  const headBottom = cy + ry;
  const neckY = headBottom - ry * 0.1;

  // Torso, drawn first so hair-back and head layer over its top edge.
  const torsoTop = neckY + 14;
  const torsoW = rx * 1.9 * buildScale * shoulderWidth;
  ctx.fillStyle = traits.clothing_color;
  ctx.beginPath();
  ctx.moveTo(cx - torsoW * 0.32, torsoTop);
  ctx.quadraticCurveTo(cx - torsoW / 2, torsoTop + 20, cx - torsoW / 2, HEIGHT);
  ctx.lineTo(cx + torsoW / 2, HEIGHT);
  ctx.quadraticCurveTo(cx + torsoW / 2, torsoTop + 20, cx + torsoW * 0.32, torsoTop);
  ctx.closePath();
  ctx.fill();
  if (traits.clothing_style === "formal") {
    ctx.fillStyle = "#f4ece1";
    ctx.beginPath();
    ctx.moveTo(cx - 10, torsoTop);
    ctx.lineTo(cx + 10, torsoTop);
    ctx.lineTo(cx, torsoTop + 22);
    ctx.closePath();
    ctx.fill();
  } else if (traits.clothing_style === "ragged") {
    ctx.strokeStyle = darken(traits.clothing_color, 40);
    ctx.lineWidth = 2;
    for (let i = 0; i < 3; i += 1) {
      ctx.beginPath();
      ctx.moveTo(cx - torsoW * 0.3 + i * 14, torsoTop + 30 + i * 10);
      ctx.lineTo(cx - torsoW * 0.2 + i * 14, torsoTop + 42 + i * 10);
      ctx.stroke();
    }
  } else if (traits.clothing_style === "capitol_fashion") {
    ctx.fillStyle = darken(traits.clothing_color, -30);
    ctx.beginPath();
    ctx.ellipse(cx, torsoTop + 14, torsoW * 0.16, 8, 0, 0, Math.PI * 2);
    ctx.fill();
  } else if (traits.clothing_style === "hunter") {
    ctx.strokeStyle = darken(traits.clothing_color, 30);
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(cx - torsoW * 0.35, torsoTop + 10);
    ctx.lineTo(cx + torsoW * 0.1, HEIGHT - 20);
    ctx.stroke();
  } else if (traits.clothing_style === "merchant") {
    ctx.fillStyle = darken(traits.clothing_color, -25);
    ctx.fillRect(cx - torsoW * 0.28, torsoTop + 16, torsoW * 0.56, 10);
  }

  // Neck.
  ctx.fillStyle = traits.skin_tone;
  ctx.fillRect(cx - rx * 0.22, neckY - 4, rx * 0.44, 24);

  drawHairBack(ctx, cx, headTop, headBottom, rx, traits.hair_style, traits.hair_color);

  // Head.
  headPath(ctx, cx, cy, rx, ry, traits.face_shape);
  ctx.fillStyle = traits.skin_tone;
  ctx.fill();
  if (traits.age_look === "weathered") {
    ctx.strokeStyle = darken(traits.skin_tone, 35);
    ctx.lineWidth = 1;
    ctx.globalAlpha = 0.6;
    for (const side of [-1, 1]) {
      ctx.beginPath();
      ctx.moveTo(cx + side * rx * 0.55, cy + ry * 0.15);
      ctx.lineTo(cx + side * rx * 0.72, cy + ry * 0.35);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  drawFace(ctx, cx, cy, rx, ry, traits);
  drawFacialHair(ctx, cx, cy, rx, ry, traits.facial_hair, traits.hair_color);
  drawHairFront(ctx, cx, cy, headTop, rx, ry, traits.hair_style, traits.hair_color);
  drawJewelry(ctx, cx, cy, rx, ry, neckY, traits.jewelry, "#d9c58a");
}
