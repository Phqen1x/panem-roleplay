// Ambient Music Player for Panem Activity.
// Handles context-sensitive ambient music playback, crossfading, and volume control.

import { getSoundSettings } from "./click_sfx.js?v=53";

let currentAudio = null;
let currentTrack = null;
let currentContext = { district_id: null, location_id: null, channel_id: null };
let onTrackChangeCallbacks = [];

export function onTrackChange(cb) {
  onTrackChangeCallbacks.push(cb);
}

function notifyTrackChange() {
  const trackInfo = currentTrack
    ? { title: currentTrack.title, scope: currentTrack.scope, file_url: currentTrack.file_url }
    : null;
  onTrackChangeCallbacks.forEach((cb) => cb(trackInfo));
}

export function getCurrentTrackInfo() {
  return currentTrack;
}

export async function updateAmbientContext(context) {
  currentContext = Object.assign(currentContext, context);
  await refreshAmbientTrack();
}

export async function refreshAmbientTrack() {
  const settings = getSoundSettings();
  const query = new URLSearchParams();
  if (currentContext.district_id != null) query.set("district_id", currentContext.district_id);
  if (currentContext.location_id) query.set("location_id", currentContext.location_id);
  if (currentContext.channel_id) query.set("channel_id", currentContext.channel_id);

  try {
    const res = await fetch(`/activity/dashboard/ambient/tracks?${query.toString()}`);
    if (!res.ok) return;
    const tracks = await res.json();
    
    // Select the best matching track
    const bestTrack = tracks.length > 0 ? tracks[0] : null;

    if (!bestTrack) {
      stopAmbient();
      currentTrack = null;
      notifyTrackChange();
      return;
    }

    if (currentTrack && currentTrack.id === bestTrack.id) {
      applyAudioVolume();
      return;
    }

    // Switch to new track
    playTrack(bestTrack);
  } catch (err) {
    console.warn("Failed to fetch ambient tracks:", err);
  }
}

function playTrack(track) {
  stopAmbient();

  currentTrack = track;
  const settings = getSoundSettings();

  const audio = new Audio(track.file_url);
  audio.loop = true;
  audio.volume = settings.muted ? 0 : Math.min(1.0, Math.max(0.0, settings.musicVolume));

  currentAudio = audio;

  if (!settings.muted && settings.musicVolume > 0) {
    audio.play().catch((err) => {
      console.log("Ambient autoplay waiting for user interaction:", err);
    });
  }

  notifyTrackChange();
}

export function applyAudioVolume() {
  const settings = getSoundSettings();
  if (currentAudio) {
    currentAudio.volume = settings.muted ? 0 : Math.min(1.0, Math.max(0.0, settings.musicVolume));
    if (settings.muted || settings.musicVolume <= 0) {
      if (!currentAudio.paused) currentAudio.pause();
    } else {
      if (currentAudio.paused) {
        currentAudio.play().catch(() => {});
      }
    }
  }
}

export function stopAmbient() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio.src = "";
    currentAudio = null;
  }
}
