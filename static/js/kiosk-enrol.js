/* Post-booking face capture.
 *
 * Runs only after the assistant has confirmed a real reservation, and only if
 * the property has capture switched on. Sequence:
 *
 *   booking confirmed  ->  ask, in plain language  ->  guest taps yes
 *   ->  camera opens   ->  N frames with a pose prompt each
 *   ->  upload once    ->  camera closes  ->  overlay disappears
 *
 * Three rules this file exists to enforce on the client side:
 *
 * 1. The camera does not open until the guest has tapped yes. Not on page load,
 *    not while the consent text is on screen. `getUserMedia` is called from
 *    inside the accept handler and nowhere else.
 *
 * 2. "No" is one tap, always visible, and costs the guest nothing. It is also
 *    reported to the server, because a hotel needs to be able to show it asked
 *    and was refused — and because that stops the kiosk asking again.
 *
 * 3. Nothing is uploaded until the whole set is captured. A half-finished
 *    session that the guest walked away from is dropped, not sent.
 *
 * There is no matching here and no endpoint to match against. The frames are for
 * a receptionist to compare against the person in front of them.
 */

(() => {
  'use strict';

  const root = document.getElementById('kiosk');
  const stage = document.getElementById('enrol-stage');
  if (!root || !stage) return;

  const API = {
    status: '/api/v1/vision/enrolment/status/',
    enrol: '/api/v1/vision/enrolment/',
  };

  const el = {
    consent: document.getElementById('enrol-consent'),
    accept: document.getElementById('enrol-accept'),
    decline: document.getElementById('enrol-decline'),
    cancel: document.getElementById('enrol-cancel'),
    window: document.getElementById('capture-window'),
    caption: document.getElementById('capture-caption'),
    title: document.getElementById('kiosk-cam-status'),
    sub: document.getElementById('capture-sub'),
    dots: document.getElementById('enrol-dots'),
    video: document.getElementById('kiosk-cam'),
    canvas: document.getElementById('kiosk-cam-canvas'),
    flash: document.getElementById('capture-flash'),
  };
  if (!el.accept || !el.video || !el.canvas) return;

  // Both languages, because the guest can switch between the booking and the
  // consent question. Asking in Bangla and then listening for "yes" is how a guest
  // says হ্যাঁ and is treated as though they said nothing at all.
  const ALL_COPY = JSON.parse(stage.dataset.copy || '{}');
  const kioskRoot = document.getElementById('kiosk');
  const language = () => {
    if (window.ashosVoice && window.ashosVoice.language) return window.ashosVoice.language();
    return ((kioskRoot && kioskRoot.dataset.language) || 'en').split('-')[0];
  };
  // A live view, not a snapshot: the overlay is built at the moment it is shown,
  // which can be several turns after this file ran.
  const COPY = new Proxy(
    {},
    {
      get: (_target, key) => {
        const words = ALL_COPY[language()] || ALL_COPY.en || {};
        return words[key];
      },
    }
  );
  const TOTAL = Number(stage.dataset.frames || 6);

  // Long enough for the guest to actually change pose between shots. Firing six
  // frames in 300ms gives six copies of one photograph, which is the failure
  // this whole flow is meant to avoid.
  const POSE_MS = 1100;

  let context = null; // { conversation, reservation, language }
  let stream = null;
  let cancelled = false;
  let running = false;

  // --- plumbing --------------------------------------------------------------

  const csrf = () => {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  };

  const headers = () => {
    const out = { 'X-CSRFToken': csrf() };
    if (root.dataset.hotel) out['X-Hotel-Code'] = root.dataset.hotel;
    return out;
  };

  const say = (title, sub) => {
    if (el.title) el.title.textContent = title || '';
    if (el.sub) el.sub.textContent = sub || '';
  };

  const drawDots = (done) => {
    if (!el.dots) return;
    el.dots.innerHTML = '';
    for (let i = 0; i < TOTAL; i += 1) {
      const dot = document.createElement('span');
      dot.className = i < done ? 'enrol-dot is-done' : 'enrol-dot';
      el.dots.appendChild(dot);
    }
  };

  // The consent screen's own markup is rendered by the server in the property's
  // language. If the guest switched language during the booking, these words have
  // to switch with everything else — a Bangla conversation that ends with an
  // English consent question is asking somebody to agree to something they were
  // not asked in their own language.
  const relabelConsent = () => {
    const set = (selector, value) => {
      const node = stage.querySelector(selector);
      if (node && value) node.textContent = value;
    };
    set('#enrol-consent .capture-title', COPY.title);
    set('.enrol-consent__body', COPY.body);
    const bullets = stage.querySelectorAll('.enrol-consent__list li');
    (COPY.bullets || []).forEach((line, index) => {
      if (bullets[index]) bullets[index].textContent = line;
    });
    set('#enrol-accept', COPY.accept);
    set('#enrol-decline', COPY.decline);
    set('#enrol-cancel', COPY.cancel);
  };

  document.addEventListener('ashos:language', relabelConsent);

  const show = (node, visible) => {
    if (node) node.classList.toggle('is-hidden', !visible);
  };

  const closeCamera = () => {
    if (stream) stream.getTracks().forEach((track) => track.stop());
    stream = null;
    el.video.srcObject = null;
  };

  const dismiss = (delay = 0) => {
    closeCamera();
    window.setTimeout(() => {
      stage.classList.add('is-hidden');
      show(el.window, false);
      show(el.caption, false);
      show(el.cancel, false);
      show(el.consent, true);
      running = false;
    }, delay);
  };

  // --- server ----------------------------------------------------------------

  const report = async (granted, frames) => {
    const body = new FormData();
    body.append('conversation', context.conversation);
    body.append('reservation', context.reservation);
    body.append('consent', granted ? 'true' : 'false');
    if (context.language) body.append('language', context.language);
    (frames || []).forEach((blob, index) => {
      body.append('frames', blob, `frame-${index + 1}.jpg`);
    });

    const response = await fetch(API.enrol, {
      method: 'POST',
      headers: headers(),
      credentials: 'same-origin',
      body,
    });
    if (!response.ok) throw new Error(`enrolment failed (${response.status})`);
    return response.json();
  };

  // --- capture ---------------------------------------------------------------

  const grab = () =>
    new Promise((resolve) => {
      const ctx = el.canvas.getContext('2d');
      el.canvas.width = el.video.videoWidth || 480;
      el.canvas.height = el.video.videoHeight || 480;
      ctx.drawImage(el.video, 0, 0, el.canvas.width, el.canvas.height);

      // A silent camera is the unsettling kind. Blink so it is obvious when a
      // photo was actually taken.
      if (el.flash) {
        el.flash.classList.add('is-firing');
        window.setTimeout(() => el.flash.classList.remove('is-firing'), 320);
      }
      el.canvas.toBlob((blob) => resolve(blob), 'image/jpeg', 0.8);
    });

  const wait = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

  const runCapture = async () => {
    const poses = COPY.poses || [];
    const frames = [];

    for (let i = 0; i < TOTAL; i += 1) {
      if (cancelled) return null;
      say(
        (COPY.capturing || 'Photo {n} of {total}')
          .replace('{n}', String(i + 1))
          .replace('{total}', String(TOTAL)),
        poses[i] || ''
      );
      drawDots(i);
      await wait(POSE_MS);
      if (cancelled) return null;
      const blob = await grab();
      if (blob) frames.push(blob);
    }
    drawDots(TOTAL);
    return frames;
  };

  // --- flow ------------------------------------------------------------------

  const begin = async () => {
    show(el.consent, false);
    show(el.window, true);
    show(el.caption, true);
    show(el.cancel, true);
    say('', '');
    drawDots(0);

    try {
      // First and only call. Deliberately inside the accept handler: the browser
      // permission prompt should be the direct result of the guest tapping yes.
      const base = { width: 640, height: 640, facingMode: 'user' };
      const picker = window.ashosDevices;
      stream = await navigator.mediaDevices.getUserMedia({
        // A lobby terminal usually has the webcam the guest should be facing
        // plus the machine's own built-in one. Honour whichever staff chose.
        video: picker ? picker.videoConstraint(base) : base,
        audio: false,
      });
      // Camera labels are blank until access is granted; now they can be read.
      root.dispatchEvent(new CustomEvent('ashos:media-granted', { bubbles: true }));
    } catch (error) {
      // Blocked, missing, or in use. Not a failure the guest should have to
      // solve — the desk can handle it.
      say(COPY.camera_blocked || 'Camera unavailable', '');
      dismiss(2600);
      return;
    }

    el.video.srcObject = stream;
    await el.video.play().catch(() => {});

    const frames = await runCapture();
    closeCamera();

    if (cancelled || !frames || !frames.length) {
      // Withdrawn mid-way. Treat it as a refusal, upload nothing.
      try {
        await report(false, null);
      } catch (error) {
        /* the refusal is best-effort; nothing was captured either way */
      }
      dismiss();
      return;
    }

    say(COPY.done || 'Thank you.', '');
    try {
      await report(true, frames);
    } catch (error) {
      say(COPY.failed || 'We could not take the photos.', '');
      dismiss(3000);
      return;
    }
    dismiss(1800);
  };

  // --- wiring ----------------------------------------------------------------

  const accept = () => {
    if (running) return;
    running = true;
    cancelled = false;
    begin();
  };

  const declineNow = async () => {
    // Recorded, then gone. No camera was ever opened on this path.
    try {
      await report(false, null);
    } catch (error) {
      /* nothing was captured; a failed record is not the guest's problem */
    }
    dismiss();
  };

  el.accept.addEventListener('click', accept);

  if (el.decline) el.decline.addEventListener('click', declineNow);

  /** Read the question out and let the guest answer out loud.
   *
   * The buttons still work and are still the primary control — this is in
   * addition, not instead. A guest at a kiosk may have luggage in both hands, and
   * a consent question they can only answer by touching the screen is one they
   * will answer by walking away.
   *
   * Silence is NOT consent: an unheard answer leaves the screen up, waiting, and
   * the buttons still there.
   */
  const askOutLoud = async () => {
    const voice = window.ashosVoice;
    if (!voice) return;

    // This screen owns the microphone while it is up, so the conversation loop
    // does not answer on the guest's behalf.
    voice.suspend();
    try {
      const language = voice.language();
      const spoken = [COPY.title, COPY.body, COPY.accept, '—', COPY.decline]
        .filter(Boolean)
        .join(' ');

      const answer = await voice.askYesNo({
        text: spoken,
        language,
        yes: COPY.yes_words || [],
        no: COPY.no_words || [],
      });

      // Nothing decided, or already handled by a tap while we were listening.
      if (answer === null || stage.classList.contains('is-hidden') || running) return;
      if (answer) accept();
      else await declineNow();
    } finally {
      // The capture path takes the camera, not the microphone, so the
      // conversation can have it back either way.
      voice.resume();
    }
  };

  if (el.cancel) {
    el.cancel.addEventListener('click', () => {
      cancelled = true;
      closeCamera();
    });
  }

  window.addEventListener('beforeunload', closeCamera);

  // The assistant announces a confirmed booking; only then is there a stay to
  // attach photos to.
  root.addEventListener('ashos:booking-confirmed', async (event) => {
    const detail = event.detail || {};
    if (!detail.conversation || !detail.reservation) return;

    // Ask the server rather than trusting a template flag: the property switch
    // can be turned off while this page is still open in a lobby.
    try {
      const response = await fetch(API.status, { headers: headers(), credentials: 'same-origin' });
      const policy = await response.json();
      if (!policy.enabled) return;
    } catch (error) {
      return;
    }

    context = detail;
    cancelled = false;
    running = false;
    relabelConsent();
    show(el.consent, true);
    show(el.window, false);
    show(el.caption, false);
    show(el.cancel, false);
    stage.classList.remove('is-hidden');

    // Read aloud in the language the conversation is in, and listen for the
    // answer. Not awaited: the buttons must stay live the whole time.
    askOutLoud();
  });
})();
