"""
Browser Manager — shared Playwright instance for the shadow controller.

Uses Firefox with the user's existing profile (logged into YouTube).
The cookie.txt browser extension is already installed, so we can:
  1. Read the exported cookies.txt file directly
  2. Use Playwright to navigate YouTube and trigger the extension
  3. Keep a YouTube Live tab open for stream monitoring
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger("shadow.browser")

# Common Firefox profile paths
FIREFOX_PROFILE_CANDIDATES = [
    # Default profile locations
    os.path.expanduser("~/.mozilla/firefox"),
    os.path.expanduser("~/snap/firefox/common/.mozilla/firefox"),
    os.path.expanduser("~/.var/app/org.mozilla.firefox/.mozilla/firefox"),
]

# Default path where the cookie.txt plugin exports
COOKIE_TXT_DEFAULT_PATHS = [
    os.path.expanduser("~/Downloads/cookies.txt"),
    os.path.expanduser("~/Downloads/youtube_cookies.txt"),
    os.path.expanduser("~/cookies.txt"),
    "/tmp/cookies.txt",
]


def _find_firefox_profile() -> Optional[str]:
    """Find the default Firefox profile directory."""
    for base in FIREFOX_PROFILE_CANDIDATES:
        if not os.path.isdir(base):
            continue
        profiles_ini = os.path.join(base, "profiles.ini")
        if os.path.isfile(profiles_ini):
            # Parse profiles.ini to find Default=1 profile
            with open(profiles_ini, "r") as f:
                lines = f.readlines()
            current_section = {}
            for line in lines:
                line = line.strip()
                if line.startswith("["):
                    current_section = {}
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    current_section[k.strip()] = v.strip()
                if current_section.get("Default") == "1" and "Path" in current_section:
                    profile_path = os.path.join(base, current_section["Path"])
                    if os.path.isdir(profile_path):
                        logger.info("Found default Firefox profile: %s", profile_path)
                        return profile_path

        # Fallback: find any .default profile directory
        for entry in os.listdir(base):
            if ".default" in entry.lower() or ".default-release" in entry.lower():
                profile_path = os.path.join(base, entry)
                if os.path.isdir(profile_path):
                    logger.info("Found Firefox profile (heuristic): %s", profile_path)
                    return profile_path

    logger.warning("No Firefox profile found")
    return None


def _find_cookie_txt() -> Optional[str]:
    """Find the most recent cookies.txt file exported by the Firefox plugin."""
    best_path = None
    best_mtime = 0

    for path in COOKIE_TXT_DEFAULT_PATHS:
        if os.path.isfile(path):
            mtime = os.path.getmtime(path)
            if mtime > best_mtime:
                best_mtime = mtime
                best_path = path

    if best_path:
        logger.debug("Found cookies.txt at: %s (modified %d)", best_path, best_mtime)
    return best_path


class BrowserManager:
    """
    Manages a shared Playwright Firefox browser instance using the
    user's existing Firefox profile (with YouTube login + cookie.txt plugin).
    """

    def __init__(self, config: dict):
        """
        Args:
            config: Parsed config.yaml settings, must include:
                - firefox_profile_path (optional, auto-detected if blank)
                - cookie_txt_path (optional, auto-detected if blank)
                - youtube_live_url (optional, URL to monitor)
                - headless (bool, default False — we want GUI for the extension)
        """
        self.config = config
        self.firefox_profile = (
            config.get("firefox_profile_path") or _find_firefox_profile()
        )
        self.cookie_txt_path = config.get("cookie_txt_path") or _find_cookie_txt()
        self.youtube_live_url = config.get("youtube_live_url", "")
        self.headless = config.get("headless", False)

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._yt_page: Optional[Page] = None
        self._stream_page: Optional[Page] = None

    async def start(self):
        """Launch Firefox with the user's existing profile."""
        self._playwright = await async_playwright().start()

        if self.firefox_profile and os.path.isdir(self.firefox_profile):
            # Copy the profile to a temp dir so Playwright doesn't lock the original
            # (Firefox can't run two instances on the same profile simultaneously)
            temp_profile = os.path.join("/tmp", "shadow_controller_firefox_profile")
            if os.path.exists(temp_profile):
                shutil.rmtree(temp_profile)
            shutil.copytree(self.firefox_profile, temp_profile, symlinks=True)
            logger.info("Copied Firefox profile to %s", temp_profile)

            self._context = await self._playwright.firefox.launch_persistent_context(
                user_data_dir=temp_profile,
                headless=self.headless,
                args=["--no-remote"],
            )
            self._browser = (
                self._context.browser if hasattr(self._context, "browser") else None
            )
            logger.info("Firefox launched with existing profile")
        else:
            # No profile found — launch fresh Firefox
            self._browser = await self._playwright.firefox.launch(
                headless=self.headless,
            )
            self._context = await self._browser.new_context()
            logger.warning(
                "Launched Firefox WITHOUT existing profile — YouTube not logged in!"
            )

        # Open YouTube tab
        self._yt_page = await self._context.new_page()
        await self._yt_page.goto("https://www.youtube.com", wait_until="networkidle")
        logger.info("YouTube tab opened")

        # Open YouTube Live stream tab if URL is set
        if self.youtube_live_url:
            self._stream_page = await self._context.new_page()
            await self._stream_page.goto(
                self.youtube_live_url, wait_until="networkidle"
            )
            logger.info("YouTube Live stream tab opened")

    async def stop(self):
        """Close the browser and Playwright."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser closed")

    # ── Cookie extraction ──────────────────────────────────────────

    async def read_cookie_txt(self) -> Optional[str]:
        """
        Read the cookies.txt file exported by the Firefox cookie.txt plugin.

        The plugin writes Netscape-format cookies when the user clicks
        the extension button. We watch for this file.

        Returns:
            Netscape-format cookie text, or None if file not found.
        """
        path = self.cookie_txt_path
        if not path or not os.path.isfile(path):
            # Re-scan common paths (user may have exported since startup)
            path = _find_cookie_txt()
            if path:
                self.cookie_txt_path = path

        if not path:
            logger.warning("No cookies.txt file found")
            return None

        try:
            content = Path(path).read_text(encoding="utf-8")
            if "# Netscape" in content or content.strip().startswith("."):
                logger.info("Read cookies.txt from %s (%d bytes)", path, len(content))
                return content
            else:
                logger.warning(
                    "cookies.txt at %s doesn't look like Netscape format", path
                )
                return content  # Try it anyway — the bot's /inject endpoint handles multiple formats
        except Exception as e:
            logger.error("Failed to read cookies.txt: %s", e)
            return None

    async def extract_cookies_from_browser(self) -> Optional[str]:
        """
        Extract YouTube cookies directly from the Playwright browser context.

        This is the fallback if the cookie.txt plugin file isn't available.
        Converts Playwright cookie objects to Netscape format.
        """
        if not self._context:
            logger.error("Browser context not available")
            return None

        try:
            # Navigate to YouTube to ensure cookies are fresh
            if self._yt_page:
                await self._yt_page.goto(
                    "https://www.youtube.com", wait_until="networkidle"
                )
                await asyncio.sleep(2)

            cookies = await self._context.cookies(["https://www.youtube.com"])
            yt_cookies = [c for c in cookies if ".youtube.com" in c.get("domain", "")]

            if not yt_cookies:
                logger.warning("No YouTube cookies found in browser context")
                return None

            # Convert to Netscape format
            netscape_lines = [
                "# Netscape HTTP Cookie File",
                "# https://curl.se/docs/http-cookies.html",
                "",
            ]

            for cookie in yt_cookies:
                domain = cookie.get("domain", "")
                path = cookie.get("path", "/")
                secure = "TRUE" if cookie.get("secure", False) else "FALSE"
                expires = str(int(cookie.get("expires", -1)))
                if expires == "-1":
                    expires = "0"
                name = cookie.get("name", "")
                value = cookie.get("value", "")
                include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"

                netscape_lines.append(
                    f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expires}\t{name}\t{value}"
                )

            content = "\n".join(netscape_lines)
            logger.info(
                "Extracted %d YouTube cookies from browser context", len(yt_cookies)
            )

            # Also save to the cookie.txt path for next time
            if self.cookie_txt_path:
                Path(self.cookie_txt_path).write_text(content, encoding="utf-8")
                logger.info("Saved extracted cookies to %s", self.cookie_txt_path)

            return content

        except Exception as e:
            logger.error("Failed to extract cookies from browser: %s", e)
            return None

    async def get_fresh_cookies(self) -> Optional[str]:
        """
        Best-effort cookie extraction. Tries cookie.txt plugin file first,
        then falls back to Playwright browser context extraction.
        """
        # Method 1: Read the file exported by the cookie.txt Firefox plugin
        cookie_text = await self.read_cookie_txt()
        if cookie_text and len(cookie_text.strip()) > 50:
            return cookie_text

        # Method 2: Extract from browser context
        logger.info("cookie.txt file not available, extracting from browser context...")
        return await self.extract_cookies_from_browser()

    # ── YouTube navigation ─────────────────────────────────────────

    async def navigate_youtube(self, url: str) -> Optional[Page]:
        """Navigate to a YouTube URL in the existing browser tab."""
        if not self._yt_page:
            logger.error("YouTube page not initialized")
            return None

        try:
            await self._yt_page.goto(url, wait_until="networkidle")
            logger.info("Navigated to %s", url)
            return self._yt_page
        except Exception as e:
            logger.error("Navigation to %s failed: %s", url, e)
            return None

    async def new_youtube_tab(self, url: str) -> Optional[Page]:
        """Open a new browser tab and navigate to a URL."""
        if not self._context:
            logger.error("Browser context not available")
            return None

        try:
            page = await self._context.new_page()
            await page.goto(url, wait_until="networkidle")
            logger.info("Opened new tab: %s", url)
            return page
        except Exception as e:
            logger.error("Failed to open new tab: %s", e)
            return None

    async def get_page_content(self, url: str) -> Optional[str]:
        """Fetch the text content of a page (for parsing by Hermes)."""
        page = await self.new_youtube_tab(url)
        if not page:
            return None
        try:
            content = await page.content()
            await page.close()
            return content
        except Exception as e:
            logger.error("Failed to get page content: %s", e)
            return None

    # ── Stream monitoring ──────────────────────────────────────────

    async def open_stream_tab(self, url: str):
        """Open or switch to the YouTube Live stream monitoring tab."""
        if self._stream_page and not self._stream_page.is_closed():
            await self._stream_page.goto(url, wait_until="networkidle")
        else:
            self._stream_page = await self._context.new_page()
            await self._stream_page.goto(url, wait_until="networkidle")
        logger.info("Stream monitoring tab: %s", url)

    async def check_stream_tab_alive(self) -> bool:
        """Check if the stream monitoring tab is still active."""
        if not self._stream_page:
            return False
        try:
            if self._stream_page.is_closed():
                return False
            # Check if YouTube is showing a live stream
            title = await self._stream_page.title()
            return bool(title and "YouTube" in title)
        except Exception:
            return False

    async def get_stream_tab_screenshot(self) -> Optional[bytes]:
        """Take a screenshot of the YouTube Live tab for visual health check."""
        if not self._stream_page or self._stream_page.is_closed():
            return None
        try:
            return await self._stream_page.screenshot(type="jpeg", quality=50)
        except Exception as e:
            logger.error("Stream tab screenshot failed: %s", e)
            return None
