# 🍪 MBot Cookie Bridge — Browser Extension

One-click YouTube cookie bridge for your MBot Radio DJ bot. Keeps the music playing 24/7 by sending fresh YouTube authentication cookies from your browser to the bot.

## Why?

YouTube increasingly blocks automated playback with "Sign in to confirm you're not a bot" errors. The bot needs cookies from a logged-in browser to play music. This extension automates the process — click one button and your YouTube cookies are sent directly to the bot.

## Install

### Chrome / Edge / Brave

1. Open `chrome://extensions/` (or `edge://extensions/`)
2. Enable **Developer mode** (top right)
3. Click **Load unpacked**
4. Select the `extension/` folder
5. The 📻 MBot icon appears in your toolbar

### Firefox

1. Open `about:debugging#/runtime/this-firefox`
2. Click **Load Temporary Add-on**
3. Select any file in the `extension/` folder
4. The icon appears in your toolbar

## Setup

1. Click the MBot icon in your toolbar
2. Enter your **Bot URL** (e.g. `http://192.168.1.100:8080`)
3. Make sure you're **logged into YouTube** in this browser
4. Click **🍪 Send YouTube Cookies**
5. Done! The bot now has your cookies and can play YouTube music

## Auto-Refresh

Toggle on **Auto-refresh cookies every 6 hours** and the extension will:
- Check the bot's cookie health every 6 hours
- If cookies are stale or missing, automatically send fresh ones
- Works even when the popup is closed (background service worker)

## How It Works

```
Your Browser (logged into YouTube)
  └─ Extension reads YouTube cookies via chrome.cookies API
      └─ Sends to bot's /api/ytcookies/inject endpoint
          └─ Bot writes youtube_cookie.txt
              └─ yt-dlp uses cookies for YouTube extraction
                  └─ Music plays! 🎵
```

## Security

- Cookies are only sent to the bot URL you configure
- No cookies are stored by the extension (only the bot URL and auto-refresh setting)
- The bot's cookie file (`youtube_cookie.txt`) contains your YouTube session cookies — protect it like a password
- Only YouTube domain cookies are extracted (`.youtube.com`, `youtube.com`, `www.youtube.com`)

## Permissions

| Permission | Why |
|-----------|-----|
| `cookies` | Read YouTube cookies from your browser |
| `storage` | Save your bot URL and auto-refresh setting |
| `alarms` | Schedule periodic auto-refresh checks |
| `host_permissions` | Send cookies to your bot, read youtube.com cookies |