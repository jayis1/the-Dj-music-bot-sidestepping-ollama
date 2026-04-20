"""
Playlist Finder — Loop 4: Hermes-powered music discovery.

Uses Hermes Agent (via Ollama) to browse YouTube in the VM's
logged-in Firefox browser and discover playlists that match
the station's music vibe.

Genres: Lo-fi, Rap, Electro Swing, EDM, Chill Beats
"""

import asyncio
import logging
import random
import re
from typing import Optional

import aiohttp

logger = logging.getLogger("shadow.playlist_finder")

# Search queries per genre — rotated randomly
GENRE_QUERIES = {
    "lo-fi": [
        "lo-fi hip hop playlist 2024 long",
        "chill lo-fi beats study playlist",
        "lo-fi hip radio playlist 24/7",
        "ambient lo-fi chill beats long playlist",
        "jazz hop lo-fi playlist",
    ],
    "rap": [
        "underground rap playlist 2024",
        "chill rap playlist long",
        "lo-fi rap beats playlist",
        "conscious hip hop playlist",
        "rap mix playlist 2024 underground",
    ],
    "electro_swing": [
        "electro swing mix playlist long",
        "electro swing party playlist",
        "vintage remix electro swing playlist",
        "electro swing curated playlist",
        "caravan palace playlist electro swing",
    ],
    "edm": [
        "EDM chill playlist 2024 long",
        "chill electronic playlist mix",
        "ambient electronic beats playlist",
        "synthwave playlist long",
        "deep house chill playlist",
    ],
    "chill_beats": [
        "chill beats playlist 2024",
        "chillhop playlist long",
        "beats to relax to playlist",
        "chill vibes playlist mix",
        "mellow beats playlist study",
    ],
    "reggae": [
        "reggae playlist 2024 long",
        "roots reggae playlist classics",
        "dancehall playlist mix 2024",
        "reggae chill playlist vibes",
        "bob marley reggae playlist long",
        "lovers rock reggae playlist",
    ],
}

# Genre hints for Hermes reasoning prompts (sub-genre descriptions)
_HERMES_GENRE_HINTS = {
    "reggae": "roots reggae, dancehall, lovers rock, reggae fusion",
}


class PlaylistFinder:
    """
    Finds YouTube playlists using the Hermes agent + browser automation.

    Two discovery modes:
      1. Hermes Agent: Ask Hermes to find playlists via browser-use
      2. Direct search: Search YouTube and extract playlist URLs
    """

    def __init__(self, config: dict, api_client, browser_manager, alert_system):
        """
        Args:
            config: Parsed config.yaml with:
                - playlist_discovery_interval (seconds, default 1800)
                - genres (list of strings)
                - min_playlist_songs (int, default 30)
                - ollama_url (str, default "http://localhost:11434")
                - ollama_model (str, default "hermes3:8b")
        """
        self.api = api_client
        self.browser = browser_manager
        self.alerts = alert_system

        self.interval = config.get("playlist_discovery_interval", 1800)
        self.genres = config.get(
            "genres", ["lo-fi", "rap", "electro_swing", "edm", "chill_beats"]
        )
        self.min_songs = config.get("min_playlist_songs", 30)
        self.ollama_url = config.get("ollama_url", "http://localhost:11434")
        self.ollama_model = config.get("ollama_model", "hermes3:8b")

        self._running = False
        self._discovered_playlists: list = []  # Cache of found playlist URLs
        self._used_playlists: set = set()  # Already set as Auto-DJ source
        self._genre_index = 0
        self._hermes_available = False

    async def start(self):
        """Start the periodic playlist discovery loop."""
        self._running = True

        # Check if Hermes / Ollama is available
        await self._check_hermes()

        logger.info(
            "Playlist finder started (interval: %ds, genres: %s, hermes: %s)",
            self.interval,
            self.genres,
            self._hermes_available,
        )

        while self._running:
            try:
                await self._discover()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Playlist finder loop error: %s", e, exc_info=True)
                await self.alerts.hermes_error("playlist_discovery", str(e))

            await asyncio.sleep(self.interval)

    def stop(self):
        """Stop the playlist discovery loop."""
        self._running = False
        logger.info("Playlist finder stopped")

    async def _check_hermes(self):
        """Check if Ollama is running and the Hermes model is available."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.ollama_url}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        models = [m.get("name", "") for m in data.get("models", [])]
                        # Check for hermes model (any variant)
                        hermes_models = [m for m in models if "hermes" in m.lower()]
                        if hermes_models:
                            self.ollama_model = hermes_models[0]
                            self._hermes_available = True
                            logger.info("Hermes model found: %s", self.ollama_model)
                        else:
                            # Fall back to any available model
                            if models:
                                self.ollama_model = models[0]
                                self._hermes_available = True
                                logger.info(
                                    "No Hermes model, using fallback: %s",
                                    self.ollama_model,
                                )
                            else:
                                logger.warning("No Ollama models available")
        except Exception as e:
            logger.warning("Ollama not reachable at %s: %s", self.ollama_url, e)
            self._hermes_available = False

    async def find_playlist(self) -> Optional[str]:
        """
        Find a suitable YouTube playlist.
        Called by the queue watchdog when queue is low.

        Returns:
            YouTube playlist URL or None
        """
        # Use cached discovered playlists first
        unused = [
            p for p in self._discovered_playlists if p not in self._used_playlists
        ]
        if unused:
            url = random.choice(unused)
            self._used_playlists.add(url)
            return url

        # Force a discovery run
        await self._discover()

        unused = [
            p for p in self._discovered_playlists if p not in self._used_playlists
        ]
        if unused:
            url = random.choice(unused)
            self._used_playlists.add(url)
            return url

        return None

    async def _discover(self):
        """Run one discovery cycle to find playlists."""
        # Pick a genre to focus on (rotate through configured genres)
        genre = self.genres[self._genre_index % len(self.genres)]
        self._genre_index += 1

        logger.info("Discovering playlists for genre: %s", genre)

        # Method 1: Hermes agent (if available)
        if self._hermes_available:
            try:
                playlists = await self._discover_with_hermes(genre)
                if playlists:
                    self._discovered_playlists.extend(playlists)
                    # Deduplicate
                    self._discovered_playlists = list(set(self._discovered_playlists))
                    logger.info(
                        "Hermes found %d playlists for '%s'", len(playlists), genre
                    )
                    return
            except Exception as e:
                logger.warning("Hermes discovery failed, falling back: %s", e)

        # Method 2: Direct YouTube search via browser
        playlists = await self._discover_via_search(genre)
        if playlists:
            self._discovered_playlists.extend(playlists)
            self._discovered_playlists = list(set(self._discovered_playlists))
            logger.info("Search found %d playlists for '%s'", len(playlists), genre)

    async def _discover_with_hermes(self, genre: str) -> list:
        """
        Use Hermes (via Ollama OpenAI-compatible API) to analyze
        YouTube search results and pick good playlists.

        Hermes acts as the brain — it decides which playlists
        have enough songs, good variety, and match the station vibe.
        """
        queries = GENRE_QUERIES.get(genre, GENRE_QUERIES.get("lo-fi"))
        query = random.choice(queries)

        # Navigate to YouTube search in the browser
        search_url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}&sp=EgJAAQ%253D%253D"  # Filter: Playlist
        page = await self.browser.new_youtube_tab(search_url)
        if not page:
            return []

        try:
            # Extract search results (links and titles)
            await asyncio.sleep(3)  # Wait for page to load

            results = await page.evaluate("""
                () => {
                    const items = [];
                    document.querySelectorAll('a#video-title, a.yt-simple-endpoint').forEach(el => {
                        const href = el.getAttribute('href') || '';
                        const title = el.textContent?.trim() || '';
                        if (href.includes('/playlist?list=') || href.includes('/watch?')) {
                            items.push({href: href, title: title});
                        }
                    });
                    return items.slice(0, 20);
                }
            """)

            if not results:
                logger.warning("No search results extracted from YouTube page")
                return []

            # Send results to Hermes for selection
            playlist_urls = await self._ask_hermes(query, results, genre)
            return playlist_urls

        finally:
            await page.close()

    async def _ask_hermes(self, query: str, results: list, genre: str) -> list:
        """
        Ask Hermes to pick the best playlists from search results.
        Uses the Ollama OpenAI-compatible /v1/chat/completions endpoint.
        """
        # Build the prompt with search result context
        result_text = "\n".join(
            f"  {i + 1}. {r.get('title', 'Untitled')[:80]} → https://youtube.com{r.get('href', '')}"
            for i, r in enumerate(results[:15])
        )

        # Add genre hints for reggae sub-genres
        genre_hint = _HERMES_GENRE_HINTS.get(genre, genre)

        prompt = f"""You are the music director for an online radio station. I searched YouTube for "{query}" and got these results:

{result_text}

Pick the {2 - 3} BEST playlists that:
- Have 30+ songs (look for "50 videos", "100+ videos" etc in the title)
- Match the genre ({genre_hint})
- Are playlists (URLs containing /playlist?list=) NOT individual videos
- Are recent (2023-2025)

Reply with ONLY the YouTube playlist URLs, one per line. No explanation needed.
If none of the results are good playlists, reply with "NONE"."""

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": self.ollama_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a music curator AI for a 24/7 radio station. You only output YouTube playlist URLs, nothing else.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 200},
                }
                async with session.post(
                    f"{self.ollama_url}/v1/chat/completions",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = (
                            data.get("choices", [{}])[0]
                            .get("message", {})
                            .get("content", "")
                        )
                        # Extract playlist URLs from Hermes response
                        urls = re.findall(
                            r"https?://(?:www\.)?youtube\.com/playlist\?list=[\w-]+",
                            content,
                        )
                        if not urls:
                            # Also check for /watch URLs with list= param
                            urls = re.findall(
                                r'https?://(?:www\.)?youtube\.com/watch\?[^"\s]*list=([\w-]+)',
                                content,
                            )
                            urls = [
                                f"https://www.youtube.com/playlist?list={lid}"
                                for lid in urls
                            ]
                        return urls
                    else:
                        logger.error("Hermes API error: %d", resp.status)
                        return []
        except Exception as e:
            logger.error("Hermes request failed: %s", e)
            return []

    async def _discover_via_search(self, genre: str) -> list:
        """
        Direct YouTube search — no LLM needed.
        Navigate to YouTube, extract playlist links from search results.
        """
        queries = GENRE_QUERIES.get(genre, GENRE_QUERIES.get("lo-fi"))
        query = random.choice(queries)

        search_url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}&sp=EgJAAQ%253D%253D"
        page = await self.browser.new_youtube_tab(search_url)
        if not page:
            return []

        try:
            await asyncio.sleep(3)

            # Extract playlist URLs directly
            playlist_ids = await page.evaluate("""
                () => {
                    const ids = new Set();
                    document.querySelectorAll('a[href]').forEach(el => {
                        const href = el.getAttribute('href') || '';
                        // Match /playlist?list=PLxxxxx
                        const match = href.match(/\\/playlist\\?list=([\\w-]+)/);
                        if (match) ids.add(match[1]);
                    });
                    return Array.from(ids).slice(0, 10);
                }
            """)

            if not playlist_ids:
                return []

            urls = [
                f"https://www.youtube.com/playlist?list={pid}" for pid in playlist_ids
            ]
            logger.info("Direct search found %d playlists", len(urls))
            return urls

        except Exception as e:
            logger.error("Direct search failed: %s", e)
            return []
        finally:
            await page.close()
