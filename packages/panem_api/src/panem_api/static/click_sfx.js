// Web Audio API Synthesizer for tactile button click sound effects.
// Uses Web Audio API for zero-latency, zero-dependency, ultra-crisp audio.

let audioCtx = null;
let soundSettings = { muted: false, musicVolume: 0.5, sfxVolume: 0.7 };

// Load sound settings from localStorage
try {
  const saved = localStorage.getItem("panem_sound_settings");
  if (saved) {
    soundSettings = Object.assign(soundSettings, JSON.parse(saved));
  }
} catch (e) {
  // Ignore storage errors
}

export function updateSoundSettings(newSettings) {
  Object.assign(soundSettings, newSettings);
  try {
    localStorage.setItem("panem_sound_settings", JSON.stringify(soundSettings));
  } catch (e) {
    // Ignore storage errors
  }
}

export function getSoundSettings() {
  return soundSettings;
}

function getAudioContext() {
  if (!audioCtx) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (AudioContextClass) {
      audioCtx = new AudioContextClass();
    }
  }
  if (audioCtx && audioCtx.state === "suspended") {
    audioCtx.resume().catch(() => {});
  }
  return audioCtx;
}

// Synthesizes a crisp, elegant, tactile click sound
export function playClickSound() {
  if (soundSettings.muted || soundSettings.sfxVolume <= 0) return;

  const ctx = getAudioContext();
  if (!ctx) return;

  try {
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    // High frequency transient burst for tactile feel
    osc.type = "sine";
    osc.frequency.setValueAtTime(1200, now);
    osc.frequency.exponentialRampToValueAtTime(300, now + 0.025);

    const volume = Math.min(1.0, Math.max(0.0, soundSettings.sfxVolume * 0.25));
    gain.gain.setValueAtTime(volume, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.025);

    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.start(now);
    osc.stop(now + 0.025);
  } catch (err) {
    // Ignore audio context errors on un-interacted pages
  }
}

// Attach click listener to interactive elements globally
export function initClickSFX() {
  document.addEventListener(
    "click",
    (event) => {
      const target = event.target.closest("button, a, input[type='submit'], input[type='button'], .btn, .tab-btn, .clickable, .custom-select-toggle, .theme-preset-chip");
      if (target) {
        playClickSound();
      }
    },
    { capture: true, passive: true }
  );
}
