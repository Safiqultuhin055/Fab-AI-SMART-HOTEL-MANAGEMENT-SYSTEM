/* AI Reception kiosk client.
 *
 * Vanilla ES6, no framework, no build step (goal.txt §2.1).
 *
 * Design notes worth knowing before editing:
 *
 * - Every state the guest can observe is driven from ONE place, `setState`.
 *   A kiosk that looks idle while it is actually thinking makes people press
 *   the button again, which doubles the bill and confuses the transcript.
 *
 * - Voice is opportunistic. If the browser has no MediaRecorder, or the
 *   provider is not configured, the mic disables itself and typing still works.
 *   Reception must never be blocked by a missing microphone permission.
 *
 * - The waveform is driven by a real AnalyserNode while recording. Faking it
 *   would mean the guest gets no feedback that the mic is actually hearing them.
 */

(() => {
  'use strict';

  const root = document.getElementById('kiosk');
  if (!root) return;

  const API = {
    start: '/api/v1/reception/conversations/',
    chat: '/api/v1/reception/chat/',
    voice: '/api/v1/reception/voice/',
    speak: '/api/v1/reception/speak/',
    handoff: '/api/v1/reception/handoff/',
    nudge: '/api/v1/reception/nudge/',
  };

  const el = {
    avatar: root.querySelector('#kiosk-avatar'),
    state: root.querySelector('#kiosk-state'),
    bubbles: root.querySelector('#kiosk-bubbles'),
    form: root.querySelector('#kiosk-form'),
    input: root.querySelector('#kiosk-input'),
    mic: root.querySelector('#kiosk-mic'),
    micHint: root.querySelector('#kiosk-mic-hint'),
    wave: root.querySelector('#kiosk-wave'),
    tiles: root.querySelectorAll('[data-prompt]'),
    human: root.querySelector('#kiosk-human'),
    reset: root.querySelector('#kiosk-reset'),
    mute: root.querySelector('#kiosk-mute'),
    voicePick: root.querySelector('#kiosk-voice-pick'),
    langPick: root.querySelector('#kiosk-lang-pick'),
    bookingCard: root.querySelector('#kiosk-booking'),
    bookingRows: root.querySelector('#kiosk-booking-rows'),
    bookingState: root.querySelector('#kiosk-booking-state'),
    bookingNote: root.querySelector('#kiosk-booking-note'),
    rooms: root.querySelector('#kiosk-rooms'),
    roomsTitle: root.querySelector('#kiosk-rooms-title'),
    roomsList: root.querySelector('#kiosk-rooms-list'),
    turnCount: root.querySelector('#kiosk-turns'),
  };

  const aiEnabled = root.dataset.aiState !== 'disabled';

  // Two engines per direction, in preference order.
  //
  // A configured provider wins when there is one — the operator paid for it and
  // it is better. With no key the browser's own Web Speech engines take over:
  // free, no round trip, and they speak bn-BD. Requiring a paid key before a
  // guest can talk to the kiosk at all was the wrong trade for a lobby terminal:
  // it disabled the microphone and read nothing aloud on a property with a
  // perfectly good chat model.
  const serverStt = root.dataset.voice === 'true';
  const serverTts = root.dataset.tts === 'true';
  const SpeechRecognitionApi = window.SpeechRecognition || window.webkitSpeechRecognition;
  const browserStt = Boolean(SpeechRecognitionApi);
  const browserTts = 'speechSynthesis' in window;

  // Every guest-facing string, handed over by the server in the guest's
  // language. Nothing in this file composes a sentence.
  //
  // ALL_COPY holds both languages, and COPY is whichever one the conversation is
  // in — reassigned by setChromeLanguage() when the guest taps the chip or when a
  // turn comes back in the other language. The alternative was fetching the other
  // half on the tap, which puts a visible English flash on a screen whose entire
  // job is to feel like talking to somebody.
  const ALL_COPY = JSON.parse(root.dataset.copyAll || '{}');
  let COPY = JSON.parse(root.dataset.copy || '{}');
  const t = (key, fallback) => COPY[key] || fallback || '';

  // Vision rail notes, resolved and formatted per language by the server: the
  // face note carries the frame count and the retention period, and working those
  // into a sentence is not something a script should be doing in two languages.
  const PANELS = JSON.parse(root.dataset.panels || '{}');

  // Bare language code -> the tag the browser speech engines require. They reject
  // a bare "bn"; the region is mandatory.
  const BCP47 = { bn: 'bn-BD', en: 'en-US' };

  // Not const: it follows the guest. Answer a Bangla-set kiosk in English and the
  // next thing you say must be *heard* as English too, or speech recognition
  // hands back nonsense and the guest is blamed for it.
  let speechLang = root.dataset.speechLang || 'en-US';

  const retune = (language) => {
    if (!language) return;
    const next = BCP47[String(language).split('-')[0]];
    if (next) speechLang = next;
  };

  // Starts at the property setting, then whatever this terminal was last set to.
  // A guest who prefers the other voice should not have to ask a manager to change
  // a database row, and the choice should survive the next guest walking up.
  const voiceKey = `ashos.voice.${root.dataset.hotel || 'default'}`;
  let voiceGender = localStorage.getItem(voiceKey) || root.dataset.voiceGender || 'female';
  const voiceName = root.dataset.voiceName || '';

  // Hands-free: the guest never presses anything. The kiosk greets, then the
  // microphone is open for as long as the page is, closing only while an answer is
  // being read out so it does not transcribe itself.
  //
  // Stated plainly because it is the operator's decision, not a detail: a lobby
  // microphone that is open all day hears everyone walking past, and the browser's
  // speech recognition sends that audio to the browser vendor. The property switch
  // (Settings -> AI, "hands-free microphone") turns it off, and closing the tab or
  // resetting the session closes the microphone.
  const handsFree = root.dataset.handsFree === 'true';

  // Is there a member of staff who can be brought into this conversation? False on
  // the public booking page, where nobody is watching it — so the "a colleague has
  // been notified" line must never appear there. The server decides; a page that
  // works this out for itself is a page that gets it wrong after a refactor.
  const staffed = root.dataset.staffed !== 'false';

  // Silence before the assistant asks the next question itself, in seconds. 0 off.
  //
  // A guest who stops replying has usually stopped at a step, not left: they are
  // reading the room list, or they typed a date and are waiting to be asked for the
  // next thing. Nobody is standing over a browser tab to notice, so the assistant
  // notices. The question is deterministic and free — see /api/v1/reception/nudge/.
  const nudgeAfter = Math.max(0, Number(root.dataset.nudgeAfter || 0)) * 1000;
  // Three, then stop until the guest says something. A page that asks a fourth time
  // is not helping, it is nagging — and it would keep writing to the transcript of
  // somebody who closed their laptop an hour ago.
  const MAX_NUDGES = 3;
  let nudgeTimer = null;
  let nudgesSent = 0;

  /** The label under the microphone when nothing is happening.
   *
   * "Resting" is not "waiting to be pressed": where the microphone opens itself the
   * resting state is an open microphone, so an instruction to tap is an instruction
   * to press a button that is already listening — printed in the one place a guest
   * reads before deciding whether to speak or reach for the keyboard.
   *
   * A function rather than a constant because `t()` reads the language the
   * conversation is currently in, and that changes under it when the guest taps the
   * chip.
   */
  const restingHint = () =>
    (handsFree && browserStt ? t('armed', 'Go ahead, I am listening') : t('tap', 'Tap to speak'));

  // A microphone that fails instantly would otherwise reopen in a tight loop, so
  // failures back off — but they never give up. This used to be a cap: six failures
  // and the microphone stood down for the session.
  //
  // The count is not per stretch, it is per SESSION (only a successful utterance
  // clears it), and Chrome hands out transient errors for free — 'network' when its
  // speech service hiccups, 'audio-capture' when the device is busy for 200ms. Six
  // of those across a long conversation is normal, so a page left open reliably
  // switched its own microphone off and left "মাইক বন্ধ" under it. Which is exactly
  // what "the voice stops after a few seconds" looks like.
  //
  // Backoff, capped, forever. Only a real refusal (NotAllowedError) is permanent.
  const RETRY_STEP_MS = 400;
  const RETRY_MAX_MS = 4000;
  const retryDelay = () => Math.min(RETRY_STEP_MS * (restarts + 1), RETRY_MAX_MS);

  // Belt and braces for every way a recognition session can die without telling us:
  // Chrome dropping it silently, a background tab throttling the timer that would
  // have reopened it, an exception on a path that forgot to reschedule. The
  // microphone's state is checked on a clock instead of being trusted to events.
  const MIC_WATCHDOG_MS = 3000;
  let micWatchdog = null;

  // How long to believe `speechSynthesis.speaking` before overruling it.
  //
  // This is not paranoia. Ask Chrome to speak Bangla on a machine with no Bangla
  // voice installed — which is this property's situation, and the page says so in
  // its own words — and `speaking` can latch true with no `onend` ever firing. The
  // microphone then waits politely for an answer that finished, or never started, and
  // the guest is left talking to a dead orb.
  const TTS_PATIENCE_MS = 12000;
  let talkingSince = 0;

  let autoListen = false;
  let restarts = 0;
  // True while another screen owns the microphone — the photo-consent question,
  // for instance. The loop must not reopen underneath it.
  let suspended = false;
  // True while the microphone was closed deliberately to make room for an answer.
  // Without it that close counts as a failure, and six typed messages would trip
  // the restart cap and silently kill hands-free.
  let pausing = false;

  // The device bar, if this page has one. Absent on a browser that cannot
  // enumerate devices, so never assume it is there.
  const devices = () => window.ashosDevices || null;
  const audioConstraint = () => {
    const picker = devices();
    return picker ? picker.audioConstraint() : true;
  };
  const routeOutput = async (mediaEl) => {
    const picker = devices();
    if (picker) await picker.applySink(mediaEl);
  };
  const voiceEnabled = serverStt || browserStt;
  const ttsEnabled = serverTts || browserTts;

  let conversationId = null;
  let busy = false;
  let recorder = null;
  let audioCtx = null;
  let analyser = null;
  let waveTimer = null;
  let voiceDisabled = false;
  let muted = false;
  let player = null;
  // Set once the guest picks from the language chip. Sent with every turn after
  // that, because a pin the server has to re-derive from each message is not a pin.
  let pinnedLanguage = '';
  let lastGreeting = '';
  // The bilingual opening, kept so a browser that blocked autoplay can be asked
  // again on the first touch — and asked for both halves, not just one.
  let openingParts = [];
  let spokenGreeting = false;
  let recognition = null;
  let listening = false;

  // --- CSRF ------------------------------------------------------------------
  const csrf = () => {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  };

  const post = async (url, body, isForm = false) => {
    const headers = { 'X-CSRFToken': csrf() };
    // Pin the property on every call. The kiosk page is opened once with
    // ?hotel=CODE; the header means the XHRs do not depend on that surviving
    // in the session.
    if (root.dataset.hotel) headers['X-Hotel-Code'] = root.dataset.hotel;
    if (!isForm) headers['Content-Type'] = 'application/json';
    const response = await fetch(url, {
      method: 'POST',
      headers,
      credentials: 'same-origin',
      body: isForm ? body : JSON.stringify(body),
    });
    const type = response.headers.get('content-type') || '';
    const payload = type.includes('json') ? await response.json() : await response.blob();
    if (!response.ok) {
      const detail = (payload && payload.detail) || `request failed (${response.status})`;
      throw new Error(detail);
    }
    return payload;
  };

  // --- View ------------------------------------------------------------------
  // What the guest reads under the orb. This is how they know whether the machine
  // is hearing them, working, or waiting — silence with no label reads as broken
  // and people tap the button again.
  // A map built once at load froze the language it was loaded in, so the label
  // under the orb stayed English after the guest switched. Read per call instead.
  const STATE_KEY = {
    idle: ['ready', 'Ready'],
    listening: ['listening', 'Listening… go ahead'],
    thinking: ['thinking', 'Thinking…'],
    speaking: ['speaking', 'Speaking'],
    offline: ['offline', 'Manual mode — staff will assist'],
  };

  let currentState = 'idle';

  const stateLabel = (state) => {
    const pair = STATE_KEY[state];
    return pair ? t(pair[0], pair[1]) : '';
  };

  const setState = (state) => {
    currentState = state;
    el.avatar.dataset.state = state;
    el.state.textContent = stateLabel(state);
    el.wave.classList.toggle('is-live', state === 'listening' || state === 'speaking');
  };

  // Numerals, in the script the rest of the screen is in. Display only: nothing
  // is parsed back out of these, and the values themselves stay as the server
  // sent them. A Bangla screen where the assistant says "৩১৬২৫ টাকা" and the card
  // beside it says "31625.00" is half switched.
  const digits = (value) => {
    const glyphs = COPY.digits || '0123456789';
    return String(value).replace(/[0-9]/g, (d) => glyphs[Number(d)] || d);
  };

  const escapeHtml = (text) =>
    text.replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

  // The newest turn is the only one that matters, so it must be on screen —
  // always, and without the guest scrolling.
  //
  // Assigning scrollTop straight after appendChild scrolls to the height the box
  // had BEFORE the browser laid the new bubble out, which lands a line or two
  // short: the last answer sits half-cut at the bottom edge, which is exactly
  // what it did. A frame later the geometry is real.
  // True unless the guest has scrolled up to re-read something. A kiosk that
  // yanks the view back down while somebody is reading is worse than one that
  // needs a swipe — but a new turn always wins, because that is what they asked
  // the machine for.
  let stuckToBottom = true;

  const pinToBottom = () => {
    if (el.bubbles) el.bubbles.scrollTop = el.bubbles.scrollHeight;
  };

  const scrollToLatest = () => {
    if (!el.bubbles) return;
    stuckToBottom = true;
    // Three times, and all three earn their place:
    //   now             — correct already when nothing reflows
    //   next frame      — after the browser has laid the new bubble out; assigning
    //                     only before that scrolls to the height the box HAD, which
    //                     is how the last answer ended up half-cut at the edge
    //   next task       — after late reflow: a web font swapping in, a photo
    //                     decoding, the rooms panel appearing beside the text
    // rAF alone is not enough: it does not fire in a background tab, and a lobby
    // terminal left on another tab and switched back is an ordinary Tuesday.
    pinToBottom();
    requestAnimationFrame(pinToBottom);
    setTimeout(pinToBottom, 0);
  };

  if (el.bubbles) {
    // 40px of slack: "near enough the bottom" survives a trackpad nudge and the
    // sub-pixel rounding a zoomed browser produces.
    el.bubbles.addEventListener('scroll', () => {
      const distance = el.bubbles.scrollHeight - el.bubbles.scrollTop - el.bubbles.clientHeight;
      stuckToBottom = distance < 40;
    });

    // The box itself changes height for reasons that have nothing to do with new
    // messages: the on-screen keyboard opening on a tablet, the rooms panel
    // appearing and re-flowing the bubbles, a font swapping in late. Each one
    // moves the bottom out from under the last answer.
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => {
        if (stuckToBottom) el.bubbles.scrollTop = el.bubbles.scrollHeight;
      }).observe(el.bubbles);
    }
  }

  const addBubble = (who, text, photo) => {
    // Repeating the same system warning on every retry turns one problem into
    // a wall of identical red boxes, which is what the kiosk looked like when
    // speech was misconfigured.
    const last = el.bubbles.lastElementChild;
    if (who === 'system' && last && last.dataset.text === text) return last;

    const node = document.createElement('div');
    node.className = `bubble bubble--${who}`;
    node.dataset.text = text;
    node.innerHTML = escapeHtml(text);

    // The room, in the message that talks about it. The side gallery is a
    // reference the guest has to look away to read; this is the answer itself
    // carrying its picture, which is how a person would show it — turning the
    // screen round rather than pointing at the wall.
    if (photo) {
      const image = document.createElement('img');
      image.className = 'bubble__photo';
      image.src = photo.url;
      image.alt = photo.alt || '';
      // Before the text: a photograph under three lines of Bangla arrives after
      // the guest has finished reading and stopped looking.
      node.insertBefore(image, node.firstChild);
      // A photo has no height until it decodes, so the scroll that ran on append
      // was short by the height of the picture — and the answer it belongs to
      // ended up above the fold the moment it appeared.
      image.addEventListener('load', scrollToLatest);
    }

    // There was a "তথ্যসূত্র: [16] আজকের রুম ও দাম · [17] …" line under every AI
    // bubble. It is gone: those numbers index the CONTEXT block the server built for
    // the model, so they are our bookkeeping, not information — a receptionist does
    // not end a sentence by reciting which page of the tariff she read it from.
    //
    // Nothing is lost. `citations` still arrives in the payload and is still stored on
    // the Message, which is where an operator auditing an answer looks.

    el.bubbles.appendChild(node);
    scrollToLatest();
    return node;
  };

  const addTyping = () => {
    const node = document.createElement('div');
    node.className = 'bubble bubble--ai';
    node.innerHTML = '<span class="bubble__typing"><span></span><span></span><span></span></span>';
    el.bubbles.appendChild(node);
    scrollToLatest();
    return node;
  };

  // --- Waveform --------------------------------------------------------------
  const BARS = 48;

  const buildWave = () => {
    el.wave.innerHTML = '';
    for (let i = 0; i < BARS; i += 1) {
      const bar = document.createElement('div');
      bar.className = 'waveform__bar';
      bar.style.height = '4px';
      el.wave.appendChild(bar);
    }
  };

  const animateWave = () => {
    const bars = el.wave.children;
    if (!analyser) {
      // Idle shimmer so the strip does not look dead between turns.
      for (let i = 0; i < bars.length; i += 1) {
        const h = 4 + Math.abs(Math.sin(Date.now() / 400 + i / 3)) * 10;
        bars[i].style.height = `${h}px`;
      }
      return;
    }
    const data = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(data);
    const step = Math.floor(data.length / bars.length) || 1;
    for (let i = 0; i < bars.length; i += 1) {
      const value = data[i * step] || 0;
      bars[i].style.height = `${4 + (value / 255) * 44}px`;
    }
  };

  // --- Conversation ----------------------------------------------------------
  const ensureConversation = async ({ speakGreeting = false } = {}) => {
    if (conversationId) return conversationId;
    const data = await post(API.start, {
      channel: root.dataset.channel || 'kiosk',
      language: root.dataset.language || 'en',
    });
    conversationId = data.conversation;

    // The language question comes FIRST and there is no greeting yet — welcoming
    // somebody before they have said which language they read means guessing, and
    // the welcome is the one sentence you least want to get wrong. It arrives as
    // the reply to their answer.
    openingParts = data.language_prompt || [];
    if (data.greeting) addBubble('ai', data.greeting);

    if (openingParts.length) {
      // Only the first line up front. The rest are added by speakOpening as they
      // are spoken, so the screen and the voice stay in step.
      addBubble('ai', openingParts[0].text);
      lastGreeting = openingParts.map((p) => p.text).join(' ');
    }

    // Text first, then voice — a guest reads it before the audio finishes, and
    // somebody who cannot hear is not excluded.
    if (speakGreeting) {
      spokenGreeting = await speakOpening();
    }
    return conversationId;
  };

  // --- Speech out ------------------------------------------------------------
  // Text appears first, then it is spoken. A guest reads faster than the audio
  // plays, and someone who does not want to listen has already got the answer.
  // Browser voice list is populated asynchronously, so the first utterance often
  // finds it empty. Ask once and keep whatever arrives.
  let voices = [];
  const loadVoices = () => {
    voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
  };
  if (browserTts) {
    loadVoices();
    window.speechSynthesis.addEventListener('voiceschanged', loadVoices);
  }

  // Web Speech exposes no gender field, so the only signal available is the voice
  // name. These are the female voices the major platforms actually ship for the
  // languages this kiosk speaks; anything unmatched falls through to whatever the
  // right language offers, because the correct language matters more than the
  // preferred gender.
  const FEMALE_HINTS = [
    'female', 'woman',
    // Windows / Edge
    'zira', 'hazel', 'susan', 'linda', 'heera', 'kalpana', 'bashkar',
    // Android / Chrome OS
    'aria', 'nanami', 'jenny', 'michelle', 'sonia', 'natasha', 'tanishaa',
    // macOS / iOS
    'samantha', 'victoria', 'karen', 'moira', 'tessa', 'fiona', 'alice',
    // Google voices are numbered, and the even ones are usually the female pair
    'google বাংলা', 'google us english',
  ];
  const MALE_HINTS = ['male', 'man', 'david', 'mark', 'daniel', 'alex', 'fred', 'rishi'];

  const matches = (voice, hints) => {
    const name = (voice.name || '').toLowerCase();
    return hints.some((hint) => name.includes(hint));
  };

  /** Voices that can actually pronounce a language, or an empty list. */
  const voicesFor = (language) => {
    const base = String(language || 'en').split('-')[0];
    return voices.filter((v) => (v.lang || '').replace('_', '-').startsWith(base));
  };

  const pickVoice = () => {
    // Only voices for THIS language. Falling back to the full list was the bug
    // behind "it will not read Bangla": with no Bangla voice installed the pool
    // became every voice, an English one was picked, and it was handed Bengali
    // script — which comes out as silence or noise. Better to report that nothing
    // can say it than to make a mess of it.
    const pool = voicesFor(speechLang);
    if (!pool.length) return null;

    // An exact provider voice name wins — an operator who typed one meant it.
    if (voiceName) {
      const exact = pool.find((v) => (v.name || '').toLowerCase().includes(voiceName.toLowerCase()));
      if (exact) return exact;
    }

    if (voiceGender === 'female') {
      const female = pool.find((v) => matches(v, FEMALE_HINTS));
      if (female) return female;
      // Nothing named female: at least avoid a voice that is clearly male.
      const neutral = pool.find((v) => !matches(v, MALE_HINTS));
      if (neutral) return neutral;
    } else if (voiceGender === 'male') {
      const male = pool.find((v) => matches(v, MALE_HINTS));
      if (male) return male;
    }

    // Exact region first, then any voice in the language.
    return pool.find((v) => v.lang === speechLang) || pool[0];
  };

  /** Can anything on this terminal pronounce that language? */
  const canSpeak = (language) => {
    if (serverTts) return true;  // a provider handles every language it advertises
    return browserTts && voicesFor(language).length > 0;
  };

  // The resolver of the utterance currently playing, so an interruption can
  // settle it. Without this, cutting a sentence short — a language switch, a
  // guest walking away, the microphone opening — left the awaiting caller hanging
  // on a promise nothing would ever resolve.
  let finishSpeaking = null;
  let playbackWatchdog = null;

  const stopSpeaking = () => {
    if (player) player.pause();
    if (browserTts) window.speechSynthesis.cancel();
    if (finishSpeaking) finishSpeaking(false);
  };

  const speakInBrowser = (text) =>
    new Promise((resolve) => {
      const voice = pickVoice();
      if (!voice) {
        // Nothing installed that speaks this language. Saying it with the wrong
        // voice is worse than not saying it, so this reports failure and lets the
        // caller decide.
        resolve(false);
        return;
      }

      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = speechLang;
      utterance.voice = voice;
      // Slightly under default: a lobby is noisy and a receptionist does not
      // read a room rate at conversational speed either.
      utterance.rate = 0.95;

      let settled = false;
      let watchdog = null;
      let poll = null;
      const done = (ok) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(watchdog);
        window.clearInterval(poll);
        setState(aiEnabled ? 'idle' : 'offline');
        resolve(ok);
      };
      utterance.onend = () => done(true);
      // Fires when the browser refuses to speak before a user gesture, which is
      // the case the greeting has to recover from.
      utterance.onerror = () => done(false);

      setState('speaking');
      window.speechSynthesis.speak(utterance);

      // Chrome can drop a pre-gesture utterance without firing onend OR onerror.
      // The greeting awaits this promise, so an unsettled one hangs the opening
      // of the conversation — check that it actually started instead of trusting
      // the events.
      watchdog = window.setTimeout(() => {
        const started = window.speechSynthesis.speaking || window.speechSynthesis.pending;
        if (!started) {
          done(false);
          return;
        }
        // It started. Now the other half of the same problem: Chrome can also drop an
        // utterance MID-sentence without firing onend or onerror, and this promise is
        // what the whole turn is waiting on — applyTurn reopens the microphone in its
        // .then(). An unsettled promise there is a microphone that never comes back,
        // which is what "the voice stops after a few seconds" turned out to be.
        //
        // Ask the engine rather than guessing at a duration: while it is genuinely
        // reading the answer out, `speaking` stays true. Two consecutive quiet polls
        // mean it has stopped without telling us. Cutting a real answer short is the
        // worse failure, so it takes two.
        let quiet = 0;
        poll = window.setInterval(() => {
          if (window.speechSynthesis.speaking || window.speechSynthesis.pending) {
            quiet = 0;
            return;
          }
          quiet += 1;
          if (quiet >= 2) done(true);
        }, 500);

        // And an absolute ceiling under it, for an engine that reports `speaking`
        // forever. Generous: Bangla at rate 0.95 is nowhere near a word every 700ms.
        const words = text.trim().split(/\s+/).length;
        watchdog = window.setTimeout(() => done(true), 5000 + words * 700);
      }, 400);
    });

  const wait = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

  // Declared here because openConversation, defined just below, sets it.
  let greeted = false;

  const failGracefully = (error) => {
    addBubble('system', `${t('unreachable', 'Reception is not available right now')} (${error.message}).`);
    setState('offline');
  };

  /** Open a conversation and deliver the whole opening, spoken. */
  const openConversation = () =>
    ensureConversation({ speakGreeting: true })
      .then(() => {
        greeted = true;
      })
      .catch(failGracefully);

  /** Speak the opening in both languages, and report whether audio played.
   *
   * Doubles as the autoplay-recovery path: a browser that refused the first time
   * gets asked again on the guest's first touch, and it has to re-speak BOTH
   * halves, not just whichever one happened to be current.
   */
  const speakOpening = async ({ addBubbles = true } = {}) => {
    if (!openingParts.length) return true;

    let played = true;
    for (const [index, part] of openingParts.entries()) {
      // The line appears as it is spoken. Showing both up front and then reading
      // them a second apart puts the text and the voice out of step, and a guest
      // reading ahead of the audio stops listening to it.
      if (index > 0 && addBubbles) addBubble('ai', part.text);

      const ok = await speakIn(part.language, part.text);
      played = played && ok;

      // A beat between the halves. Run together they sound like one long sentence
      // in a language the guest may only half understand, and the second half is
      // the one a foreign visitor is waiting for.
      const gap = Number(part.pause_after_ms || 0);
      if (gap > 0) await wait(gap);
    }
    return played;
  };

  /** Speak one line in a specific language, then restore the current one.
   *
   * Needed for the opening, which is bilingual. Setting `speechLang` for the whole
   * conversation instead would leave recognition listening in the wrong language
   * the moment the prompt finished.
   */
  const speakIn = async (language, text) => {
    const previous = speechLang;
    retune(language);
    try {
      return await speak(text);
    } finally {
      speechLang = previous;
    }
  };

  // Returns whether audio actually played. The caller needs to know: a browser
  // that blocks autoplay silently swallows the greeting, and the only fix is to
  // retry on the guest's first touch.
  const speak = async (text) => {
    if (muted || !text) return true;  // nothing owed

    // The microphone closes BEFORE a word is spoken, every time, wherever the answer
    // came from. With browser recognition the speaker feeds straight back into the
    // same device, so an open microphone during playback means the assistant hears
    // itself, transcribes itself, and answers itself.
    //
    // This used to `return true` instead — speak nothing, and pretend it was said.
    // That was the wrong half of the trade: the microphone is ours to close and the
    // answer is the guest's to hear. It also made every question the assistant asks
    // on its own initiative silent, because nothing had closed the microphone first
    // on that path.
    //
    // `autoListen` is untouched, so this is a mute rather than a stand-down: the loop
    // reopens itself the moment the speech ends (applyTurn's .then(rearm), and the
    // watchdog behind it).
    if (listening || recognition) pauseForAnswer();

    if (serverTts) {
      try {
        const blob = await post(API.speak, {
          text,
          voice: voiceName,
          gender: voiceGender,
          // The language of THIS answer, not the property's — the server picks the
          // voice from it, and the two diverge the moment a guest switches.
          language: speechLang,
        });
        if (!(blob instanceof Blob)) return true;
        stopSpeaking();
        player = new Audio(URL.createObjectURL(blob));
        setState('speaking');

        // play() resolves when playback STARTS, not when it finishes. Returning
        // there made every caller think the answer had been read out while it was
        // still on its first syllable — which is why the bilingual opening spoke
        // its two halves over the top of each other.
        //
        // This resolves on 'ended' instead, so "spoken" means spoken.
        const finished = new Promise((resolve) => {
          const settle = (ok) => {
            if (finishSpeaking !== settle) return;  // superseded by a newer utterance
            finishSpeaking = null;
            window.clearTimeout(playbackWatchdog);
            setState(aiEnabled ? 'idle' : 'offline');
            resolve(ok);
          };
          finishSpeaking = settle;

          player.onended = () => settle(true);
          player.onerror = () => settle(false);

          // Belt and braces: a stalled element that never fires 'ended' must not
          // leave a caller waiting for it. Sized from the real duration once the
          // browser knows it.
          const cap = () => {
            window.clearTimeout(playbackWatchdog);
            const ms = Number.isFinite(player.duration) ? player.duration * 1000 + 2000 : 30000;
            playbackWatchdog = window.setTimeout(() => settle(true), ms);
          };
          player.onloadedmetadata = cap;
          cap();
        });

        // Before play, not after: switching the sink mid-playback drops the
        // opening syllable on some devices.
        await routeOutput(player);
        await player.play();
        return finished;
      } catch (error) {
        // Fall through to the browser rather than going silent — a dead speech
        // key should not cost the guest the spoken answer.
        setState(aiEnabled ? 'idle' : 'offline');
      }
    }

    if (browserTts && (await speakInBrowser(text))) return true;

    // Nothing on this terminal can pronounce this language. Said once, not on
    // every turn — and the written answer is already on screen, so this is an
    // explanation rather than an error.
    warnNoVoice(speechLang);
    return true;
  };

  //: Which languages we have already reported as unspeakable.
  const warnedLanguages = new Set();

  const warnNoVoice = (language) => {
    const base = String(language || 'en').split('-')[0];
    if (warnedLanguages.has(base)) return;
    warnedLanguages.add(base);

    // Silence with no explanation reads as broken software. It is a missing
    // system voice, and it is fixable — so say which one and move on.
    addBubble('system', t('no_voice', 'This terminal has no voice for that language.'));
    setState(aiEnabled ? 'idle' : 'offline');
  };

  // The labels the booking card shows, in the order a receptionist would read
  // them back. Anything the guest has not said yet is simply absent — an empty
  // row reads as a demand.
  // Keys only. The labels used to live here in English, which is how a Bangla
  // kiosk ended up reading "Arriving / Nights / Phone" back to a guest who had
  // been spoken to in Bangla the whole way through.
  const BOOKING_ROWS = [
    'check_in',
    'nights',
    'room_code',
    'rooms',
    'adults',
    'children',
    'guest_name',
    'guest_phone',
  ];

  const rowLabel = (key) => (COPY.booking_rows || {})[key] || key;

  // The rooms, pictured, beside the conversation.
  //
  // Every card is drawn from booking.gallery, which the server built from the
  // priced snapshot it took this turn — so the picture cannot drift from the room
  // being sold, and a room type the hotel does not offer cannot appear.
  //
  // Re-rendered rather than patched: a booking turn changes which rooms are on
  // offer (dates move, a type sells out, the guest picks one), and diffing four
  // cards to save four <img> tags the browser has already cached is complexity
  // for nothing.
  const roomMeta = (room) => {
    // Only the facts this hotel actually filled in. "Sleeps 2 · · " is worse
    // than a shorter line.
    const bits = [];
    // The view is the hotelier's own free text; the map covers the values a
    // seeded property ships with and anything else passes through untouched.
    if (room.view) bits.push((COPY.views || {})[room.view] || room.view);
    if (room.bed) bits.push((COPY.beds || {})[room.bed] || room.bed);
    if (room.sleeps) bits.push(`${t('sleeps', 'Sleeps')} ${digits(room.sleeps)}`);
    if (room.size_sqm) bits.push(`${digits(room.size_sqm)} m²`);
    return bits.join(' · ');
  };

  const roomCard = (room) => {
    const photos = Array.isArray(room.photos) ? room.photos : [];
    const lead = photos[0];
    const card = document.createElement('div');
    card.className = `room-card${room.chosen ? ' is-chosen' : ''}`;

    const visual = lead
      ? `<img class="room-card__photo" src="${escapeHtml(lead.url)}" alt="${escapeHtml(room.name || '')}">`
      // No upload for this type yet. Its own facts on a tile, never a stock
      // bedroom standing in for a room the guest has not been given.
      : `<div class="room-card__blank">${escapeHtml(t('no_photo', 'Photo coming soon'))}</div>`;

    card.innerHTML = `
      ${visual}
      <div class="room-card__body">
        <div class="room-card__name">${escapeHtml(room.name || room.code || '')}</div>
        <div class="room-card__meta">${escapeHtml(roomMeta(room))}</div>
      </div>`;

    if (lead && lead.caption) {
      const caption = document.createElement('div');
      caption.className = 'room-card__caption';
      caption.textContent = lead.caption;
      card.appendChild(caption);
    }

    // The rest of this type's photos. Tap swaps the lead image — a guest wanting
    // to see the bathroom should not have to ask the assistant for it.
    if (photos.length > 1) {
      const strip = document.createElement('div');
      strip.className = 'room-card__strip';
      photos.forEach((photo, index) => {
        const thumb = document.createElement('img');
        thumb.src = photo.url;
        thumb.alt = photo.caption || '';
        if (index === 0) thumb.classList.add('is-active');
        thumb.addEventListener('click', () => {
          const main = card.querySelector('.room-card__photo');
          if (main) main.src = photo.url;
          const captionNode = card.querySelector('.room-card__caption');
          if (captionNode) captionNode.textContent = photo.caption || '';
          strip.querySelectorAll('img').forEach((node) => node.classList.remove('is-active'));
          thumb.classList.add('is-active');
        });
        strip.appendChild(thumb);
      });
      card.appendChild(strip);
    }
    return card;
  };

  // Which room the conversation has already shown a picture of.
  //
  // Once the guest has settled on a room, the next four turns are name, phone,
  // "confirm?" — and re-attaching the same photograph to each of those answers
  // is a column of the same picture, which reads as a bug. So: once per room,
  // again when they switch to a different one.
  let picturedRoom = '';

  const bubblePhoto = (booking) => {
    const gallery = (booking && booking.gallery) || [];
    // Only the room being taken. While the guest is still choosing, the gallery
    // is the place for four options; a bubble can honestly carry one.
    const room = gallery.find((entry) => entry.chosen) || (gallery.length === 1 ? gallery[0] : null);
    if (!room || !room.photos || !room.photos.length) {
      // Backed out or switched away, so the next choice gets its picture even if
      // it is the same room code as last time.
      //
      // Only when this turn actually carried booking state: a guest asking for
      // the wifi password mid-booking sends none, and treating that as "they
      // changed their mind" would re-post the same photograph afterwards.
      if (!room && booking && !booking.room_code) picturedRoom = '';
      return null;
    }
    if (room.code === picturedRoom) return null;
    picturedRoom = room.code;
    return { url: room.photos[0].url, alt: room.name || room.code };
  };

  const renderRooms = (booking) => {
    if (!el.rooms || !el.roomsList) return;
    const rooms = (booking && booking.gallery) || [];
    if (!rooms.length) {
      el.rooms.classList.add('is-hidden');
      el.roomsList.replaceChildren();
      return;
    }
    // One card means the guest has chosen; the heading says so rather than
    // continuing to offer.
    const chosen = rooms.length === 1 || rooms.some((room) => room.chosen);
    el.roomsTitle.textContent = chosen
      ? t('rooms_one', 'Your room')
      : t('rooms_some', 'Rooms available');
    el.roomsList.replaceChildren(...rooms.map(roomCard));
    el.rooms.classList.remove('is-hidden');
  };

  // The last booking payload, so switching language redraws the card and the
  // gallery immediately instead of leaving English labels on screen until the
  // guest happens to say something else.
  let lastBooking = null;

  // Which of the four steps are behind the guest, and which one is being asked
  // for now. Read off the validated draft, never off what the assistant said —
  // the assistant claiming a booking is confirmed does not make it so.
  const renderProgress = (booking) => {
    const steps = root.querySelector('#kiosk-progress');
    if (!steps) return;
    const done = {
      dates: Boolean(booking && booking.check_in && booking.nights),
      room: Boolean(booking && booking.room_code),
      guest: Boolean(booking && booking.guest_name && booking.guest_phone),
      confirmed: Boolean(booking && booking.code),
    };
    // The first one not done is the one the assistant is asking about.
    const current = ['dates', 'room', 'guest', 'confirmed'].find((key) => !done[key]);
    steps.querySelectorAll('.progress-step').forEach((node) => {
      const key = node.dataset.step;
      node.classList.toggle('is-done', done[key]);
      node.classList.toggle('is-current', key === current);
    });
  };

  // The public booking page's rail: the bill, then the slip.
  //
  // Both read the same validated draft the booking card does. The assistant's own
  // sentence is never a source for a number here — it says what it likes, and the
  // server says what is true.
  //
  // Absent on the terminal and the console, where the rail is the arrival's vision
  // steps instead, so every lookup is null-guarded rather than assumed.
  const renderOnlineRail = (booking) => {
    const bill = root.querySelector('#online-bill-card');
    const slipCard = root.querySelector('#online-slip-card');
    if (!bill || !slipCard) return;

    const rows = (target, pairs) => {
      target.innerHTML = pairs
        .filter(([, value]) => value !== '' && value !== null && value !== undefined)
        .map(
          ([label, value]) =>
            `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(digits(value))}</dd>`
        )
        .join('');
    };

    // A bill needs something to bill for. Before a room is settled it says nothing
    // rather than showing a total of zero, which reads as "free".
    const hasQuote = Boolean(booking && booking.total && booking.room_code);
    bill.classList.toggle('d-none', !hasQuote);
    if (hasQuote) {
      rows(root.querySelector('#online-bill-rows'), [
        [rowLabel('room_code'), booking.room_code],
        [rowLabel('nights'), booking.nights],
        [rowLabel('rooms'), booking.rooms],
        [t('booking_total', 'Total'), `${booking.total} ${booking.currency || ''}`],
      ]);
      const state = root.querySelector('#online-bill-state');
      // "estimate" until a reservation exists, because until then the price can
      // still move under the guest — a room can sell out and the dates can change.
      if (state) {
        state.textContent = booking.code
          ? t('slip_ready', 'confirmed')
          : t('bill_estimate', 'estimate');
        state.className = booking.code ? 'pill pill--ok' : 'pill';
      }
    }

    // The slip appears when a reservation actually exists. `code` is issued by the
    // write, so it cannot appear until one has happened.
    const confirmed = Boolean(booking && booking.code);
    slipCard.classList.toggle('d-none', !confirmed);
    if (confirmed) {
      const ref = root.querySelector('#online-slip-ref');
      if (ref) ref.textContent = booking.code;
      rows(root.querySelector('#online-slip-rows'), [
        [rowLabel('guest_name'), booking.guest_name],
        [rowLabel('check_in'), booking.check_in],
        [rowLabel('nights'), booking.nights],
        [rowLabel('room_code'), booking.room_code],
        [t('booking_total', 'Total'), `${booking.total} ${booking.currency || ''}`],
      ]);
    }
  };

  const renderBooking = (booking) => {
    lastBooking = booking;
    renderOnlineRail(booking);
    renderProgress(booking);
    renderRooms(booking);
    const card = el.bookingCard;
    if (!card) return;
    if (!booking) {
      card.classList.add('d-none');
      return;
    }
    card.classList.remove('d-none');

    const rows = BOOKING_ROWS.filter((key) => {
      const value = booking[key];
      return value !== '' && value !== null && value !== undefined && value !== 0;
    }).map(
      (key) =>
        `<dt>${escapeHtml(rowLabel(key))}</dt><dd>${escapeHtml(digits(booking[key]))}</dd>`
    );

    if (booking.total) {
      // Server-computed, from the same pricing service the folio uses.
      rows.push(`<dt>${escapeHtml(t('booking_total', 'Total'))}</dt><dd class="booking-draft__total">${escapeHtml(digits(booking.total))} ${escapeHtml(booking.currency || '')}</dd>`);
    }
    el.bookingRows.innerHTML = rows.join('');

    const confirmed = Boolean(booking.code);
    // The reference stays in Latin digits: a guest reads it out at the desk and a
    // receptionist types it into a field that only accepts what the server issued.
    el.bookingState.textContent = confirmed ? booking.code : t('booking_draft', 'in progress');
    el.bookingState.className = confirmed ? 'pill pill--ok' : 'pill';

    const issues = booking.issues || [];
    if (issues.length) {
      el.bookingNote.textContent = issues.join(' · ');
      el.bookingNote.classList.remove('d-none');
    } else {
      el.bookingNote.classList.add('d-none');
    }
  };

  const currentLanguage = () => (root.dataset.language || 'en').split('-')[0];

  // Everything on the screen that is words, in one place.
  //
  // Called at load and again on every language change. The alternative — letting
  // each feature relabel its own corner — is how a screen ends up half switched:
  // the buttons in Bangla, the booking card and the rail still in English, because
  // those two were written on different days.
  const setText = (node, value) => {
    if (node && value) node.textContent = value;
  };

  const applyChrome = () => {
    setText(root.querySelector('#kiosk-title'), t('kiosk_title'));
    setText(document.getElementById('kiosk-brand-sub'), t('brand_sub'));
    setText(el.state, stateLabel(currentState));

    setText(root.querySelector('#kiosk-send'), t('send'));
    setText(el.human, t('human'));
    if (el.reset) el.reset.title = t('reset_title');
    if (el.mute) el.mute.title = t('mute_title');
    setText(root.querySelector('#kiosk-voice-label'), `${t('voice_label')}:`);
    setText(root.querySelector('#kiosk-turns-label'), t('turns'));
    const turnsChip = root.querySelector('#kiosk-turns-chip');
    if (turnsChip) turnsChip.title = t('turns');

    if (el.input) {
      el.input.placeholder = t('placeholder');
      el.input.setAttribute('aria-label', t('aria_message'));
    }
    if (el.bubbles) el.bubbles.setAttribute('aria-label', t('aria_conversation'));
    if (el.langPick) el.langPick.setAttribute('aria-label', t('language_label'));
    if (el.voicePick) {
      el.voicePick.setAttribute('aria-label', t('voice_label'));
      const names = { female: 'voice_female', male: 'voice_male', any: 'voice_any' };
      Array.from(el.voicePick.options).forEach((option) => {
        setText(option, t(names[option.value]));
      });
    }

    // The microphone hint doubles as a live status ("I can hear you…"), so only
    // the resting label is safe to overwrite from here.
    if (el.micHint && !voiceDisabled && !autoListen && !recorder) {
      el.micHint.textContent = restingHint();
    }
    if (el.mic) {
      el.mic.setAttribute(
        'aria-label',
        handsFree && browserStt ? t('mic_stop') : t('tap')
      );
    }

    // A property that wrote its own hint owns those words, in whatever language it
    // wrote them.
    const hint = root.querySelector('#kiosk-hint');
    if (hint && hint.dataset.custom !== 'true') hint.textContent = t('hint');

    setText(root.querySelector('#kiosk-booking-title'), t('booking_title'));

    // The booking page's rail. Left out of this list, its titles kept whichever
    // language the page was served in while everything around them switched —
    // which is the exact half-switched screen the language rule exists to stop.
    const railText = [
      ['#online-bill-card .card-title-ashos', 'bill_title'],
      ['#online-slip-card .card-title-ashos', 'slip_title'],
      ['#online-slip-card .online-slip__ref span', 'slip_reference'],
      ['#online-slip-card .online-slip__actions .btn', 'slip_print'],
    ];
    railText.forEach(([selector, key]) => setText(root.querySelector(selector), t(key)));
    const slipPill = root.querySelector('#online-slip-card .pill');
    if (slipPill) slipPill.textContent = t('slip_ready');

    const picker = devices();
    if (picker && picker.relabel) picker.relabel(COPY.devices || {});

    const panels = PANELS[currentLanguage()] || {};
    root.querySelectorAll('[data-panel]').forEach((card) => {
      const words = panels[card.dataset.panel];
      setText(card.querySelector('[data-panel-pill]'), t('pill_ready'));
      setText(card.querySelector('[data-panel-blank]'), t('not_enabled'));
      if (!words) return;
      setText(card.querySelector('[data-panel-title]'), words.title);
      setText(card.querySelector('[data-panel-note]'), words.note);
    });
    const shot = root.querySelector('#kiosk-shot-status');
    if (shot && shot.dataset.live !== 'true') shot.textContent = t('waiting_guest');
    const thumb = root.querySelector('#kiosk-shot-thumb');
    if (thumb) thumb.alt = t('guest_photo_alt');

    // Tiles carry a prompt as well as a label: tapping one sends it as the guest's
    // own message, so an English prompt on a Bangla kiosk asks the model the
    // question in the wrong language and gets the answer back in it.
    const tiles = (ALL_COPY[currentLanguage()] || {}).tiles || [];
    tiles.forEach((tile) => {
      const node = root.querySelector(`[data-tile="${tile.key}"]`);
      if (!node) return;
      node.dataset.prompt = tile.prompt;
      setText(node.querySelector('.tile__label'), tile.label);
      setText(node.querySelector('.tile__sub'), tile.sub);
    });

    // Anything whose contents were built from the old language.
    if (lastBooking) renderBooking(lastBooking);
  };

  /** Point the whole screen at one language. Safe to call with the current one. */
  const setChromeLanguage = (language) => {
    const code = String(language || '').split('-')[0];
    if (!code || !ALL_COPY[code] || code === currentLanguage()) return;
    COPY = ALL_COPY[code];
    root.dataset.language = code;
    if (el.langPick) el.langPick.value = code;
    applyChrome();
    // Bangla and English wrap to different numbers of lines, so the bottom moved.
    scrollToLatest();
    // The device bar and the consent overlay are separate scripts with their own
    // copy. One event rather than three globals.
    root.dispatchEvent(
      new CustomEvent('ashos:language', { bubbles: true, detail: { language: code } })
    );
  };

  // --- The silence timer -----------------------------------------------------
  //
  // Armed after every assistant turn, cancelled by any sign of the guest. What it
  // fires is not a model call: the server derives the question from the validated
  // booking draft, so an idle tab costs nothing and cannot be asked for something
  // the guest already answered.

  const stopNudge = () => {
    if (nudgeTimer) {
      window.clearTimeout(nudgeTimer);
      nudgeTimer = null;
    }
  };

  /** Guest did something. Silence is over and the count starts again. */
  const guestIsHere = () => {
    nudgesSent = 0;
    stopNudge();
  };

  const armNudge = () => {
    stopNudge();
    if (!nudgeAfter || !conversationId || nudgesSent >= MAX_NUDGES) return;
    nudgeTimer = window.setTimeout(askNext, nudgeAfter);
  };

  const askNext = async () => {
    nudgeTimer = null;
    // Not over the top of anything: mid-request, mid-sentence, or while the guest
    // is speaking are all moments when the guest is not, in fact, silent.
    if (busy || listening || nudgesSent >= MAX_NUDGES || !conversationId) return;
    if (browserTts && window.speechSynthesis.speaking) {
      armNudge();
      return;
    }
    // Somebody typing a name into the box has not gone quiet, they are answering.
    if (el.input && el.input.value.trim()) {
      armNudge();
      return;
    }

    nudgesSent += 1;
    try {
      const data = await post(API.nudge, { conversation: conversationId });
      // 204: nothing worth asking — a closed conversation, or a guest who has not
      // spoken yet. Stop rather than retry; there is no state that changes on its
      // own to make the answer different.
      if (!data || !data.reply) {
        nudgesSent = MAX_NUDGES;
        return;
      }
      applyTurn(data, null);
    } catch (error) {
      // A nudge nobody asked for must never surface as an error bubble. The guest
      // did not do anything; there is nothing to apologise to them for.
      nudgesSent = MAX_NUDGES;
    }
  };

  const applyTurn = (data, typingNode) => {
    if (typingNode) typingNode.remove();
    // Before speaking: the voice has to be chosen for the language of THIS
    // answer, not the language the kiosk was configured in.
    retune(data.language);
    // A guest who switched by talking rather than tapping must not be shown a
    // chip claiming the old language — or a screen still labelled in it. Speaking
    // is the case that matters: somebody who cannot type Bangla asks in Bangla,
    // the server answers in Bangla, and the chrome follows both of them.
    setChromeLanguage(data.language);
    addBubble('ai', data.reply, bubblePhoto(data.booking));
    renderBooking(data.booking);

    // Only where somebody was actually notified. The server does not set handoff on
    // a self-serve channel, and this second guard is deliberate belt-and-braces: the
    // one sentence on this screen that must never be shown falsely is the one
    // promising a person.
    if (data.handoff && staffed) {
      addBubble('system', t('staff_coming', 'A member of staff has been notified and is on the way.'));
    }
    if (data.reservation_code) {
      // Only now is there a stay to attach anything to. kiosk-enrol.js decides
      // whether to ask about a photo; this file does not open a camera.
      root.dispatchEvent(
        new CustomEvent('ashos:booking-confirmed', {
          bubbles: true,
          detail: {
            conversation: conversationId,
            reservation: data.reservation_code,
            language: root.dataset.language || '',
          },
        })
      );
    }
    if (el.turnCount) el.turnCount.textContent = digits(data.turn_count ?? 0);
    setState(aiEnabled ? 'idle' : 'offline');
    // Speak, then listen again. Reopening the microphone before the answer has
    // finished playing would have the kiosk transcribe its own voice.
    speak(data.reply).then(() => {
      restarts = 0;
      rearm();
      // The silence starts when the assistant stops talking, not when the answer
      // appeared on screen. Timing it from the text would count the time spent
      // reading the answer out as time the guest sat there saying nothing.
      armNudge();
    });
  };

  const send = async (text) => {
    if (busy || !text.trim()) return;
    busy = true;
    // The guest spoke. Any pending question is theirs to answer now, and the count
    // resets so a later silence gets the full three attempts again.
    guestIsHere();
    // Typed or spoken, the microphone closes while the answer is produced and
    // read out, then the loop reopens it.
    pauseForAnswer();
    el.input.value = '';
    addBubble('guest', text);
    setState('thinking');
    const typing = addTyping();

    try {
      await ensureConversation();
      const data = await post(API.chat, {
        conversation: conversationId,
        message: text,
        language: pinnedLanguage,
      });
      applyTurn(data, typing);
    } catch (error) {
      typing.remove();
      addBubble('system', `${t('sorry', 'Sorry')} — ${error.message}.`);
      setState('idle');
    } finally {
      busy = false;
      // The turn is over however it went. On the error path nothing else reopens the
      // microphone — applyTurn does that in its .then(), and we never got there — so a
      // failed request used to cost the guest their voice for the rest of the session.
      pausing = false;
      rearm();
      el.input.focus();
    }
  };

  // --- Voice -----------------------------------------------------------------
  const startRecording = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraint() });
    // Device labels are blank until a permission exists; this is the moment they
    // become readable, so let the bar re-read them.
    root.dispatchEvent(new CustomEvent('ashos:media-granted', { bubbles: true }));

    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    audioCtx.createMediaStreamSource(stream).connect(analyser);

    const chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (event) => event.data.size && chunks.push(event.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      if (audioCtx) { audioCtx.close(); audioCtx = null; }
      analyser = null;

      const blob = new Blob(chunks, { type: 'audio/webm' });
      if (blob.size < 1200) {
        addBubble('system', t('too_short', 'That was too short to hear.'));
        setState('idle');
        return;
      }

      setState('thinking');
      busy = true;
      const typing = addTyping();
      try {
        await ensureConversation();
        const form = new FormData();
        form.append('conversation', conversationId);
        form.append('audio', blob, 'speech.webm');
        const data = await post(API.voice, form, true);
        typing.remove();
        if (data.transcript) {
          // The guest sees what was actually heard, not just what it produced.
          el.input.value = '';
          addBubble('guest', data.transcript);
        }
        applyTurn(data, null);
      } catch (error) {
        typing.remove();
        // A speech backend that is missing or unauthorised will fail the same
        // way every time. Retrying it just produces the same red box again, so
        // stand the microphone down for this session and say so once.
        disableVoice(`Voice is unavailable — ${error.message}`);
      } finally {
        busy = false;
      }
    };

    recorder.start();
    setState('listening');
    el.mic.classList.add('is-recording');
    el.micHint.textContent = t('sending', 'Listening — tap again to send');
  };

  const stopRecording = () => {
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    recorder = null;
    if (recognition) {
      try {
        recognition.stop();
      } catch (error) {
        /* already stopping */
      }
    }
    listening = false;
    el.mic.classList.remove('is-recording');
    el.micHint.textContent = voiceDisabled ? el.micHint.textContent : t('tap', 'Tap to speak');
  };

  // --- Hands-free loop -------------------------------------------------------

  const standDown = (hintKey) => {
    autoListen = false;
    stopRecording();
    el.mic.classList.remove('is-armed', 'is-hearing');
    if (!voiceDisabled) el.micHint.textContent = t(hintKey || 'standby', 'Tap to speak');
    setState(aiEnabled ? 'idle' : 'offline');
  };

  /** Close the microphone for the duration of one answer.
   *
   * Not a stand-down: ``autoListen`` stays true, so the loop reopens itself as
   * soon as the answer has finished playing. Without this the kiosk hears its own
   * voice and answers itself.
   */
  const pauseForAnswer = () => {
    pausing = true;
    if (recognition) {
      try {
        recognition.abort();
      } catch (error) {
        /* already closing */
      }
    }
    listening = false;
    el.mic.classList.remove('is-recording', 'is-armed', 'is-hearing');
  };

  /** Open the microphone again, if now is a sensible moment. */
  const rearm = () => {
    if (!autoListen || voiceDisabled || busy || listening || suspended) return;

    // Never while the answer is being read out: browser recognition captures the
    // same device the speaker feeds, and the kiosk would transcribe itself.
    //
    // Both engines have to be checked. speak() resolves for the server path as
    // soon as play() is accepted — which is when playback STARTS, not when it
    // finishes — so waiting on the promise alone would reopen the microphone over
    // the top of the answer.
    const stillTalking =
      (browserTts && window.speechSynthesis.speaking) ||
      (player && !player.paused && !player.ended);

    if (stillTalking) {
      // Believed, but not indefinitely. A `speaking` flag that never clears —
      // Chrome, asked for a language it has no voice for — would otherwise hold the
      // microphone shut for the rest of the session, one 400ms deferral at a time.
      if (!talkingSince) talkingSince = performance.now();
      if (performance.now() - talkingSince < TTS_PATIENCE_MS) {
        window.setTimeout(rearm, 400);
        return;
      }
      // Overruled. Whatever it thinks it is saying, it has had long enough.
      stopSpeaking();
    }
    talkingSince = 0;

    try {
      listenInBrowser();
    } catch (error) {
      // Nearly always InvalidStateError from a session that has not finished
      // closing. Back off and try again — there is no failure count that should end
      // with a microphone the guest has to tap.
      restarts += 1;
      window.setTimeout(rearm, retryDelay());
    }
  };

  /** Ask the browser for the microphone, so nothing has to be tapped.
   *
   * SpeechRecognition works without a gesture once the origin HAS the permission,
   * but a cold first visit has to obtain it. getUserMedia triggers the browser's
   * own permission bubble — a browser dialog, not a control on the page — and the
   * grant then persists for the origin, so this is the last time anybody has to
   * agree to anything on that terminal.
   *
   * The track is released immediately: the point is the permission, not the audio.
   * Holding a stream open would light the tab's recording indicator all day for no
   * reason and fight the recogniser for the device.
   */
  const requestMicPermission = async () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return false;

    // The chosen microphone first, then any microphone. An `exact` deviceId that
    // has since been unplugged throws OverconstrainedError, and treating that as
    // "no microphone here" would leave a terminal mute because of a device
    // somebody swapped out last week.
    for (const audio of [audioConstraint(), true]) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio });
        stream.getTracks().forEach((track) => track.stop());
        // Device labels only become readable once a permission exists.
        root.dispatchEvent(new CustomEvent('ashos:media-granted', { bubbles: true }));
        return true;
      } catch (error) {
        if (error && error.name === 'NotAllowedError') return false;  // a real refusal
      }
    }
    return false;
  };

  /** Reopen the microphone if it should be open and is not.
   *
   * Every path above reschedules itself, and that was still not enough: a dropped
   * recognition session, a timer throttled while the tab was in the background, or
   * one exception on a path that forgot to reschedule, and the loop is over with the
   * screen still saying "I am listening".
   *
   * A 3-second clock cannot miss any of those, because it does not care why. The
   * only states it respects are the ones where the microphone is *meant* to be shut:
   * a turn in flight, an answer being read out, another screen holding the device,
   * or a guest who shut it themselves.
   */
  const watchMic = () => {
    // `pausing` is deliberately NOT a guard here. It exists so onend can tell our own
    // abort from a failure, and it is cleared by that onend — which never runs if
    // there was no live session to abort. A flag that can latch must not be the thing
    // standing between the guest and a working microphone; `busy` already covers "a
    // turn is in flight", and rearm() defers by itself while an answer is playing.
    if (!autoListen || voiceDisabled || suspended || busy || listening) return;
    rearm();
  };

  /** Start the loop. Called on load — nothing needs tapping. */
  const startHandsFree = async () => {
    if (!handsFree || !browserStt || voiceDisabled || autoListen || suspended) return;

    // Ask, but do not depend on the answer. The probe can fail for reasons that
    // say nothing about whether recognition will work — the device momentarily
    // busy, or a remembered microphone that has since been unplugged — and Chrome
    // may well hold the permission already. Refusing to arm on a failed probe made
    // the microphone dead in exactly those cases.
    const granted = await requestMicPermission();

    autoListen = true;
    restarts = 0;

    // Give the device a moment to come back after the probe released it. Starting
    // recognition on top of a closing stream is what produces 'audio-capture'.
    window.setTimeout(rearm, granted ? 250 : 0);

    // And from here on it is watched rather than trusted. One interval for the life
    // of the page — armed once, because startHandsFree() is also the resume path and
    // a second interval would double every reopen attempt.
    if (!micWatchdog) micWatchdog = window.setInterval(watchMic, MIC_WATCHDOG_MS);
  };

  // --- Browser speech recognition --------------------------------------------
  // Used when no STT provider is configured. Chrome and Edge only; Firefox and
  // Safari report the API missing and the mic disables itself with an honest
  // message rather than failing on the first press.
  //
  // No waveform on this path. SpeechRecognition captures the microphone itself
  // and hands back no audio stream, and opening a second getUserMedia purely to
  // animate bars would fight it for the device on some machines. The interim
  // transcript appearing in the input box is better feedback anyway: it shows
  // what was actually heard, not that something was heard.
  const listenInBrowser = () => {
    recognition = new SpeechRecognitionApi();
    recognition.lang = speechLang;
    recognition.interimResults = true;
    recognition.continuous = false;
    recognition.maxAlternatives = 1;

    let finalText = '';

    recognition.onresult = (event) => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const chunk = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += chunk;
        else interim += chunk;
      }
      // Show it landing, so a guest who is not being understood can see that
      // before they finish a sentence.
      el.input.value = (finalText + interim).trim();
    };

    // Blink only while it is genuinely hearing something. A microphone that looks
    // busy the whole time tells the guest nothing; one that reacts to their voice
    // tells them they are being picked up.
    recognition.onspeechstart = () => {
      el.mic.classList.add('is-hearing');
      el.micHint.textContent = t('hearing', 'I can hear you…');
      // Mid-sentence is not silence. Without this the assistant would talk over a
      // guest who took a breath between "two nights" and "starting Friday".
      guestIsHere();
    };
    recognition.onspeechend = () => {
      el.mic.classList.remove('is-hearing');
    };

    recognition.onerror = (event) => {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        // Denied once is denied for the session. Asking again on a loop is worse
        // than not asking.
        autoListen = false;
        disableVoice(t('mic_blocked', 'The microphone was blocked'));
        return;
      }
      if (event.error === 'no-speech') {
        // Ordinary in hands-free — nobody is talking yet. Not worth reporting.
        if (!autoListen) el.micHint.textContent = t('nothing_heard', 'I did not hear anything');
        return;
      }
      if (event.error === 'aborted') return;

      // Everything else is transient until proven otherwise, and this used to be
      // the opposite: ONE unknown error stood the microphone down for the whole
      // session. The common one is 'audio-capture' — the device is momentarily
      // busy, which happens routinely right after the permission probe releases
      // it — and killing voice for the session over that is a permanent fix to a
      // problem that lasted 200ms.
      //
      // Only an actual refusal (handled above) is permanent. The rest back off and
      // try again — forever, capped at RETRY_MAX_MS. A guest who is told the
      // microphone is listening must not have it quietly retire behind a transient
      // error from the browser's speech service.
      if (autoListen) {
        restarts += 1;
        window.setTimeout(rearm, retryDelay());
        return;
      }
      el.micHint.textContent = t('tap', 'Tap to speak');
      setState(aiEnabled ? 'idle' : 'offline');
    };

    recognition.onend = () => {
      listening = false;
      recognition = null;
      el.mic.classList.remove('is-recording', 'is-hearing');

      const said = (finalText || el.input.value).trim();
      if (said) {
        restarts = 0;
        send(said);  // reopens itself once the answer has been spoken
        return;
      }

      if (pausing) {
        // We closed it ourselves to answer. Not a failure, and not our job to
        // reopen — applyTurn does that once the answer has finished playing.
        pausing = false;
        return;
      }

      if (autoListen) {
        // Chrome closes the session after a pause even with nothing said. Reopening
        // that is the NORMAL case, so it must not count towards the failure cap —
        // counting it meant a terminal nobody spoke to for a couple of minutes
        // quietly switched its own microphone off.
        el.mic.classList.add('is-armed');
        el.micHint.textContent = t('armed', 'Go ahead, I am listening');
        window.setTimeout(rearm, 250);
        return;
      }

      if (!voiceDisabled) el.micHint.textContent = t('tap', 'Tap to speak');
      setState(aiEnabled ? 'idle' : 'offline');
    };

    // The speaker feeds straight back into the microphone otherwise, and the
    // kiosk starts answering its own last sentence.
    stopSpeaking();

    recognition.start();
    listening = true;
    el.input.value = '';
    setState('listening');
    el.mic.classList.add('is-recording');
    el.mic.classList.toggle('is-armed', autoListen);
    el.micHint.textContent = autoListen
      ? t('armed', 'Go ahead, I am listening')
      : t('sending', 'Listening — tap again to send');
  };

  const disableVoice = (message) => {
    voiceDisabled = true;
    autoListen = false;
    stopRecording();
    el.mic.disabled = true;
    el.mic.classList.remove('is-recording');
    el.micHint.textContent = t('voice_off', 'Voice unavailable — please type');
    addBubble('system', `${message}. ${t('type_instead', 'You can type your question instead.')}`);
    setState(aiEnabled ? 'idle' : 'offline');
  };

  const toggleMic = async () => {
    if (busy || voiceDisabled) return;

    // In hands-free the button becomes stop/resume rather than push-to-talk. A
    // guest who wants the microphone shut must be able to shut it.
    if (handsFree && browserStt) {
      if (autoListen) standDown('standby');
      else startHandsFree();
      return;
    }

    if (recorder || listening) { stopRecording(); return; }
    try {
      if (serverStt) await startRecording();
      else listenInBrowser();
    } catch (error) {
      disableVoice(t('no_reach_mic', 'I could not reach the microphone'));
    }
  };

  // --- Wiring ----------------------------------------------------------------
  // Label the screen before anything else runs. The server rendered it in the
  // property's language already; this makes the load path and the switch path the
  // same code, so a key that only the switch sets cannot go missing at load.
  applyChrome();
  buildWave();
  waveTimer = window.setInterval(animateWave, 60);
  setState(aiEnabled ? 'idle' : 'offline');

  el.form.addEventListener('submit', (event) => {
    event.preventDefault();
    send(el.input.value);
  });

  el.tiles.forEach((tile) => {
    tile.addEventListener('click', () => send(tile.dataset.prompt));
  });

  if (el.input) {
    // Typing is not silence, but a half-typed message is not an answer either: the
    // clock restarts from the last keystroke rather than being cancelled outright,
    // so a guest who starts typing their name and then stops still gets asked.
    el.input.addEventListener('input', () => {
      guestIsHere();
      armNudge();
    });
  }

  if (el.human) {
    el.human.addEventListener('click', async () => {
      try {
        await ensureConversation();
        const data = await post(API.handoff, { conversation: conversationId });
        addBubble('ai', data.reply);
        addBubble('system', t('staff_notified', 'Staff notified.'));
      } catch (error) {
        addBubble('system', `${t('desk_unreachable', 'Could not reach the desk')} — ${error.message}.`);
      }
    });
  }

  if (el.reset) {
    el.reset.addEventListener('click', () => {
      conversationId = null;
      pinnedLanguage = '';
      spokenGreeting = false;
      // A pending question belongs to the guest who has just left. Firing it into
      // the next guest's empty screen would open their conversation by asking them
      // for a phone number.
      guestIsHere();
      el.bubbles.innerHTML = '';
      // The next guest starts from nothing, pictures included.
      picturedRoom = '';
      renderBooking(null);
      setState(aiEnabled ? 'idle' : 'offline');
      // openConversation, not ensureConversation: the next guest gets the whole
      // opening — both lines, spoken, with the beat between them. Calling the
      // plain version left only the first line on screen and said nothing.
      openConversation();
    });
  }

  if (el.langPick) {
    el.langPick.value = (root.dataset.language || 'en').split('-')[0];

    el.langPick.addEventListener('change', async () => {
      pinnedLanguage = el.langPick.value;
      // Relabel first, before the round trip. A tap that changes nothing on screen
      // for a second and a half reads as a control that did not work, and the
      // guest taps it again.
      setChromeLanguage(pinnedLanguage);
      // Retune before anything is said, so the microphone is already listening in
      // the new language by the time the guest replies.
      retune(pinnedLanguage);
      stopSpeaking();

      try {
        await ensureConversation();
        // Routed through the normal turn so the server records the switch and
        // answers the confirmation itself — the client does not compose Bangla.
        busy = true;
        pauseForAnswer();
        const data = await post(API.chat, {
          conversation: conversationId,
          message: pinnedLanguage === 'bn' ? 'বাংলা' : 'English',
          language: pinnedLanguage,
        });
        busy = false;
        applyTurn(data, null);
      } catch (error) {
        busy = false;
        addBubble('system', `${t('sorry', 'Sorry')} — ${error.message}.`);
      }
    });
  }

  if (el.voicePick) {
    el.voicePick.value = voiceGender;
    el.voicePick.addEventListener('change', () => {
      voiceGender = el.voicePick.value;
      localStorage.setItem(voiceKey, voiceGender);
      // Stop mid-sentence rather than finishing the old answer in the old voice —
      // the guest just asked for a different one, and hearing the change is the
      // only confirmation that the control did anything.
      stopSpeaking();
      if (lastGreeting) speak(lastGreeting);
    });
  }

  if (el.mute && !ttsEnabled) {
    el.mute.disabled = true;
    el.mute.title = t('no_speak', 'Nothing can speak on this terminal');
  } else if (el.mute) {
    el.mute.addEventListener('click', () => {
      muted = !muted;
      el.mute.textContent = muted ? '🔇' : '🔊';
      el.mute.setAttribute('aria-pressed', String(muted));
      if (muted) stopSpeaking();
    });
  }

  // What the microphone can actually do here, said precisely. The old hint
  // blamed the configuration even when the browser could have handled it, which
  // is a wrong answer dressed up as a limitation.
  const canRecord = Boolean(navigator.mediaDevices && window.MediaRecorder);
  const micUsable = (serverStt && canRecord) || browserStt;

  if (!micUsable) {
    voiceDisabled = true;
    el.mic.disabled = true;
    el.micHint.textContent = serverStt
      ? t('no_record', 'This browser cannot record audio — please type')
      : t('no_input', 'This browser has no speech input — please type');
  } else {
    el.mic.addEventListener('click', toggleMic);
    // Through t(), like every other word on this screen. A bare literal here is
    // how a fully Bangla kiosk still said "Tap to speak" under the microphone —
    // the one control a guest looks at before they say anything at all.
    //
    // And it says what this page actually does — see restingHint(). Arming replaces
    // it milliseconds later anyway; starting on 'tap' showed a flash of the wrong
    // answer, and a permanently wrong one on a browser that took a moment to hand
    // over the device.
    el.micHint.textContent = restingHint();
  }

  window.addEventListener('beforeunload', () => {
    window.clearInterval(waveTimer);
    window.clearInterval(micWatchdog);
    stopNudge();
    stopRecording();
  });

  // --- Opening the conversation ----------------------------------------------
  // The assistant is the front door: it greets as soon as the page is up, out
  // loud where the browser allows it. No camera, no scan frame, nothing pointed
  // at the guest before a word has been exchanged.
  // Autoplay policy: most browsers refuse audio before the first gesture, so the
  // opening is written immediately and spoken on the first touch if the speech
  // attempt was blocked. Text first is the accessible order anyway.


  // Recovery, not the happy path. The kiosk arms itself on load; this only exists
  // because a browser that has never been granted the microphone, or that is
  // enforcing its autoplay policy, will refuse both on a cold first visit. On the
  // lobby terminal that happens once, ever — see runcommand.txt §23 for the launch
  // flags that remove it entirely.
  const recoverOnFirstTouch = () => {
    startHandsFree();
    if (!greeted || spokenGreeting) return;
    spokenGreeting = true;
    // Already on screen from the blocked attempt, so this reads them out without
    // adding the bubbles a second time.
    speakOpening({ addBubbles: false });
  };
  ['pointerdown', 'keydown'].forEach((event) =>
    window.addEventListener(event, recoverOnFirstTouch, { once: true })
  );

  // Greet, then open the microphone and leave it open. No tap anywhere in here.
  openConversation().then(startHandsFree);

  // Guest walked away, or reset was pressed: clear the screen so the next person
  // does not inherit someone else's conversation. A privacy control, not tidiness.
  // --- Voice, offered to the rest of the page --------------------------------
  //
  // One recogniser owns the microphone. Anything else on the page that wants to
  // speak or to hear an answer asks through here rather than starting a second
  // one — two recognisers fighting for the same device is how a working
  // microphone becomes an unreliable one.
  //
  // Used by kiosk-enrol.js so the photo-consent question is read aloud and can be
  // answered out loud, in whichever language the conversation is in.
  window.ashosVoice = {
    /** The language the conversation is currently in, as a bare code. */
    language: () => speechLang.split('-')[0],

    /** Read something out. Resolves when it has finished, or immediately if it
     *  cannot be spoken at all. */
    say: (text, language) => speakIn(language || speechLang, text),

    /** Stop the hands-free loop, for a screen that owns the microphone. */
    suspend: () => {
      suspended = true;
      standDown('standby');
    },

    /** Hand the microphone back to the conversation. */
    resume: () => {
      suspended = false;
      startHandsFree();
    },

    /** Ask a yes/no question out loud and wait for the answer.
     *
     * Resolves true, false, or null when nothing usable was heard — null matters:
     * silence is not consent, and a caller must be able to tell "they said no"
     * from "I did not hear them".
     */
    askYesNo: async ({ text, language, yes = [], no = [], attempts = 2 }) => {
      if (text) await speakIn(language || speechLang, text);
      if (!browserStt) return null;

      const match = (heard, words) =>
        words.some((word) => word && heard.includes(String(word).toLowerCase()));

      for (let i = 0; i < attempts; i += 1) {
        const heard = (await listenOnce(language || speechLang)).toLowerCase().trim();
        if (!heard) continue;
        // No before yes: "no thanks" contains neither ambiguity nor a yes, but
        // "yes, no problem" would match both and must not be read as agreement.
        if (match(heard, no)) return false;
        if (match(heard, yes)) return true;
      }
      return null;
    },
  };

  /** One utterance, returned as text. Used only while the loop is suspended. */
  const listenOnce = (language) =>
    new Promise((resolve) => {
      if (!browserStt) {
        resolve('');
        return;
      }

      const once = new SpeechRecognitionApi();
      once.lang = BCP47[String(language || 'en').split('-')[0]] || language || 'en-US';
      once.interimResults = false;
      once.continuous = false;
      once.maxAlternatives = 1;

      let answered = '';
      let settled = false;
      const done = () => {
        if (settled) return;
        settled = true;
        el.mic.classList.remove('is-hearing');
        resolve(answered);
      };

      once.onresult = (event) => {
        answered = Array.from(event.results)
          .map((r) => r[0].transcript)
          .join(' ');
      };
      once.onspeechstart = () => el.mic.classList.add('is-hearing');
      once.onerror = done;
      once.onend = done;

      // The speaker must not be talking into it, same as the main loop.
      stopSpeaking();
      try {
        once.start();
        el.mic.classList.add('is-armed');
      } catch (error) {
        done();
        return;
      }

      // Chrome usually ends the session itself; this is the backstop for when it
      // does not, so a consent screen can never wait forever.
      window.setTimeout(() => {
        try {
          once.abort();
        } catch (error) {
          /* already finished */
        }
        done();
      }, 9000);
    });

  root.addEventListener('ashos:guest-left', () => {
    standDown('tap');
    conversationId = null;
    el.bubbles.innerHTML = '';
    renderBooking(null);
    if (el.turnCount) el.turnCount.textContent = '0';
    stopSpeaking();
    setState(aiEnabled ? 'idle' : 'offline');
  });
})();
