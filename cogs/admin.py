import discord
from discord.ext import commands
import config
import re
import aiohttp
from datetime import datetime
import logging
import os

from utils import cookie_parser


def _reload_ytdl_cookies():
    """
    Reload yt-dlp cookie settings from config into the shared option dicts.
    Called after any cookie change to keep all yt-dlp instances consistent.
    """
    cookiefile = config.YTDDL_COOKIEFILE.strip()
    browser = config.YTDDL_COOKIES_FROM_BROWSER.strip()

    from cogs import youtube

    # Remove any stale cookie settings
    for key in ("cookiefile", "cookiesfrombrowser"):
        youtube.YTDL_FORMAT_OPTIONS.pop(key, None)
        youtube.YTDL_PLAYLIST_FLAT_OPTIONS.pop(key, None)

    # Apply new settings using the same priority as _build_cookie_opts()
    if browser:
        parts = browser.split(":", 1)
        browser_name = parts[0].strip().lower()
        profile = parts[1].strip() if len(parts) > 1 else None
        try:
            if profile:
                youtube.YTDL_FORMAT_OPTIONS["cookiesfrombrowser"] = (
                    browser_name,
                    profile,
                )
                youtube.YTDL_PLAYLIST_FLAT_OPTIONS["cookiesfrombrowser"] = (
                    browser_name,
                    profile,
                )
            else:
                youtube.YTDL_FORMAT_OPTIONS["cookiesfrombrowser"] = (browser_name,)
                youtube.YTDL_PLAYLIST_FLAT_OPTIONS["cookiesfrombrowser"] = (
                    browser_name,
                )
            logging.info(
                f"yt-dlp cookies reloaded: using browser '{browser_name}'"
                + (f" profile '{profile}'" if profile else "")
            )
        except Exception as e:
            logging.error(f"Failed to set cookies_from_browser: {e}")
    elif cookiefile and os.path.exists(cookiefile):
        youtube.YTDL_FORMAT_OPTIONS["cookiefile"] = cookiefile
        youtube.YTDL_PLAYLIST_FLAT_OPTIONS["cookiefile"] = cookiefile
        logging.info(f"yt-dlp cookies reloaded: using cookie file '{cookiefile}'")
    else:
        logging.warning("yt-dlp cookies reloaded: no valid cookie source found")


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="fetch_and_set_cookies")
    @commands.is_owner()
    async def fetch_and_set_cookies(self, ctx, url: str):
        """Fetches cookies from a given URL and saves them to youtube_cookie.txt for yt-dlp.
        Usage: ?fetch_and_set_cookies <URL>
        """
        if not url.startswith("https://"):
            return await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} URL must be HTTPS.",
                    discord.Color.red(),
                )
            )
        logging.info(
            f"fetch_and_set_cookies command invoked by {ctx.author} for URL: {url}"
        )
        await ctx.send(
            embed=self.create_embed(
                "Fetching Cookies", f"Attempting to fetch cookies from `{url}`..."
            )
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    logging.info(
                        f"HTTP GET request to {url} returned status: {response.status}"
                    )
                    if response.status != 200:
                        logging.error(
                            f"Failed to fetch URL {url}. Status: {response.status}"
                        )
                        return await ctx.send(
                            embed=self.create_embed(
                                "Error",
                                f"{config.ERROR_EMOJI} Failed to fetch URL. Status: {response.status}",
                                discord.Color.red(),
                            )
                        )

                    set_cookie_headers = response.headers.getall("Set-Cookie", [])
                    logging.info(
                        f"Found {len(set_cookie_headers)} 'Set-Cookie' headers."
                    )

                    if not set_cookie_headers:
                        logging.warning(
                            f"No 'Set-Cookie' headers found in response from {url}"
                        )
                        return await ctx.send(
                            embed=self.create_embed(
                                "No Cookies",
                                f"{config.ERROR_EMOJI} No 'Set-Cookie' headers found in the response from `{url}`.",
                                discord.Color.orange(),
                            )
                        )

                    cookie_lines = []
                    for header in set_cookie_headers:
                        parsed_cookies = cookie_parser.parse_all_cookies(header)
                        logging.debug(f"Parsed cookies from header: {parsed_cookies}")
                        for name, value in parsed_cookies.items():
                            domain_match = re.search(
                                r"Domain=([^;]+)", header, re.IGNORECASE
                            )
                            domain = domain_match.group(1) if domain_match else ""

                            path_match = re.search(
                                r"Path=([^;]+)", header, re.IGNORECASE
                            )
                            path = path_match.group(1) if path_match else "/"

                            secure = "TRUE" if "Secure" in header else "FALSE"

                            expires_match = re.search(
                                r"Expires=([^;]+)", header, re.IGNORECASE
                            )
                            expiration_timestamp = "0"
                            if expires_match:
                                try:
                                    expires_str = expires_match.group(1).strip()
                                    dt_object = datetime.strptime(
                                        expires_str, "%a, %d %b %Y %H:%M:%S %Z"
                                    )
                                    expiration_timestamp = str(
                                        int(dt_object.timestamp())
                                    )
                                except ValueError:
                                    logging.warning(
                                        f"Could not parse expiration date '{expires_str}' for cookie {name}"
                                    )
                                    pass

                            flag = "TRUE" if domain.startswith(".") else "FALSE"

                            cookie_line = f"{domain}\t{flag}\t{path}\t{secure}\t{expiration_timestamp}\t{name}\t{value}"
                            cookie_lines.append(cookie_line)

                    if not cookie_lines:
                        logging.warning(
                            f"No parsable cookies found in response from {url}"
                        )
                        return await ctx.send(
                            embed=self.create_embed(
                                "No Parsable Cookies",
                                f"{config.ERROR_EMOJI} No parsable cookies found in the response from `{url}`.",
                                discord.Color.orange(),
                            )
                        )

                    cookiefile = config.YTDDL_COOKIEFILE or "youtube_cookie.txt"
                    with open(cookiefile, "w") as f:
                        f.write("# Netscape HTTP Cookie File\n")
                        f.write("\n".join(cookie_lines))
                    logging.info(
                        f"Successfully wrote {len(cookie_lines)} cookie lines to {cookiefile}"
                    )

                    # Clear any browser cookie setting so cookiefile takes priority
                    config.YTDDL_COOKIES_FROM_BROWSER = ""
                    _reload_ytdl_cookies()

                    await ctx.send(
                        embed=self.create_embed(
                            "Cookies Set",
                            f"{config.SUCCESS_EMOJI} Successfully fetched and set cookies from `{url}` to `{cookiefile}`.",
                        )
                    )

        except aiohttp.ClientError as e:
            logging.error(f"Network error fetching cookies from {url}: {e}")
            await ctx.send(
                embed=self.create_embed(
                    "Network Error",
                    f"{config.ERROR_EMOJI} A network error occurred: {e}",
                    discord.Color.red(),
                )
            )
        except Exception as e:
            logging.error(
                f"An unexpected error occurred in fetch_and_set_cookies for {url}: {e}",
                exc_info=True,
            )
            await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} An unexpected error occurred: {e}",
                    discord.Color.red(),
                )
            )

    @commands.command(name="ytcookies")
    @commands.is_owner()
    async def ytcookies(self, ctx, source: str = ""):
        """
        Set or check yt-dlp cookie authentication for YouTube.

        Usage:
          ?ytcookies                    — Show current cookie status
          ?ytcookies browser:chrome     — Use cookies from Chrome browser
          ?ytcookies browser:firefox    — Use cookies from Firefox browser
          ?ytcookies browser:brave      — Use cookies from Brave browser
          ?ytcookies file               — Use youtube_cookie.txt file
          ?ytcookies clear              — Clear cookie settings (no auth)

        For browser cookies to work, you must have logged into YouTube in
        that browser at least once. On Linux headless servers, Firefox is
        usually easiest (Chrome needs keyring/secretstorage).

        You can also export cookies manually using a browser extension
        like "Get cookies.txt LOCALLY" and save as youtube_cookie.txt
        in the bot directory.
        """
        source = source.strip().lower()

        if not source:
            # Show current status
            browser = config.YTDDL_COOKIES_FROM_BROWSER.strip()
            cookiefile = config.YTDDL_COOKIEFILE.strip()
            file_exists = os.path.exists(cookiefile) if cookiefile else False

            lines = ["**yt-dlp Cookie Status:**"]
            if browser:
                lines.append(f"🍪 Browser: `{browser}`")
            if cookiefile:
                status = "✅ exists" if file_exists else "❌ not found"
                lines.append(f"🍪 Cookie file: `{cookiefile}` ({status})")
            if not browser and not (cookiefile and file_exists):
                lines.append("⚠️ No cookies configured — YouTube may block requests")
                lines.append("Use `?ytcookies browser:chrome` or `?ytcookies file`")

            return await ctx.send(
                embed=self.create_embed(
                    "Cookie Status", "\n".join(lines), discord.Color.blue()
                )
            )

        if source.startswith("browser:"):
            browser_name = source.split(":", 1)[1].strip()
            if not browser_name:
                return await ctx.send(
                    embed=self.create_embed(
                        "Error",
                        f"{config.ERROR_EMOJI} Specify a browser name, e.g. `?ytcookies browser:chrome`",
                        discord.Color.red(),
                    )
                )

            # Validate by trying to detect the browser
            valid_browsers = [
                "chrome",
                "firefox",
                "brave",
                "edge",
                "opera",
                "vivaldi",
                "chromium",
            ]
            if browser_name not in valid_browsers:
                return await ctx.send(
                    embed=self.create_embed(
                        "Unknown Browser",
                        f"{config.ERROR_EMOJI} Unknown browser `{browser_name}`. "
                        f"Supported: {', '.join(valid_browsers)}",
                        discord.Color.red(),
                    )
                )

            config.YTDDL_COOKIES_FROM_BROWSER = browser_name
            _reload_ytdl_cookies()
            logging.info(
                f"yt-dlp cookies set to browser: {browser_name} (by {ctx.author})"
            )
            return await ctx.send(
                embed=self.create_embed(
                    "Browser Cookies Set",
                    f"{config.SUCCESS_EMOJI} yt-dlp will now use cookies from **{browser_name}**.\n"
                    f"Make sure you've logged into YouTube in {browser_name} at least once.\n"
                    f"This takes effect immediately — no restart needed.",
                    discord.Color.green(),
                )
            )

        elif source == "file":
            cookiefile = config.YTDDL_COOKIEFILE or "youtube_cookie.txt"
            if not os.path.exists(cookiefile):
                return await ctx.send(
                    embed=self.create_embed(
                        "No Cookie File",
                        f"{config.ERROR_EMOJI} Cookie file `{cookiefile}` not found.\n"
                        "Export YouTube cookies from your browser using an extension like "
                        '"Get cookies.txt LOCALLY" and save as `youtube_cookie.txt` '
                        "in the bot directory.",
                        discord.Color.orange(),
                    )
                )
            config.YTDDL_COOKIES_FROM_BROWSER = ""
            _reload_ytdl_cookies()
            logging.info(f"yt-dlp cookies set to file: {cookiefile} (by {ctx.author})")
            return await ctx.send(
                embed=self.create_embed(
                    "Cookie File Active",
                    f"{config.SUCCESS_EMOJI} yt-dlp will now use cookies from `{cookiefile}`.",
                    discord.Color.green(),
                )
            )

        elif source == "clear":
            config.YTDDL_COOKIES_FROM_BROWSER = ""
            from cogs import youtube

            for key in ("cookiefile", "cookiesfrombrowser"):
                youtube.YTDL_FORMAT_OPTIONS.pop(key, None)
                youtube.YTDL_PLAYLIST_FLAT_OPTIONS.pop(key, None)
            logging.info(f"yt-dlp cookies cleared (by {ctx.author})")
            return await ctx.send(
                embed=self.create_embed(
                    "Cookies Cleared",
                    f"{config.SUCCESS_EMOJI} yt-dlp cookie settings removed. "
                    "YouTube may block requests without cookies.",
                    discord.Color.green(),
                )
            )

        else:
            return await ctx.send(
                embed=self.create_embed(
                    "Unknown Option",
                    f"{config.ERROR_EMOJI} Unknown option `{source}`. Use:\n"
                    "• `?ytcookies` — show status\n"
                    "• `?ytcookies browser:chrome` — use Chrome cookies\n"
                    "• `?ytcookies file` — use youtube_cookie.txt\n"
                    "• `?ytcookies clear` — remove cookies",
                    discord.Color.orange(),
                )
            )

    @commands.command(name="shutdown")
    @commands.is_owner()
    async def shutdown(self, ctx):
        """Shuts down the bot completely."""
        logging.info(f"Shutdown command invoked by {ctx.author}")
        await ctx.send(
            embed=self.create_embed(
                "Shutting Down", f"{config.SUCCESS_EMOJI} The bot is now shutting down."
            )
        )
        await self.bot.close()
        logging.info("Bot has been shut down.")

    @commands.command(name="restart")
    @commands.is_owner()
    async def restart(self, ctx):
        """Restarts the bot."""
        logging.info(f"Restart command invoked by {ctx.author}")
        await ctx.send(
            embed=self.create_embed(
                "Restarting", f"{config.SUCCESS_EMOJI} The bot is restarting..."
            )
        )
        await self.bot.close()
        logging.info("Bot is attempting to restart.")

    def create_embed(self, title, description, color=discord.Color.blurple()):
        return discord.Embed(title=title, description=description, color=color)


async def setup(bot):
    await bot.add_cog(Admin(bot))
