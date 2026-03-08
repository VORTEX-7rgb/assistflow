"""
RapidRAG — scraper.py  (FIXED)

BUGS FIXED vs previous version:
  1. _disallowed_paths was an INSTANCE variable — persisted between scrape_site()
     calls across different clients. Client A's site could poison Client B's scraper.
     FIX: Reset _disallowed_paths at the START of every scrape_site() call.

  2. Timeout too short (10s) — kills slow/shared-hosting sites common in India.
     FIX: Raised to 25s per page, robots.txt fetch stays at 5s.

  3. Homepage cap bug — homepage was inserted AFTER the max_pages cap was applied,
     so on sites where homepage wasn't in the filtered list AND list was already
     at max_pages, we'd scrape max_pages+1 pages total.
     FIX: Insert homepage BEFORE cap, then deduplicate, then cap.

  4. Link discovery only scraped homepage for links — misses JS-rendered menus.
     FIX: Added secondary link discovery from sitemap.xml as fallback.

  5. BeautifulSoup was using html.parser (slow, less accurate).
     FIX: Try lxml first, fall back to html.parser if not installed.

  6. WebsiteIngestRequest url field_validator was using model_validate override
     which doesn't fire during FastAPI request parsing.
     FIX: Added url normalization at the TOP of scrape_site() as safety net.
"""

import re
import asyncio
import logging
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp
from bs4 import BeautifulSoup

from config import settings, ScrapedPage

logger = logging.getLogger(__name__)

# Best available HTML parser
try:
    import lxml  # noqa: F401
    _HTML_PARSER = "lxml"
except ImportError:
    _HTML_PARSER = "html.parser"
    logger.warning("lxml not installed — using html.parser (slower). Run: pip install lxml")


class WebScraper:
    """
    Async web scraper designed for small business websites.
    - Discovers important links automatically (homepage + sitemap)
    - Filters out blogs, news, admin pages
    - Scrapes up to 30 pages concurrently (10 workers)
    - Cleans HTML → extracts pure text
    - Target: full site scraped in <60 seconds
    """

    KEEP_PATTERNS = [
        "/service", "/pricing", "/price", "/rate", "/fee",
        "/faq", "/about", "/contact", "/team", "/doctor", "/staff",
        "/treatment", "/specialty", "/speciality", "/course", "/program",
        "/gallery", "/testimonial", "/review", "/hour", "/location",
        "/menu", "/package", "/offer", "/branch", "/product", "/facility",
        "/tour", "/tours", "/collection", "/news", "/article", "/superstars",
        "/events", "/shows",
    ]

    BLOCK_PATTERNS = [
        "/tag", "/category",
        "/author", "/archive", "/wp-content", "/wp-admin", "/wp-json",
        "/feed", "/rss", "/login", "/admin", "/cart", "/checkout",
        "/account", "/register", "/signin", "/signup", "/search",
        "/comment", "/trackback", "/xmlrpc",
    ]

    BLOCK_EXTENSIONS = {
        ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
        ".xlsx", ".ppt", ".pptx", ".css", ".js",
    }

    REMOVE_TAGS = [
        "script", "style", "nav", "header", "footer", "aside",
        "noscript", "iframe", "form", "button", "svg", "canvas",
    ]

    def __init__(self):
        self.max_pages    = settings.scraper_max_pages
        self.concurrency  = settings.scraper_concurrency
        self.timeout      = max(settings.scraper_timeout, 25)  # min 25s — many sites are slow
        self.user_agent   = "Mozilla/5.0 (compatible; RapidRAG-Bot/1.0)"
        # NOTE: _disallowed_paths is NOT set here — it's reset per scrape_site() call
        # to prevent cross-client contamination (Bug #1)

    # ─────────────────────────────────────────
    # Main Entry Point
    # ─────────────────────────────────────────

    async def scrape_site(self, url: str) -> list[ScrapedPage]:
        """
        Scrape an entire website. Full pipeline:
        1. Reset per-scrape state (FIX: was shared across clients)
        2. Discover links from homepage + sitemap
        3. Filter to important pages only
        4. Concurrently fetch all pages
        5. Clean HTML → extract text
        6. Return list of ScrapedPage objects
        """
        # FIX #1: Reset disallowed paths EVERY call — never share state between clients
        disallowed_paths: set[str] = set()

        # Normalize URL (safety net — API layer should also do this)
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        base_url = self._normalize_url(url)

        logger.info(f"Starting scrape: {base_url}")

        # Load robots.txt (with fresh state)
        await self._load_robots_txt(base_url, disallowed_paths)

        # Discover links from homepage
        all_links = await self._discover_links(base_url)

        # Try sitemap as extra source (many sites have pages not linked from homepage)
        sitemap_links = await self._discover_from_sitemap(base_url)
        all_links = list(set(all_links) | set(sitemap_links))

        logger.info(f"Discovered {len(all_links)} total links")

        # FIX #3: Insert homepage BEFORE filtering and cap
        if base_url not in all_links:
            all_links.insert(0, base_url)

        # Filter to important pages
        important_links = [
            link for link in all_links if self._is_important_page(link, disallowed_paths)
        ]

        # Ensure homepage is always in the list (re-insert if filtered out)
        if base_url not in important_links:
            important_links.insert(0, base_url)

        # Deduplicate and cap
        seen = set()
        deduped = []
        for link in important_links:
            if link not in seen:
                seen.add(link)
                deduped.append(link)

        important_links = deduped[:self.max_pages]

        logger.info(f"Filtered to {len(important_links)} important pages (cap={self.max_pages})")

        # Concurrent fetch
        semaphore = asyncio.Semaphore(self.concurrency)
        connector = aiohttp.TCPConnector(limit=self.concurrency, ssl=False)
        timeout   = aiohttp.ClientTimeout(total=self.timeout, connect=10)

        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as session:
            tasks = [
                self._fetch_page(session, semaphore, link)
                for link in important_links
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        pages = []
        errors = 0
        for result in results:
            if isinstance(result, ScrapedPage) and result.clean_text.strip():
                pages.append(result)
            else:
                errors += 1

        logger.info(
            f"Scrape complete: {len(pages)} pages scraped, {errors} errors/empty"
        )
        return pages

    # ─────────────────────────────────────────
    # Robots.txt
    # ─────────────────────────────────────────

    async def _load_robots_txt(self, base_url: str, disallowed_paths: set[str]) -> None:
        """Parse robots.txt and cache disallowed paths. Fail-open."""
        try:
            parsed = urlparse(base_url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(robots_url, ssl=False) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        applies = False
                        for line in text.splitlines():
                            line = line.strip()
                            if line.lower().startswith("user-agent:"):
                                agent = line.split(":", 1)[1].strip().lower()
                                applies = agent == "*" or "rapidrag" in agent
                            elif applies and line.lower().startswith("disallow:"):
                                path = line.split(":", 1)[1].strip()
                                if path:
                                    disallowed_paths.add(path)
        except Exception:
            pass  # fail-open — always scrape if robots.txt unreachable

    # ─────────────────────────────────────────
    # Link Discovery — Homepage
    # ─────────────────────────────────────────

    async def _discover_links(self, base_url: str) -> list[str]:
        """Fetch homepage and extract all internal links."""
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": self.user_agent},
            ) as session:
                async with session.get(base_url, ssl=False) as response:
                    if response.status != 200:
                        logger.warning(f"Homepage returned {response.status}: {base_url}")
                        return [base_url]
                    html = await response.text()
        except Exception as e:
            logger.error(f"Failed to fetch homepage for link discovery: {e}")
            return [base_url]

        soup = BeautifulSoup(html, _HTML_PARSER)
        base_domain = urlparse(base_url).netloc

        links = {base_url}

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue

            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)

            if parsed.netloc != base_domain:
                continue

            path_lower = parsed.path.lower()
            if any(path_lower.endswith(ext) for ext in self.BLOCK_EXTENSIONS):
                continue

            clean_url = urlunparse((
                parsed.scheme, parsed.netloc, parsed.path,
                parsed.params, parsed.query, "",
            ))
            links.add(clean_url)

        return list(links)

    # ─────────────────────────────────────────
    # Link Discovery — Sitemap (FIX #4 extra source)
    # ─────────────────────────────────────────

    async def _discover_from_sitemap(self, base_url: str) -> list[str]:
        """
        Try to parse sitemap.xml for additional page links.
        Most business sites have it. Fail silently if missing.
        """
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc
        sitemap_urls = [
            f"{parsed_base.scheme}://{parsed_base.netloc}/sitemap.xml",
            f"{parsed_base.scheme}://{parsed_base.netloc}/sitemap_index.xml",
        ]

        links = []
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for sitemap_url in sitemap_urls:
                    try:
                        async with session.get(sitemap_url, ssl=False) as resp:
                            if resp.status != 200:
                                continue
                            text = await resp.text()
                            # Extract <loc> tags
                            for match in re.findall(r"<loc>(.*?)</loc>", text, re.IGNORECASE):
                                loc = match.strip()
                                if urlparse(loc).netloc == base_domain:
                                    if loc.endswith(".xml"):
                                        try:
                                            async with session.get(loc, ssl=False) as sub_resp:
                                                if sub_resp.status == 200:
                                                    sub_text = await sub_resp.text()
                                                    for sub_match in re.findall(r"<loc>(.*?)</loc>", sub_text, re.IGNORECASE):
                                                        sub_loc = sub_match.strip()
                                                        if urlparse(sub_loc).netloc == base_domain and not sub_loc.endswith(".xml"):
                                                            links.append(sub_loc)
                                        except Exception:
                                            pass
                                    else:
                                        links.append(loc)
                        if links:
                            break  # got results, don't try next sitemap
                    except Exception:
                        continue
        except Exception:
            pass

        logger.debug(f"Sitemap discovered {len(links)} additional links")
        return links

    # ─────────────────────────────────────────
    # Page Fetching
    # ─────────────────────────────────────────

    async def _fetch_page(
        self,
        session: aiohttp.ClientSession,
        semaphore: asyncio.Semaphore,
        url: str,
        max_retries: int = 2,
    ) -> ScrapedPage:
        """Fetch a single page and extract clean text. Retries on timeout/5xx."""
        async with semaphore:
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    async with session.get(url, ssl=False, allow_redirects=True) as response:
                        if response.status >= 500 and attempt < max_retries:
                            await asyncio.sleep(1 * (attempt + 1))
                            continue

                        if response.status != 200:
                            logger.debug(f"Skip {url}: status {response.status}")
                            return ScrapedPage(url=url)

                        content_type = response.headers.get("Content-Type", "")
                        if "text/html" not in content_type:
                            return ScrapedPage(url=url)

                        html = await response.text(errors="replace")

                    title, clean_text = self._clean_html(html)
                    word_count = len(clean_text.split())

                    # Use an aggressive threshold (e.g. 100 words) because modern JS sites load shell with very little text
                    if word_count < 100:
                        # Page has scarce content — likely a redirect, paywall, or client-side rendered JS app.
                        logger.debug(f"Skip {url}: too few words ({word_count}), trying playwright fallback...")
                        try:
                            from playwright.async_api import async_playwright
                            async with async_playwright() as p:
                                browser = await p.chromium.launch(headless=True)
                                try:
                                    page = await browser.new_page()
                                    await page.goto(url, wait_until="networkidle", timeout=15000)
                                    html = await page.content()
                                    title, clean_text = self._clean_html(html)
                                    word_count = len(clean_text.split())
                                    if word_count >= 20:
                                        logger.debug(f"Playwright scraped: {url} ({word_count} words)")
                                        await browser.close()
                                        return ScrapedPage(
                                            url=url, title=title, clean_text=clean_text, word_count=word_count
                                        )
                                finally:
                                    await browser.close()
                        except Exception as pe:
                            logger.debug(f"Playwright fallback failed for {url}: {pe}")
                            
                        logger.debug(f"Skip {url}: still too few words after fallback")
                        return ScrapedPage(url=url)

                    logger.debug(f"Scraped: {url} ({word_count} words)")
                    return ScrapedPage(
                        url=url,
                        title=title,
                        clean_text=clean_text,
                        word_count=word_count,
                    )

                except asyncio.TimeoutError:
                    last_error = "Timeout"
                    if attempt < max_retries:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                except Exception as e:
                    last_error = str(e)
                    if attempt < max_retries:
                        await asyncio.sleep(1 * (attempt + 1))
                        continue

            logger.debug(f"Failed after {max_retries+1} attempts: {url} — {last_error}")
            return ScrapedPage(url=url)

    # ─────────────────────────────────────────
    # HTML Cleaning  (FIX #5: uses lxml when available)
    # ─────────────────────────────────────────

    def _clean_html(self, html: str) -> tuple[str, str]:
        """Extract clean text from raw HTML. Returns (title, clean_text)."""
        soup = BeautifulSoup(html, _HTML_PARSER)

        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        # Remove noise tags
        for tag_name in self.REMOVE_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # Remove cookie/popup/banner elements
        noise_patterns = [
            "cookie", "popup", "modal", "banner", "newsletter",
            "advertisement", "sidebar", "widget", "social", "overlay",
        ]
        for pattern in noise_patterns:
            for el in soup.find_all(attrs={"class": re.compile(pattern, re.I)}):
                el.decompose()
            for el in soup.find_all(attrs={"id": re.compile(pattern, re.I)}):
                el.decompose()

        # Try to find main content area
        main_content = (
            soup.find("main")
            or soup.find("article")
            or soup.find("div", {"role": "main"})
            or soup.find("div", {"id": re.compile(r"content|main", re.I)})
            or soup.find("div", {"class": re.compile(r"content|main", re.I)})
            or soup.body
            or soup
        )

        text = main_content.get_text(separator="\n", strip=True)
        text = self._post_clean(text)

        return title, text

    def _post_clean(self, text: str) -> str:
        """Final text cleanup after HTML extraction."""
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            if len(stripped) > 2:
                lines.append(stripped)
        text = "\n".join(lines)
        text = text.replace("\x00", "")
        return text.strip()

    # ─────────────────────────────────────────
    # Page Filtering
    # ─────────────────────────────────────────

    def _is_important_page(self, url: str, disallowed_paths: set[str]) -> bool:
        """Returns True for business-relevant pages, False for noise."""
        parsed = urlparse(url)
        path = parsed.path.lower().rstrip("/")

        if not path or path == "/":
            return True

        # Check robots.txt disallowed paths
        for disallowed in disallowed_paths:
            if path.startswith(disallowed):
                return False

        if any(blocked in path for blocked in self.BLOCK_PATTERNS):
            return False

        if any(path.endswith(ext) for ext in self.BLOCK_EXTENSIONS):
            return False

        if any(keep in path for keep in self.KEEP_PATTERNS):
            return True

        # Let all pages through unless explicitly blocked or bad extensions
        # Increase URL depth allowance to capture rich deep data
        segments = [s for s in path.split("/") if s]
        if len(segments) <= 8:
            return True

        return False

    # ─────────────────────────────────────────
    # URL Normalization
    # ─────────────────────────────────────────

    def _normalize_url(self, url: str) -> str:
        """Ensure URL has scheme, no fragment, no trailing slash issues."""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        parsed = urlparse(url)
        path = parsed.path if parsed.path else "/"

        return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, ""))