/* Which camera, microphone and speaker this terminal uses.
 *
 * A lobby kiosk is a fixed machine with a fixed rig — an external webcam on the
 * stand, a boundary mic, a powered speaker — and often a laptop's own built-in
 * versions of all three that nobody wants. The browser picks "default", which is
 * regularly the wrong one. This is the bar that fixes it once.
 *
 * Set up by staff, not by guests, so the choice is persisted in localStorage per
 * property: whoever installs the terminal picks the devices, and every guest
 * afterwards gets the right ones without touching anything.
 *
 * Exposes `window.ashosDevices`:
 *   cameraId() / micId()   deviceId to pass to getUserMedia, or '' for default
 *   applySink(mediaEl)     route playback to the chosen speaker
 *   refresh()              re-enumerate (labels appear once permission exists)
 *
 * Two browser facts worth knowing before editing:
 *
 * 1. enumerateDevices() returns entries with EMPTY labels until the page has
 *    been granted access to that kind of device. That is a privacy rule, not a
 *    bug — a page must not be able to fingerprint your hardware for free. So the
 *    bar shows "Camera 1" style placeholders and fills in real names the moment
 *    a permission is granted. It never asks for permission just to read labels;
 *    prompting a guest for their microphone so a dropdown can look tidier is not
 *    a trade worth making.
 *
 * 2. Output routing needs setSinkId, which Chrome and Edge have and others do
 *    not. Where it is missing the speaker chip says so instead of silently doing
 *    nothing. Browser speech synthesis cannot be routed at all — it has no media
 *    element to attach a sink to — so that limitation is stated on the chip too.
 */

(() => {
  'use strict';

  const root = document.getElementById('kiosk');
  const bar = document.getElementById('kiosk-devices');
  if (!root || !bar) return;

  const el = {
    camera: document.getElementById('device-camera'),
    mic: document.getElementById('device-mic'),
    speaker: document.getElementById('device-speaker'),
    note: document.getElementById('device-note'),
  };

  const KINDS = {
    camera: { select: el.camera, kind: 'videoinput' },
    mic: { select: el.mic, kind: 'audioinput' },
    speaker: { select: el.speaker, kind: 'audiooutput' },
  };

  // The bar is set up by staff, but it sits on the lobby screen in front of the
  // guest — so it is in the guest's language like everything else on it, and it
  // follows the language chip. "Camera — system default" hardcoded here was the
  // last English left on a Bangla kiosk.
  let COPY = (JSON.parse(root.dataset.copy || '{}').devices) || {};
  const t = (key, fallback) => COPY[key] || fallback || '';
  const named = (key, label, extra) =>
    t(key, '').replace('{label}', label).replace('{n}', extra) || label;
  const kindLabel = (which) => t(which, which);

  // Per property: two terminals in one hotel can have different hardware, and a
  // shared key would make each overwrite the other.
  const storeKey = (which) => `ashos.device.${root.dataset.hotel || 'default'}.${which}`;

  const canRoute = typeof HTMLMediaElement !== 'undefined'
    && 'setSinkId' in HTMLMediaElement.prototype;
  const canEnumerate = Boolean(navigator.mediaDevices && navigator.mediaDevices.enumerateDevices);

  const chosen = {
    camera: localStorage.getItem(storeKey('camera')) || '',
    mic: localStorage.getItem(storeKey('mic')) || '',
    speaker: localStorage.getItem(storeKey('speaker')) || '',
  };

  let labelsHidden = false;

  // --- Enumeration -----------------------------------------------------------

  const fill = (which, devices) => {
    const { select } = KINDS[which];
    if (!select) return;

    const label = kindLabel(which);
    const list = devices.filter((d) => d.kind === KINDS[which].kind);
    select.innerHTML = '';

    const auto = document.createElement('option');
    auto.value = '';
    auto.textContent = list.length
      ? named('default', label)
      : named('none', label);
    select.appendChild(auto);

    list.forEach((device, index) => {
      const option = document.createElement('option');
      option.value = device.deviceId;
      // Empty label means we have no permission for this kind yet. The device's
      // own name is the operating system's, in whatever language that is — not
      // something to translate.
      option.textContent = device.label || named('numbered', label, index + 1);
      if (!device.label) labelsHidden = true;
      select.appendChild(option);
    });

    // A device that has been unplugged since the choice was made must not leave
    // the bar showing a selection that cannot be honoured.
    const stillThere = list.some((d) => d.deviceId === chosen[which]);
    select.value = stillThere ? chosen[which] : '';
    if (!stillThere && chosen[which]) {
      chosen[which] = '';
      localStorage.removeItem(storeKey(which));
    }

    select.disabled = !list.length;
    const chip = select.closest('.device-chip');
    if (chip) chip.classList.toggle('is-empty', !list.length);
  };

  const refresh = async () => {
    if (!canEnumerate) {
      bar.classList.add('d-none');
      return;
    }
    labelsHidden = false;
    let devices = [];
    try {
      devices = await navigator.mediaDevices.enumerateDevices();
    } catch (error) {
      bar.classList.add('d-none');
      return;
    }

    Object.keys(KINDS).forEach((which) => fill(which, devices));

    const notes = [];
    if (labelsHidden) {
      notes.push(t('note_labels'));
    }
    if (!canRoute) {
      notes.push(t('note_no_route'));
    }
    // Both of these are browser limitations rather than missing work, and a
    // control that silently does nothing is worse than one that explains itself.
    if (root.dataset.tts !== 'true') {
      notes.push(t('note_browser_tts'));
    }
    if (root.dataset.voice !== 'true') {
      notes.push(t('note_browser_stt'));
    }
    const text = notes.filter(Boolean).join(' ');
    // Tooltip for the staff member setting the terminal up, and the same words in
    // the live region for a screen reader. Not printed on the lobby screen: a
    // guest never needs to read the limits of an output-routing API.
    bar.title = text;
    if (el.note) el.note.textContent = text;
  };

  // --- Selection -------------------------------------------------------------

  Object.entries(KINDS).forEach(([which, { select }]) => {
    if (!select) return;
    select.addEventListener('change', () => {
      chosen[which] = select.value;
      if (select.value) localStorage.setItem(storeKey(which), select.value);
      else localStorage.removeItem(storeKey(which));

      root.dispatchEvent(
        new CustomEvent('ashos:device-changed', {
          bubbles: true,
          detail: { kind: which, deviceId: select.value },
        })
      );
    });
  });

  if (canEnumerate && navigator.mediaDevices.addEventListener) {
    // Somebody plugs the good microphone in after the page was opened.
    navigator.mediaDevices.addEventListener('devicechange', refresh);
  }

  // --- Public surface --------------------------------------------------------

  window.ashosDevices = {
    cameraId: () => chosen.camera,
    micId: () => chosen.mic,
    speakerId: () => chosen.speaker,

    /** Constraints for getUserMedia, honouring the chosen device. */
    audioConstraint() {
      return chosen.mic ? { deviceId: { exact: chosen.mic } } : true;
    },
    videoConstraint(base) {
      const video = { ...base };
      if (chosen.camera) video.deviceId = { exact: chosen.camera };
      return video;
    },

    /** Route one media element to the chosen speaker. Safe to call always. */
    async applySink(mediaEl) {
      if (!canRoute || !chosen.speaker || !mediaEl || !mediaEl.setSinkId) return false;
      try {
        await mediaEl.setSinkId(chosen.speaker);
        return true;
      } catch (error) {
        // Wrong id, revoked permission, device gone. Default output is a fine
        // outcome; a silent answer is not.
        return false;
      }
    },

    refresh,

    /** Re-label in another language. Called by kiosk.js on a language change. */
    relabel(words) {
      COPY = words || COPY;
      if (el.camera) el.camera.setAttribute('aria-label', t('aria_camera', 'Camera'));
      if (el.mic) el.mic.setAttribute('aria-label', t('aria_mic', 'Microphone input'));
      if (el.speaker) el.speaker.setAttribute('aria-label', t('aria_speaker', 'Audio output'));
      // Re-enumerating rather than editing the option text in place: the selected
      // device has to survive the relabel, and fill() is the one place that knows
      // how to restore it.
      refresh();
    },
  };

  refresh();

  // Labels are blank until a permission exists, so re-read them right after any
  // successful capture — that is the moment the names become available.
  root.addEventListener('ashos:media-granted', refresh);
})();
