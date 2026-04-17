// ── MBot Cookie Bridge — Background Service Worker ──────────────────
// Handles auto-refresh: periodically checks cookie health and sends
// fresh YouTube cookies if they're stale or missing.

const ALARM_NAME = "mbot-cookie-refresh";
const CHECK_INTERVAL_MINUTES = 360; // 6 hours

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "updateAutoRefresh") {
    updateAlarm();
  }
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === ALARM_NAME) {
    await autoRefreshCookies();
  }
});

// ── Auto-refresh logic ─────────────────────────────────────────────

async function getStored(key, defaultVal) {
  const result = await chrome.storage.local.get(key);
  return result[key] !== undefined ? result[key] : defaultVal;
}

async function setStored(key, value) {
  return chrome.storage.local.set({ [key]: value });
}

async function updateAlarm() {
  const autoRefresh = await getStored("autoRefresh", false);
  
  // Clear existing alarm
  await chrome.alarms.clear(ALARM_NAME);
  
  if (autoRefresh) {
    // Set new alarm
    chrome.alarms.create(ALARM_NAME, {
      delayInMinutes: CHECK_INTERVAL_MINUTES,
      periodInMinutes: CHECK_INTERVAL_MINUTES,
    });
    console.log("MBot Cookie Bridge: Auto-refresh enabled (every 6h)");
  } else {
    console.log("MBot Cookie Bridge: Auto-refresh disabled");
  }
}

async function autoRefreshCookies() {
  const url = await getStored("botUrl", "http://localhost:8080");
  
  try {
    // Check health first
    const healthRes = await fetch(`${url}/api/ytcookies/health`);
    const health = await healthRes.json();
    
    if (!health.needs_injection) {
      console.log("MBot Cookie Bridge: Cookies still fresh, skipping refresh");
      return;
    }

    console.log("MBot Cookie Bridge: Cookies need refresh, sending...");
    
    // Get YouTube cookies
    const ytCookies = [];
    const domains = [".youtube.com", "youtube.com", "www.youtube.com"];
    for (const domain of domains) {
      try {
        const cookies = await chrome.cookies.getAll({ domain });
        ytCookies.push(...cookies);
      } catch (e) {}
    }
    // Also try URL-based
    try {
      const urlCookies = await chrome.cookies.getAll({ url: "https://www.youtube.com" });
      const seen = new Set(ytCookies.map(c => `${c.domain}:${c.name}`));
      for (const c of urlCookies) {
        if (!seen.has(`${c.domain}:${c.name}`)) ytCookies.push(c);
      }
    } catch (e) {}

    if (ytCookies.length === 0) {
      console.warn("MBot Cookie Bridge: No YouTube cookies found for auto-refresh");
      return;
    }

    // Send to bot
    const res = await fetch(`${url}/api/ytcookies/inject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
        source: "extension-auto",
      }),
    });

    const data = await res.json();
    if (data.ok) {
      console.log(`MBot Cookie Bridge: Auto-refreshed ${ytCookies.length} cookies`);
      await setStored("lastSend", Date.now());
    } else {
      console.warn("MBot Cookie Bridge: Auto-refresh failed:", data.error);
    }
  } catch (e) {
    console.warn("MBot Cookie Bridge: Auto-refresh error:", e.message);
  }
}

// Initialize alarm on install
chrome.runtime.onInstalled.addListener(() => {
  updateAlarm();
});

// Also restore alarm on browser start
chrome.runtime.onStartup.addListener(() => {
  updateAlarm();
});