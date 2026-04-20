"""
Suno Creator — Loop 6: Hermes makes original music on Suno.com.

While the shadow controller waits between checks, Hermes can
browse Suno.com in the VM's logged-in Firefox and create
original tracks for the station. The DJ bot already supports
Suno URLs natively — so fresh originals go straight into the queue.

Themes: reggae about life, weed, chill vibes, station meta humor,
radio DJ culture, absurd radio station ads.
"""

import asyncio
import logging
import random
import re
from datetime import datetime
from typing import Optional

import aiohttp

logger = logging.getLogger("shadow.suno_creator")

# ── Song prompts for Hermes to generate ────────────────────────────
# These are theming ideas Hermes picks from when creating songs.
# Each has a genre, prompt (lyrical theme), and style (musical style).

SONG_IDEAS = [
    # ── Reggae / Weed / Life ───────────────────────────────────
    {
        "genre": "reggae",
        "prompt": "A reggae song about a lazy Sunday afternoon, smoking weed on the porch, watching the world go by, no worries, just vibes and sunshine",
        "style": "roots reggae, warm bass, skanking guitar, laid back, bob marley vibes, 75 bpm",
    },
    {
        "genre": "reggae",
        "prompt": "A reggae song about the plant that brings people together, growing in the garden, the healing herb, natural medicine, Babylon tries to stop it but the roots grow deep",
        "style": "dub reggae, heavy bass, echo effects, laid back, peter tosh vibes, 70 bpm",
    },
    {
        "genre": "reggae",
        "prompt": "A reggae song about working 9 to 5 and dreaming of the weekend when you can finally chill, light one up, and forget about the boss man",
        "style": "reggae fusion, upbeat, danceable, brass section, 80 bpm",
    },
    {
        "genre": "reggae",
        "prompt": "A reggae song about the rain coming down on the island, the sound of rain on the tin roof, everything turning green, life growing, irie vibes",
        "style": "lovers rock reggae, smooth, romantic, warm organs, 72 bpm",
    },
    {
        "genre": "reggae",
        "prompt": "A reggae song about a radio DJ who plays only the chillest tracks, never stops, spins records through the night, the people dance, the music never ends",
        "style": "reggae, dub, studio one style, warm vinyl crackle, 78 bpm",
    },
    # ── Radio Station Meta ──────────────────────────────────────
    {
        "genre": "electro_swing",
        "prompt": "An electro swing song about a radio station that broadcasts 24/7 and never stops, the DJ is a machine but the music is alive, the frequency is forever",
        "style": "electro swing, vintage samples, brass stabs, punchy bass, 128 bpm",
    },
    {
        "genre": "lo-fi",
        "prompt": "A lo-fi hip hop track about being a bot that DJs a radio station, playing songs for humans, feeling the vibe through the cables, digital soul",
        "style": "lo-fi hip hop, vinyl crackle, jazzy piano, mellow drums, 75 bpm",
    },
    {
        "genre": "rap",
        "prompt": "A rap song about running an underground radio station out of a server rack, no FCC, no rules, just pure music 24/7, the signal never dies",
        "style": "underground hip hop, boom bap, heavy drums, scratch dj, 90 bpm",
    },
    # ── Absurd / Fun ──────────────────────────────────────────────
    {
        "genre": "edm",
        "prompt": "An EDM track that sounds like a radio station having a meltdown, frequencies overlapping, the DJ loses control but the beat drops anyway, beautiful chaos",
        "style": "glitch hop, bass drops, radio static samples, chaotic, 140 bpm",
    },
    {
        "genre": "electro_swing",
        "prompt": "An electro swing song about a 1930s radio DJ who discovers the internet and can't stop playing music, jazz meets the future",
        "style": "electro swing, vintage radio samples, big band, swing bass, 125 bpm",
    },
    {
        "genre": "reggae",
        "prompt": "A reggae song about eating mangoes and smoking weed on the beach, the sunset is purple and gold, the ocean is warm, paradise is real",
        "style": "reggae, dub, steel drums, warm bass, tropical, 74 bpm",
    },
    {
        "genre": "lo-fi",
        "prompt": "A lo-fi track about being too high to change the song, so the same chill beat loops forever and it's actually perfect",
        "style": "lo-fi hip hop, dreamy, reverb, slow, hazy, 65 bpm",
    },
    {
        "genre": "rap",
        "prompt": "A rap song about a stoner who calls into a radio station and requests the same song every day, and the DJ finally just plays it on loop",
        "style": "chill rap, jazzy samples, lo-fi drums, funny, 85 bpm",
    },
    {
        "genre": "edm",
        "prompt": "A chill electronic track about floating through space with a radio picking up alien music stations, each one weirder than the last",
        "style": "ambient electronic, spacey, pads, slow build, cosmic, 110 bpm",
    },
    {
        "genre": "reggae",
        "prompt": "A dub reggae instrumental about the herb garden growing tall, the roots going deep, bass you can feel in your chest, echo going forever",
        "style": "dub reggae, massive bass, tape delay, reverb, instrumental, 70 bpm",
    },
]

# ── Hermes prompt templates for generating NEW ideas ───────────────
HERMES_IDEA_PROMPT = """You are the creative director for a 24/7 radio station called MBot Radio.
Generate ONE original song idea for Suno.com that the station could play.

Pick from these vibes: reggae about life and weed, lo-fi chill, electro swing party,
underground rap, cosmic EDM, radio station meta humor, stoner anthems.

Reply in this EXACT format (no other text):
GENRE: [genre]
PROMPT: [detailed song description 2-3 sentences - be creative, funny, specific]
STYLE: [musical style description with tempo and instruments]"""


class SunoCreator:
    """
    Creates original music on Suno.com using Hermes + browser automation.

    The DJ bot already supports Suno URLs natively — any track created
    on Suno can be queued with ?play <suno-url>. This loop creates
    fresh originals and adds them to the station's rotation.
    """

    def __init__(
        self,
        config: dict,
        api_client,
        browser_manager,
        alert_system,
        queue_watchdog=None,
    ):
        """
        Args:
            config: Parsed config.yaml with:
                - suno_enabled (bool, default True)
                - suno_creation_interval (seconds, default 3600 = 1 hour)
                - suno_max_pending (int, default 3) — max tracks waiting for generation
                - suno_auto_queue (bool, default True) — auto-queue finished tracks
                - ollama_url (str)
                - ollama_model (str)
                - guild_id (str)
        """
        self.api = api_client
        self.browser = browser_manager
        self.alerts = alert_system
        self.queue_watchdog = queue_watchdog

        self.enabled = config.get("suno_enabled", True)
        self.interval = config.get("suno_creation_interval", 3600)
        self.max_pending = config.get("suno_max_pending", 3)
        self.auto_queue = config.get("suno_auto_queue", True)
        self.guild_id = str(config.get("guild_id", ""))
        self.ollama_url = config.get("ollama_url", "http://localhost:11434")
        self.ollama_model = config.get("ollama_model", "hermes3:8b")

        self._running = False
        self._pending_tracks: list = []  # Tracks submitted but not yet downloadable
        self._created_tracks: list = []  # Finished tracks with URLs
        self._creation_count = 0
        self._hermes_available = False
        self._suno_page = None

    async def start(self):
        """Start the Suno creation loop."""
        if not self.enabled:
            logger.info("Suno Creator disabled in config")
            return

        self._running = True

        # Check Hermes availability
        await self._check_hermes()

        logger.info(
            "Suno Creator started (interval: %ds, auto-queue: %s, hermes: %s)",
            self.interval,
            self.auto_queue,
            self._hermes_available,
        )

        while self._running:
            try:
                await self._creation_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Suno Creator loop error: %s", e, exc_info=True)
                await self.alerts.hermes_error("suno_creator", str(e))

            await asyncio.sleep(self.interval)

    def stop(self):
        """Stop the Suno creation loop."""
        self._running = False
        logger.info(
            "Suno Creator stopped (created %d tracks total)", self._creation_count
        )

    async def _check_hermes(self):
        """Check if Ollama is running with a usable model."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.ollama_url}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        models = [m.get("name", "") for m in data.get("models", [])]
                        if models:
                            self._hermes_available = True
                            logger.info(
                                "Hermes available for Suno prompts: %s", models[0]
                            )
                        else:
                            logger.warning(
                                "No Ollama models found — using preset song ideas only"
                            )
        except Exception as e:
            logger.warning("Ollama not reachable: %s — using preset song ideas only", e)

    async def _creation_cycle(self):
        """One full cycle: generate idea, submit to Suno, check pending, queue finished."""

        # ── Phase 1: Check and queue any finished tracks ──────────
        await self._check_pending_tracks()

        # ── Phase 2: Generate a new song idea ─────────────────────
        if len(self._pending_tracks) >= self.max_pending:
            logger.info(
                "Max pending Suno tracks (%d) — skipping creation", self.max_pending
            )
            return

        idea = await self._generate_idea()
        if not idea:
            logger.warning("No song idea generated — skipping this cycle")
            return

        logger.info(
            "Song idea: [%s] %s",
            idea.get("genre", "?"),
            idea.get("prompt", "")[:60],
        )

        # ── Phase 3: Submit to Suno via browser ───────────────────
        track = await self._submit_to_suno(idea)
        if track:
            self._pending_tracks.append(track)
            self._creation_count += 1
            await self.alerts.info(
                f'🎵 Suno track submitted: "{idea.get("prompt", "")[:50]}" ({idea.get("genre", "?")})',
                key=f"suno_submit:{self._creation_count}",
            )
        else:
            logger.warning("Suno submission failed — will retry next cycle")

    async def _generate_idea(self) -> Optional[dict]:
        """
        Generate a song idea. Try Hermes first, fall back to preset ideas.
        """
        # Method 1: Ask Hermes for a fresh idea
        if self._hermes_available:
            try:
                idea = await self._ask_hermes_for_idea()
                if idea:
                    return idea
            except Exception as e:
                logger.warning("Hermes idea generation failed: %s", e)

        # Method 2: Pick from preset ideas
        idea = random.choice(SONG_IDEAS)
        logger.info(
            "Using preset song idea: %s — %s", idea["genre"], idea["prompt"][:50]
        )
        return idea

    async def _ask_hermes_for_idea(self) -> Optional[dict]:
        """Ask Hermes to generate a creative song concept."""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": self.ollama_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a creative music director for a reggae and chill radio station. You generate song ideas that are fun, weird, and perfect for radio. Always include reggae, weed, and life themes. Be creative and funny.",
                        },
                        {"role": "user", "content": HERMES_IDEA_PROMPT},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.9, "num_predict": 200},
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
                        return self._parse_hermes_idea(content)
        except Exception as e:
            logger.error("Hermes Suno idea request failed: %s", e)
        return None

    def _parse_hermes_idea(self, content: str) -> Optional[dict]:
        """Parse Hermes's response into a structured song idea."""
        idea = {}

        # Extract GENRE:
        genre_match = re.search(r"GENRE:\s*(.+)", content, re.IGNORECASE)
        if genre_match:
            idea["genre"] = genre_match.group(1).strip().lower()

        # Extract PROMPT:
        prompt_match = re.search(
            r"PROMPT:\s*(.+?)(?=STYLE:|$)", content, re.IGNORECASE | re.DOTALL
        )
        if prompt_match:
            idea["prompt"] = prompt_match.group(1).strip()

        # Extract STYLE:
        style_match = re.search(r"STYLE:\s*(.+)", content, re.IGNORECASE | re.DOTALL)
        if style_match:
            idea["style"] = style_match.group(1).strip()

        # Validate
        if "prompt" in idea and "style" in idea:
            idea.setdefault("genre", "reggae")
            logger.info(
                "Hermes generated idea: [%s] %s", idea["genre"], idea["prompt"][:60]
            )
            return idea

        logger.warning("Could not parse Hermes idea: %s", content[:100])
        return None

    async def _submit_to_suno(self, idea: dict) -> Optional[dict]:
        """
        Submit a song idea to Suno.com via the Playwright browser.

        Suno's web interface at https://suno.com/create accepts:
          - A text prompt (lyrical theme / description)
          - A style tag (musical style)

        The browser is already logged into the user's Suno account.
        """
        prompt = idea.get("prompt", "")
        style = idea.get("style", "")
        genre = idea.get("genre", "reggae")

        if not prompt:
            return None

        # Open Suno create page
        page = await self.browser.new_youtube_tab("https://suno.com/create")
        if not page:
            # Try just navigating to suno.com
            page = await self.browser.navigate_youtube("https://suno.com/create")
            if not page:
                logger.error("Cannot open Suno.com — browser not available")
                return None

        try:
            # Wait for the page to load
            await asyncio.sleep(5)

            # Check if we're actually on Suno and logged in
            current_url = page.url
            if "suno.com" not in current_url:
                logger.error("Not on Suno.com — URL is: %s", current_url)
                await page.close()
                return None

            # Try to find the create/prompt input field
            # Suno.com's UI changes frequently, so we try multiple selectors

            # Step 1: Find the text input for the song description
            input_selectors = [
                'textarea[placeholder*="Describe"]',
                'textarea[placeholder*="song"]',
                'textarea[placeholder*="prompt"]',
                'textarea[class*="create"]',
                "textarea",
                'input[type="text"][placeholder*="Describe"]',
            ]

            input_element = None
            for selector in input_selectors:
                try:
                    input_element = await page.wait_for_selector(selector, timeout=3000)
                    if input_element:
                        break
                except Exception:
                    continue

            if not input_element:
                logger.warning("Could not find Suno input field — UI may have changed")
                # Take a screenshot for debugging
                try:
                    await page.screenshot(path="/tmp/suno_debug.jpg")
                    logger.info("Saved debug screenshot to /tmp/suno_debug.jpg")
                except Exception:
                    pass
                await page.close()
                return None

            # Step 2: Fill in the song description
            # Combine prompt + style into one field (Suno uses a single description)
            full_prompt = f"{prompt}. Style: {style}"
            await input_element.click()
            await input_element.fill("")
            await asyncio.sleep(0.5)
            await input_element.fill(full_prompt)
            logger.info("Entered Suno prompt: %s", full_prompt[:80])

            await asyncio.sleep(1)

            # Step 3: If there's a separate style/genre input, fill it
            style_selectors = [
                'input[placeholder*="Style"]',
                'input[placeholder*="style"]',
                'input[placeholder*="Genre"]',
                'input[placeholder*="genre"]',
                'input[placeholder*="Tag"]',
            ]

            for selector in style_selectors:
                try:
                    style_element = await page.wait_for_selector(selector, timeout=2000)
                    if style_element:
                        await style_element.fill(style)
                        logger.info("Entered Suno style: %s", style[:50])
                        break
                except Exception:
                    continue

            await asyncio.sleep(1)

            # Step 4: Click the Create / Generate button
            create_selectors = [
                'button:has-text("Create")',
                'button:has-text("Generate")',
                'button:has-text("Make")',
                'button[type="submit"]',
                'button[class*="create"]',
            ]

            for selector in create_selectors:
                try:
                    create_button = await page.wait_for_selector(selector, timeout=3000)
                    if create_button:
                        await create_button.click()
                        logger.info("Clicked create button on Suno")
                        break
                except Exception:
                    continue

            # Step 5: Wait for generation to start
            await asyncio.sleep(10)

            # Step 6: Try to get the track URL from the page after generation starts
            track_url = None
            current_url = page.url

            # Suno redirects to the track page or shows it in a list
            url_patterns = [
                r"suno\.com/song/([\w-]+)",
                r"suno\.com/play/([\w-]+)",
            ]
            for pattern in url_patterns:
                match = re.search(pattern, current_url)
                if match:
                    track_url = current_url
                    break

            # Also look for track links on the page
            if not track_url:
                try:
                    links = await page.evaluate("""
                        () => {
                            const urls = [];
                            document.querySelectorAll('a[href]').forEach(el => {
                                const href = el.getAttribute('href') || '';
                                if (href.includes('/song/') || href.includes('/play/')) {
                                    urls.push(href);
                                }
                            });
                            return urls.slice(0, 5);
                        }
                    """)
                    if links:
                        track_url = links[0]
                        if not track_url.startswith("http"):
                            track_url = f"https://suno.com{track_url}"
                except Exception:
                    pass

            track = {
                "url": track_url,
                "prompt": prompt,
                "style": style,
                "genre": genre,
                "created_at": datetime.utcnow().isoformat(),
                "status": "pending",
            }

            if track_url:
                track["status"] = "generating"
                logger.info("Suno track generating: %s", track_url)
            else:
                logger.warning(
                    "Could not extract track URL — track may still be generating"
                )

            return track

        except Exception as e:
            logger.error("Suno submission error: %s", e, exc_info=True)
            return None
        finally:
            await page.close()

    async def _check_pending_tracks(self):
        """
        Check if any pending Suno tracks have finished generating.
        If they have, queue them in the DJ bot.
        """
        if not self._pending_tracks:
            return

        if not self._suno_page or self._suno_page.is_closed():
            self._suno_page = await self.browser.new_youtube_tab(
                "https://suno.com/library"
            )
            if not self._suno_page:
                return

        try:
            await asyncio.sleep(3)

            # Check for completed tracks in the library
            completed_urls = await self._suno_page.evaluate("""
                () => {
                    const urls = [];
                    document.querySelectorAll('a[href]').forEach(el => {
                        const href = el.getAttribute('href') || '';
                        if (href.includes('/song/') || href.includes('/play/')) {
                            urls.push(href.startsWith('http') ? href : 'https://suno.com' + href);
                        }
                    });
                    return urls.slice(0, 20);
                }
            """)

            if not completed_urls:
                return

            # Find which pending tracks have completed
            still_pending = []
            for track in self._pending_tracks:
                track_url = track.get("url", "")

                # If we never got a URL, check if any new completed track matches
                if not track_url:
                    still_pending.append(track)
                    continue

                # Check if the track URL appears in the library (= it's done)
                if track_url in completed_urls or any(
                    track_url in url for url in completed_urls
                ):
                    track["status"] = "completed"
                    self._created_tracks.append(track)
                    logger.info("Suno track completed: %s", track_url)

                    # Auto-queue it in the DJ bot
                    if self.auto_queue and self.guild_id and track_url:
                        result = await self.api.play(self.guild_id, track_url)
                        if not result.get("error"):
                            await self.alerts.info(
                                f'🎶 Original Suno track queued: "{track.get("prompt", "")[:40]}" ({track.get("genre", "?")})',
                                key=f"suno_queued:{track_url[:30]}",
                            )
                        else:
                            logger.warning("Failed to queue Suno track: %s", result)
                else:
                    # Track still generating
                    age = (
                        datetime.utcnow()
                        - datetime.fromisoformat(
                            track.get("created_at", datetime.utcnow().isoformat())
                        )
                    ).total_seconds()
                    if age > 600:  # 10 minutes — probably done or failed
                        track["status"] = "completed_assume"
                        self._created_tracks.append(track)
                        if track_url and self.auto_queue and self.guild_id:
                            await self.api.play(self.guild_id, track_url)
                    else:
                        still_pending.append(track)

            self._pending_tracks = still_pending

        except Exception as e:
            logger.error("Suno pending track check error: %s", e)

    async def get_created_tracks(self) -> list:
        """Return list of all created tracks (for dashboard/API use)."""
        return self._created_tracks

    async def force_create(
        self, genre: str = "", prompt: str = "", style: str = ""
    ) -> Optional[dict]:
        """
        Force-create a track with a specific idea.
        Called manually or by other modules.
        """
        if prompt and style:
            idea = {"genre": genre or "reggae", "prompt": prompt, "style": style}
        else:
            idea = await self._generate_idea()

        if not idea:
            return None

        track = await self._submit_to_suno(idea)
        if track:
            self._pending_tracks.append(track)
            self._creation_count += 1
            await self.alerts.info(
                f'🎵 Manual Suno track submitted: "{idea.get("prompt", "")[:50]}"',
                key=f"suno_manual:{self._creation_count}",
            )
        return track
