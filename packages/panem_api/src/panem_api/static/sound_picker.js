// Neoclassical Sound Settings Picker for Panem Activity.
// Includes Master Mute, Ambient Music Volume, Click SFX Volume, and Now Playing Track display.

import { getSoundSettings, updateSoundSettings, playClickSound } from "./click_sfx.js?v=53";
import { applyAudioVolume, getCurrentTrackInfo, onTrackChange, refreshAmbientTrack } from "./ambient_player.js?v=53";

export function mountSoundPicker(container) {
  container.innerHTML = `
    <button type="button" class="sound-picker-toggle" aria-label="Audio & Sound Settings" title="Audio & Sound Settings" aria-expanded="false">
      <svg class="sound-icon" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/>
      </svg>
    </button>
    <div class="sound-picker-popup" hidden>
      <div class="sound-picker-header">
        <span class="sound-picker-title">Audio & Sound</span>
        <span class="sound-picker-badge">Neoclassical</span>
      </div>

      <div class="sound-row">
        <button type="button" class="sound-btn sound-mute-btn">
          <span class="mute-status-text">Mute Audio</span>
        </button>
      </div>

      <div class="sound-slider-group">
        <label class="sound-label">
          <span>Ambient Music Volume</span>
          <span class="sound-val music-val">50%</span>
        </label>
        <input type="range" class="sound-slider music-slider" min="0" max="100" value="50" />
      </div>

      <div class="sound-slider-group">
        <label class="sound-label">
          <span>Click SFX Volume</span>
          <span class="sound-val sfx-val">70%</span>
        </label>
        <input type="range" class="sound-slider sfx-slider" min="0" max="100" value="70" />
      </div>

      <div class="sound-now-playing">
        <div class="now-playing-label">Now Playing Ambient</div>
        <div class="now-playing-title">None</div>
      </div>

      <div class="sound-actions">
        <button type="button" class="sound-btn sound-test-sfx-btn">Test Click SFX</button>
      </div>
    </div>
  `;

  const toggleBtn = container.querySelector(".sound-picker-toggle");
  const popup = container.querySelector(".sound-picker-popup");
  const muteBtn = container.querySelector(".sound-mute-btn");
  const muteText = container.querySelector(".mute-status-text");
  const musicSlider = container.querySelector(".music-slider");
  const musicVal = container.querySelector(".music-val");
  const sfxSlider = container.querySelector(".sfx-slider");
  const sfxVal = container.querySelector(".sfx-val");
  const nowPlayingTitle = container.querySelector(".now-playing-title");
  const testSfxBtn = container.querySelector(".sound-test-sfx-btn");

  function renderState() {
    const settings = getSoundSettings();
    muteBtn.classList.toggle("muted", settings.muted);
    muteText.textContent = settings.muted ? "Unmute Audio" : "Mute Audio";

    musicSlider.value = Math.round(settings.musicVolume * 100);
    musicVal.textContent = `${musicSlider.value}%`;

    sfxSlider.value = Math.round(settings.sfxVolume * 100);
    sfxVal.textContent = `${sfxSlider.value}%`;

    const track = getCurrentTrackInfo();
    if (track) {
      const scopeLabel = track.scope ? ` [${track.scope.charAt(0).toUpperCase() + track.scope.slice(1)}]` : "";
      nowPlayingTitle.textContent = `${track.title}${scopeLabel}`;
    } else {
      nowPlayingTitle.textContent = "Idle (No ambient track)";
    }
  }

  muteBtn.addEventListener("click", () => {
    const settings = getSoundSettings();
    updateSoundSettings({ muted: !settings.muted });
    applyAudioVolume();
    renderState();
  });

  musicSlider.addEventListener("input", (e) => {
    const val = Number(e.target.value) / 100;
    updateSoundSettings({ musicVolume: val });
    applyAudioVolume();
    renderState();
  });

  sfxSlider.addEventListener("input", (e) => {
    const val = Number(e.target.value) / 100;
    updateSoundSettings({ sfxVolume: val });
    renderState();
  });

  testSfxBtn.addEventListener("click", () => {
    playClickSound();
  });

  onTrackChange(() => {
    renderState();
  });

  function positionPopup() {
    const margin = 16;
    const btnRect = toggleBtn.getBoundingClientRect();
    const width = Math.min(270, window.innerWidth - margin * 2);
    popup.style.width = `${width}px`;
    const left = Math.min(Math.max(btnRect.right - width, margin), window.innerWidth - width - margin);
    const top = btnRect.bottom + 8;
    popup.style.left = `${left}px`;
    popup.style.top = `${top}px`;
  }

  toggleBtn.addEventListener("click", () => {
    if (popup.hidden) {
      renderState();
      positionPopup();
      popup.hidden = false;
      toggleBtn.setAttribute("aria-expanded", "true");
    } else {
      popup.hidden = true;
      toggleBtn.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("click", (event) => {
    if (!popup.hidden && !event.composedPath().includes(container)) {
      popup.hidden = true;
      toggleBtn.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !popup.hidden) {
      popup.hidden = true;
      toggleBtn.setAttribute("aria-expanded", "false");
    }
  });

  container.hidden = false;
  renderState();
}
