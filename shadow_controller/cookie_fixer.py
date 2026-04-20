"""
Cookie Fixer — Loop 1: autonomous cookie health check & refresh.

Monitors the DJ bot's YouTube cookie health via Mission Control API.
When cookies are stale or auth is blocked:
  1. Reads fresh cookies from the Firefox cookie.txt plugin export
  2. Falls back to Playwright browser context extraction
  3. Injects via Mission Control /api/ytcookies/inject endpoint
  4. Verifies auth block is cleared
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("shadow.cookie_fixer")


class CookieFixer:
    """
    Monitors and automatically refreshes YouTube cookies for the DJ bot.

    Cookie freshness is critical — yt-dlp uses them to bypass
    YouTube's bot detection and access age-restricted content.
    Without valid cookies, the bot can't play anything.
    """

    def __init__(self, config: dict, api_client, browser_manager, alert_system):
        """
        Args:
            config: Parsed config.yaml with:
                - cookie_check_interval (seconds, default 300)
                - cookie_max_age_days (int, default 5)
                - cookie_txt_path (optional)
        """
        self.api = api_client
        self.browser = browser_manager
        self.alerts = alert_system

        self.interval = config.get("cookie_check_interval", 300)
        self.max_age_days = config.get("cookie_max_age_days", 5)
        self._running = False
        self._last_refresh_time = None
        self._consecutive_failures = 0

    async def start(self):
        """Start the cookie monitoring loop."""
        self._running = True
        logger.info(
            "Cookie fixer started (interval: %ds, max age: %d days)",
            self.interval,
            self.max_age_days,
        )

        while self._running:
            try:
                await self._check_and_fix()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Cookie fixer loop error: %s", e, exc_info=True)
                self._consecutive_failures += 1
                if self._consecutive_failures > 5:
                    await self.alerts.error(
                        "Cookie fixer has failed 5 times in a row — may need manual intervention",
                        key="cookie_fixer_broken",
                    )

            await asyncio.sleep(self.interval)

    def stop(self):
        """Stop the cookie monitoring loop."""
        self._running = False
        logger.info("Cookie fixer stopped")

    async def _check_and_fix(self):
        """Main check: are cookies healthy? If not, refresh them."""
        logger.debug("Checking cookie health...")

        # Step 1: Check health from Mission Control API
        health = await self.api.cookie_health()
        auth = await self.api.cookie_auth_status()

        needs_refresh = False

        # Check 1: Auth blocked (bot can't play YouTube)
        if auth.get("auth_blocked"):
            needs_refresh = True
            logger.warning("YouTube auth is BLOCKED — cookies need refresh")

        # Check 2: Cookie file is stale
        cookie_age_days = health.get("cookie_age_days", 0)
        if cookie_age_days > self.max_age_days:
            needs_refresh = True
            logger.warning("Cookies are stale: %.1f days old", cookie_age_days)

        # Check 3: Health endpoint says injection needed
        if health.get("needs_injection"):
            needs_refresh = True

        # Check 4: No cookie file at all
        if health.get("cookie_source") == "none" or health.get("error"):
            needs_refresh = True

        if not needs_refresh:
            logger.debug("Cookies healthy (age: %.1f days)", cookie_age_days)
            self._consecutive_failures = 0
            return

        # Step 2: Alert that we're refreshing
        await self.alerts.cookie_expired()

        # Step 3: Get fresh cookies
        cookie_text = await self._get_fresh_cookies()
        if not cookie_text:
            await self.alerts.cookie_refresh_failed(
                "could not extract cookies from browser"
            )
            self._consecutive_failures += 1
            return

        # Step 4: Inject via Mission Control API
        result = await self.api.cookie_inject(cookie_text)

        if result.get("error"):
            await self.alerts.cookie_refresh_failed(str(result["error"]))
            self._consecutive_failures += 1
            return

        logger.info(
            "Cookies injected: %s", result.get("message", result.get("status", "ok"))
        )

        # Step 5: Verify auth block is cleared
        await asyncio.sleep(3)  # Give the bot time to reload
        auth_verify = await self.api.cookie_auth_status()

        if auth_verify.get("auth_blocked"):
            await self.alerts.cookie_refresh_failed(
                "auth still blocked after injection"
            )
            self._consecutive_failures += 1
        else:
            await self.alerts.cookie_refreshed()
            self._consecutive_failures = 0
            self._last_refresh_time = asyncio.get_event_loop().time()
            logger.info("Cookie refresh successful")

    async def _get_fresh_cookies(self) -> Optional[str]:
        """
        Get fresh cookies using two methods:
          1. Read the cookie.txt file exported by the Firefox plugin
          2. Fall back to Playwright browser context extraction
        """
        # Method 1: Cookie.txt plugin file
        # The Firefox "cookie.txt" extension exports a Netscape-format file
        # when the user clicks the export button. We watch for it.
        cookie_text = await self.browser.read_cookie_txt()
        if cookie_text and len(cookie_text.strip()) > 50:
            # Validate it contains YouTube auth cookies
            essential_cookies = ["SID", "HSID", "SSID", "APISID", "SAPISID"]
            found = sum(1 for c in essential_cookies if c in cookie_text)
            if found >= 2:
                logger.info(
                    "Got fresh cookies from cookie.txt plugin (%d/%d essential found)",
                    found,
                    len(essential_cookies),
                )
                return cookie_text
            else:
                logger.warning(
                    "cookie.txt found but missing essential YouTube cookies (%d/%d)",
                    found,
                    len(essential_cookies),
                )

        # Method 2: Playwright browser context extraction
        logger.info("Falling back to Playwright cookie extraction...")
        cookie_text = await self.browser.extract_cookies_from_browser()
        if cookie_text and len(cookie_text.strip()) > 50:
            return cookie_text

        logger.error("Both cookie extraction methods failed")
        return None

    async def force_refresh(self) -> bool:
        """
        Force an immediate cookie refresh (called by other modules).
        Returns True if refresh succeeded.
        """
        logger.info("Force cookie refresh triggered")
        cookie_text = await self._get_fresh_cookies()
        if not cookie_text:
            return False

        result = await self.api.cookie_inject(cookie_text)
        if result.get("error"):
            return False

        await asyncio.sleep(3)
        auth = await self.api.cookie_auth_status()
        return not auth.get("auth_blocked", False)
