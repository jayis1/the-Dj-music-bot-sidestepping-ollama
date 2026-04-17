// ── MBot Cookie Bridge — Content Script ─────────────────────────────
// Injected into Mission Control pages. Detects when Mission Control
// needs cookies and communicates with the popup/background via
// window.postMessage.

// Listen for messages from Mission Control
window.addEventListener('message', async (event) => {
  // Only accept same-origin messages
  if (event.source !== window) return;

  // Mission Control checking if extension is present
  if (event.data.type === 'mbot-check-extension') {
    window.postMessage({
      type: 'mbot-extension-present',
      version: chrome.runtime.getManifest().version
    }, '*');
  }

  // Mission Control requesting cookie send
  if (event.data.type === 'mbot-send-cookies') {
    const botUrl = event.data.botUrl || window.location.origin;
    
    try {
      // Get YouTube cookies
      const ytCookies = [];
      const domains = ['.youtube.com', 'youtube.com', 'www.youtube.com'];
      for (const domain of domains) {
        try {
          const cookies = await chrome.cookies.getAll({ domain });
          ytCookies.push(...cookies);
        } catch (e) {}
      }
      // Also URL-based
      try {
        const urlCookies = await chrome.cookies.getAll({ url: 'https://www.youtube.com' });
        const seen = new Set(ytCookies.map(c => `${c.domain}:${c.name}`));
        for (const c of urlCookies) {
          if (!seen.has(`${c.domain}:${c.name}`)) ytCookies.push(c);
        }
      } catch (e) {}

      if (ytCookies.length === 0) {
        window.postMessage({
          type: 'mbot-cookies-sent',
          ok: false,
          error: 'No YouTube cookies found. Log into YouTube first.'
        }, '*');
        return;
      }

      // Send to bot
      const res = await fetch(`${botUrl}/api/ytcookies/inject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cookies: ytCookies.map(c => ({
            name: c.name,
            value: c.value,
            domain: c.domain,
            path: c.path,
            secure: c.secure,
            httpOnly: c.httpOnly,
            expiration: c.expirationDate || c.expirationDate || 0,
          })),
          source: 'extension',
        }),
      });

      const data = await res.json();
      window.postMessage({
        type: 'mbot-cookies-sent',
        ok: data.ok,
        error: data.error,
        warning: data.warning,
        count: ytCookies.length
      }, '*');

    } catch (e) {
      window.postMessage({
        type: 'mbot-cookies-sent',
        ok: false,
        error: e.message
      }, '*');
    }
  }
});

// Auto-announce presence when page loads
window.postMessage({
  type: 'mbot-extension-present',
  version: chrome.runtime.getManifest().version
}, '*');