// ── MBot Cookie Bridge — Popup Script ──────────────────────────────────
// Reads YouTube cookies from the browser and sends them to the MBot
// Radio DJ bot with one click. Keeps the music playing 24/7.

const DEFAULT_URL = "http://localhost:8080";
const YT_DOMAINS = [".youtube.com", "youtube.com", "www.youtube.com"];

const sendBtn = document.getElementById("send-btn");
const refreshBtn = document.getElementById("refresh-btn");
const statusDot = document.getElementById("status-dot");
const statusLabel = document.getElementById("status-label");
const statusDetail = document.getElementById("status-detail");
const botUrlInput = document.getElementById("bot-url");
const autoRefreshToggle = document.getElementById("auto-refresh");
const resultDiv = document.getElementById("result");

// ── Storage ────────────────────────────────────────────────────────

async function getStored(key, defaultVal) {
  const result = await chrome.storage.local.get(key);
  return result[key] !== undefined ? result[key] : defaultVal;
}

async function setStored(key, value) {
  return chrome.storage.local.set({ [key]: value });
}

// ── Init ────────────────────────────────────────────────────────────

async function init() {
  const savedUrl = await getStored("botUrl", DEFAULT_URL);
  botUrlInput.value = savedUrl;
  autoRefreshToggle.checked = await getStored("autoRefresh", false);

  botUrlInput.addEventListener("change", async () => {
    await setStored("botUrl", botUrlInput.value.trim() || DEFAULT_URL);
    checkHealth();
  });

  autoRefreshToggle.addEventListener("change", async () => {
    await setStored("autoRefresh", autoRefreshToggle.checked);
    // Tell background script to start/stop auto-refresh alarm
    chrome.runtime.sendMessage({ action: "updateAutoRefresh" });
  });

  await checkHealth();
}

// ── Health Check ────────────────────────────────────────────────────

async function checkHealth() {
  const url = botUrlInput.value.trim() || DEFAULT_URL;
  setStatus("gray", "Checking...", "Connecting to bot");

  try {
    const res = await fetch(`${url}/api/ytcookies/health`, {
      method: "GET",
      headers: { "Accept": "application/json" },
    });
    const data = await res.json();

    if (data.needs_injection) {
      setStatus("red", "Cookies needed", "YouTube is blocked — send cookies now");
      sendBtn.disabled = false;
      sendBtn.textContent = "🍪 Send YouTube Cookies";
    } else if (data.cookie_age_hours && data.cookie_age_hours > 168) {
      setStatus("yellow", "Cookies stale", `Cookie file is ${Math.round(data.cookie_age_hours / 24)}d old — refresh recommended`);
      sendBtn.disabled = false;
      sendBtn.textContent = "🔄 Refresh Cookies";
    } else {
      setStatus("green", "Cookies OK", `Using ${data.cookie_source} — YouTube working`);
      sendBtn.disabled = false;
      sendBtn.textContent = "🍪 Send Fresh Cookies";
    }
  } catch (e) {
    setStatus("gray", "Bot not reachable", `Can't connect to ${url}`);
    sendBtn.disabled = true;
    showResult("error", `Connection failed: ${e.message}`);
  }
}

function setStatus(color, label, detail) {
  statusDot.className = `dot ${color}`;
  statusLabel.textContent = label;
  statusDetail.textContent = detail;
}

// ── Send Cookies ──────────────────────────────────────────────────

async function sendCookies() {
  const url = botUrlInput.value.trim() || DEFAULT_URL;
  sendBtn.disabled = true;
  sendBtn.textContent = "⏳ Sending...";
  showResult("", "");

  try {
    // Get all YouTube cookies from the browser
    const ytCookies = [];
    for (const domain of YT_DOMAINS) {
      try {
        const cookies = await chrome.cookies.getAll({ domain });
        ytCookies.push(...cookies);
      } catch (e) {
        console.warn(`Could not get cookies for ${domain}:`, e);
      }
    }

    // Also try with url-based query
    try {
      const urlCookies = await chrome.cookies.getAll({ url: "https://www.youtube.com" });
      // Deduplicate by name+domain
      const seen = new Set(ytCookies.map(c => `${c.domain}:${c.name}`));
      for (const c of urlCookies) {
        if (!seen.has(`${c.domain}:${c.name}`)) {
          ytCookies.push(c);
        }
      }
    } catch (e) {
      console.warn("URL cookie query failed:", e);
    }

    if (ytCookies.length === 0) {
      showResult("error", "No YouTube cookies found. Make sure you're logged into YouTube in this browser.");
      sendBtn.disabled = false;
      sendBtn.textContent = "🍪 Send YouTube Cookies";
      return;
    }

    console.log(`Found ${ytCookies.length} YouTube cookies`);

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
        source: "extension",
      }),
    });

    const data = await res.json();
    if (data.ok) {
      const count = ytCookies.length;
      const hasWarning = data.warning ? `\n⚠️ ${data.warning}` : "";
      showResult("success", `✅ Sent ${count} cookies! YouTube playback resuming.${hasWarning}`);
      setStatus("green", "Cookies sent!", `Sent ${count} cookies — YouTube should work now`);
      sendBtn.textContent = "🍪 Send Fresh Cookies";

      // Store last success timestamp
      await setStored("lastSend", Date.now());
    } else {
      showResult("error", `❌ ${data.error || "Failed to send cookies"}`);
      setStatus("red", "Send failed", data.error || "Unknown error");
    }
  } catch (e) {
    showResult("error", `❌ Network error: ${e.message}`);
    setStatus("gray", "Error", e.message);
  }

  sendBtn.disabled = false;
}

function showResult(type, message) {
  resultDiv.className = `result ${type}`;
  resultDiv.textContent = message;
}

// ── Event Listeners ──────────────────────────────────────────────

sendBtn.addEventListener("click", sendCookies);
refreshBtn.addEventListener("click", checkHealth);

// Start
init();