/* ASHOS shell behaviour.
 *
 * Vanilla ES6 by decision (goal.txt §2.1) — no framework, no build step. The
 * staff UI is server-rendered; JavaScript only handles chrome and live probes.
 */

(() => {
  'use strict';

  // --- Theme -----------------------------------------------------------------
  // Stored in a cookie rather than localStorage so the server renders the right
  // theme on first paint. localStorage would flash the wrong one.
  const THEME_COOKIE = 'ashos_theme';

  const setTheme = (theme) => {
    document.documentElement.setAttribute('data-theme', theme);
    document.cookie = `${THEME_COOKIE}=${theme};path=/;max-age=31536000;samesite=lax`;
  };

  document.getElementById('theme-toggle')?.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    setTheme(current === 'dark' ? 'light' : 'dark');
  });

  // --- Mobile sidebar --------------------------------------------------------
  document.getElementById('sidebar-toggle')?.addEventListener('click', () => {
    document.getElementById('ashos-sidebar')?.classList.toggle('is-open');
  });

  // --- Platform health panel -------------------------------------------------
  const panel = document.getElementById('health-panel');
  if (panel) {
    const paint = (checks) => {
      panel.querySelectorAll('[data-check]').forEach((el) => {
        const key = el.dataset.check;
        const value = checks[key] ?? 'unknown';
        const label = el.textContent.split(':')[0];
        const healthy = value === 'ok' || /^\d/.test(String(value));
        el.textContent = `${label}: ${value}`;
        el.classList.add(healthy ? 'pill--ok' : 'pill--danger');
      });
    };

    fetch(panel.dataset.endpoint, { headers: { Accept: 'application/json' } })
      .then((r) => r.json())
      .then((data) => paint(data.checks || {}))
      .catch(() => paint({ database: 'unreachable', cache: 'unreachable', pgvector: 'unreachable' }));
  }

  // --- AI assistant placeholder ----------------------------------------------
  // The real concierge panel arrives in P2. Until then the button says so
  // instead of opening an empty drawer.
  document.getElementById('ai-assistant-fab')?.addEventListener('click', (event) => {
    if (event.currentTarget.disabled) return;
    window.alert('AI Concierge panel arrives in Phase 2.');
  });
})();
