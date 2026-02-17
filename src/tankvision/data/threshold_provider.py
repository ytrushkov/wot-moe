"""MoE threshold provider: fetches and caches damage thresholds from wotconsole.info.

The Wargaming API does NOT expose MoE damage thresholds (the combined damage
needed to reach 65%/85%/95%). These must come from community sites that track
server population data.

Data flow:
    1. On startup, check if local cache is stale (>24 hours old).
    2. If stale, fetch the full marks table from wotconsole.info/marks/.
    3. Parse all tanks in one pass and cache to a local JSON file.
    4. At runtime, look up thresholds by tank name (fuzzy match).
    5. If fetch fails, use stale cache. If no cache at all, return None.

Primary source: https://wotconsole.info/marks/
Fallback: stale on-disk cache, then config ``target_damage`` as last resort.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

# wotconsole.info marks page — lists all tanks with MoE thresholds
MARKS_PAGE_URL = "https://wotconsole.info/marks/"

# Cache thresholds for 24 hours
CACHE_TTL_SECONDS = 86400

# Request timeout for fetching the marks page
_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=30)


@dataclass
class MoeThresholds:
    """Damage thresholds for a specific tank's Marks of Excellence."""

    tank_name: str
    mark_65: float  # Combined damage for 1 mark (65th percentile)
    mark_85: float  # Combined damage for 2 marks (85th percentile)
    mark_95: float  # Combined damage for 3 marks (95th percentile)

    def target_for_mark(self, mark_level: int) -> float:
        """Get the target damage for a specific mark level (1, 2, or 3)."""
        if mark_level == 1:
            return self.mark_65
        elif mark_level == 2:
            return self.mark_85
        elif mark_level == 3:
            return self.mark_95
        raise ValueError(f"Invalid mark level: {mark_level}. Must be 1, 2, or 3.")

    def to_dict(self) -> dict:
        return {
            "tank_name": self.tank_name,
            "mark_65": self.mark_65,
            "mark_85": self.mark_85,
            "mark_95": self.mark_95,
        }

    @classmethod
    def from_dict(cls, data: dict) -> MoeThresholds:
        return cls(
            tank_name=data["tank_name"],
            mark_65=data["mark_65"],
            mark_85=data["mark_85"],
            mark_95=data["mark_95"],
        )


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------


class _MarksTableParser(HTMLParser):
    """Best-effort parser for the wotconsole.info/marks/ HTML table.

    The page is expected to contain a ``<table>`` with columns for tank name
    and MoE damage values at 65%, 85%, and 95%.  Column names may vary, so we
    discover column indices from the ``<th>`` header row.
    """

    # Patterns used to identify header columns
    _NAME_PATTERNS = re.compile(r"(?i)tank|vehicle|name")
    _65_PATTERNS = re.compile(r"(?i)65|1\s*mark|mark\s*1|1st")
    _85_PATTERNS = re.compile(r"(?i)85|2\s*mark|mark\s*2|2nd")
    _95_PATTERNS = re.compile(r"(?i)95|3\s*mark|mark\s*3|3rd")

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict] = []
        # Column index mapping — discovered from <th> headers
        self._col_name: int = -1
        self._col_65: int = -1
        self._col_85: int = -1
        self._col_95: int = -1
        self._headers_found = False
        # State tracking
        self._in_table = False
        self._in_thead = False
        self._in_tbody = False
        self._in_tr = False
        self._in_cell = False
        self._cell_index = 0
        self._current_row: list[str] = []
        self._current_cell_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._in_table = True
        elif tag == "thead":
            self._in_thead = True
        elif tag == "tbody":
            self._in_tbody = True
        elif tag == "tr" and self._in_table:
            self._in_tr = True
            self._current_row = []
            self._cell_index = 0
        elif tag in ("th", "td") and self._in_tr:
            self._in_cell = True
            self._current_cell_text = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._in_table = False
            self._in_thead = False
            self._in_tbody = False
        elif tag == "thead":
            self._in_thead = False
        elif tag == "tbody":
            self._in_tbody = False
        elif tag in ("th", "td") and self._in_cell:
            self._in_cell = False
            self._current_row.append(self._current_cell_text.strip())
            self._cell_index += 1
        elif tag == "tr" and self._in_tr:
            self._in_tr = False
            if self._in_thead or not self._headers_found:
                self._try_discover_headers(self._current_row)
            elif self._headers_found:
                self._process_data_row(self._current_row)

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell_text += data

    def _try_discover_headers(self, row: list[str]) -> None:
        """Try to identify column indices from a header row."""
        for i, text in enumerate(row):
            if self._NAME_PATTERNS.search(text):
                self._col_name = i
            elif self._95_PATTERNS.search(text):
                self._col_95 = i
            elif self._85_PATTERNS.search(text):
                self._col_85 = i
            elif self._65_PATTERNS.search(text):
                self._col_65 = i

        if self._col_name >= 0 and self._col_95 >= 0:
            self._headers_found = True
            logger.debug(
                "Discovered marks table columns: name=%d, 65=%d, 85=%d, 95=%d",
                self._col_name, self._col_65, self._col_85, self._col_95,
            )

    def _process_data_row(self, row: list[str]) -> None:
        """Extract threshold data from a table body row."""
        if len(row) <= max(self._col_name, self._col_95):
            return

        name = row[self._col_name].strip()
        if not name:
            return

        mark_95 = self._parse_number(row[self._col_95]) if self._col_95 >= 0 else 0.0
        mark_85 = self._parse_number(row[self._col_85]) if self._col_85 >= 0 else 0.0
        mark_65 = self._parse_number(row[self._col_65]) if self._col_65 >= 0 else 0.0

        if mark_95 > 0:
            # If we only have the 95% mark, estimate the others
            if mark_85 <= 0:
                mark_85 = mark_95 * (85.0 / 95.0)
            if mark_65 <= 0:
                mark_65 = mark_95 * (65.0 / 95.0)

            self.results.append({
                "tank_name": name,
                "mark_65": mark_65,
                "mark_85": mark_85,
                "mark_95": mark_95,
            })

    @staticmethod
    def _parse_number(text: str) -> float:
        """Parse a numeric string, stripping commas, spaces, and units."""
        cleaned = re.sub(r"[^\d.]", "", text)
        try:
            return float(cleaned)
        except ValueError:
            return 0.0


def _parse_embedded_json(html: str) -> list[dict] | None:
    """Try to extract tank data from embedded JSON in <script> tags.

    Many dynamic sites embed the initial dataset as a JavaScript variable
    inside the HTML page (e.g. ``var data = [...]``).
    """
    # Look for JSON arrays embedded in script tags
    patterns = [
        r"(?:var|let|const)\s+\w+\s*=\s*(\[.*?\]);",
        r"data\s*[:=]\s*(\[.*?\])[,;\s]",
        r"marks\s*[:=]\s*(\[.*?\])[,;\s]",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, html, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                if not isinstance(data, list) or len(data) < 10:
                    continue
                # Validate: each item should have a name-like field and number-like fields
                sample = data[0]
                if not isinstance(sample, dict):
                    continue
                # Try to identify the structure
                result = _normalize_json_entries(data)
                if result:
                    return result
            except (json.JSONDecodeError, IndexError, KeyError):
                continue
    return None


def _normalize_json_entries(entries: list[dict]) -> list[dict] | None:
    """Normalize JSON entries into our standard format.

    The JSON structure may vary, so we try multiple field name patterns.
    """
    # Possible field names for each value
    name_keys = ["tank_name", "name", "vehicle", "tank", "short_name", "vehicle_name"]
    mark95_keys = ["mark_95", "95", "mark3", "three_marks", "3mark", "dmg95", "damage_95"]
    mark85_keys = ["mark_85", "85", "mark2", "two_marks", "2mark", "dmg85", "damage_85"]
    mark65_keys = ["mark_65", "65", "mark1", "one_mark", "1mark", "dmg65", "damage_65"]

    def _find_key(entry: dict, candidates: list[str]) -> str | None:
        for k in candidates:
            if k in entry:
                return k
            # Case-insensitive match
            for ek in entry:
                if ek.lower() == k.lower():
                    return ek
        return None

    sample = entries[0]
    nk = _find_key(sample, name_keys)
    m95k = _find_key(sample, mark95_keys)

    if not nk or not m95k:
        return None

    m85k = _find_key(sample, mark85_keys)
    m65k = _find_key(sample, mark65_keys)

    results = []
    for entry in entries:
        name = str(entry.get(nk, "")).strip()
        mark_95 = _safe_float(entry.get(m95k, 0))
        if not name or mark_95 <= 0:
            continue

        mark_85 = _safe_float(entry.get(m85k, 0)) if m85k else mark_95 * (85.0 / 95.0)
        mark_65 = _safe_float(entry.get(m65k, 0)) if m65k else mark_95 * (65.0 / 95.0)

        results.append({
            "tank_name": name,
            "mark_65": mark_65,
            "mark_85": mark_85,
            "mark_95": mark_95,
        })
    return results if len(results) >= 10 else None


def _safe_float(value: object) -> float:
    """Convert a value to float, returning 0.0 on failure."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def parse_marks_page(html: str) -> list[dict]:
    """Parse MoE threshold data from the wotconsole.info marks page HTML.

    Tries multiple strategies:
        1. Embedded JSON in <script> tags
        2. HTML table parsing

    Returns a list of dicts with keys: tank_name, mark_65, mark_85, mark_95.
    """
    # Strategy 1: embedded JSON
    json_results = _parse_embedded_json(html)
    if json_results:
        logger.info("Parsed %d tanks from embedded JSON data", len(json_results))
        return json_results

    # Strategy 2: HTML table
    parser = _MarksTableParser()
    parser.feed(html)
    if parser.results:
        logger.info("Parsed %d tanks from HTML table", len(parser.results))
        return parser.results

    logger.warning(
        "Could not parse any tank data from wotconsole.info/marks/ — "
        "the page structure may have changed"
    )
    return []


# ---------------------------------------------------------------------------
# ThresholdProvider — main API
# ---------------------------------------------------------------------------


class ThresholdProvider:
    """Fetches and caches MoE damage thresholds for all tanks.

    On startup, call ``refresh_if_stale()`` to ensure the local cache is fresh.
    Then use ``get_by_name()`` to look up thresholds for the current tank.

    The cache is stored as a single JSON file containing every tank parsed
    from the marks page, plus metadata (fetch timestamp).

    Args:
        cache_dir: Directory for the on-disk threshold cache.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir or Path.home() / ".wot-console-overlay" / "cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        # In-memory index: {lowercase_name: MoeThresholds}
        self._name_index: dict[str, MoeThresholds] = {}
        self._all_names: list[str] = []  # for fuzzy matching
        self._fetched_at: float = 0.0
        # Load disk cache on init
        self._load_from_disk()

    @property
    def _cache_file(self) -> Path:
        return self._cache_dir / "moe_thresholds.json"

    @property
    def is_stale(self) -> bool:
        """True if the cache is older than 24 hours or empty."""
        if not self._name_index:
            return True
        return (time.time() - self._fetched_at) > CACHE_TTL_SECONDS

    @property
    def tank_count(self) -> int:
        return len(self._name_index)

    async def refresh_if_stale(
        self, session: aiohttp.ClientSession | None = None
    ) -> bool:
        """Fetch fresh data if the local cache is stale.

        Returns True if the cache is now fresh (either already was or refreshed).
        """
        if not self.is_stale:
            logger.debug(
                "Threshold cache is fresh (%d tanks, age %.0f min)",
                self.tank_count,
                (time.time() - self._fetched_at) / 60,
            )
            return True
        return await self.refresh(session)

    async def refresh(
        self, session: aiohttp.ClientSession | None = None
    ) -> bool:
        """Fetch the marks page and rebuild the local cache.

        Returns True if fetch + parse succeeded.
        """
        logger.info("Refreshing MoE thresholds from %s ...", MARKS_PAGE_URL)

        owns_session = session is None
        if owns_session:
            session = aiohttp.ClientSession()

        try:
            html = await self._fetch_page(session)
            if html is None:
                return False

            entries = parse_marks_page(html)
            if not entries:
                return False

            self._rebuild_index(entries)
            self._fetched_at = time.time()
            self._save_to_disk(entries)

            logger.info(
                "Cached MoE thresholds for %d tanks (source: wotconsole.info)",
                len(entries),
            )
            return True
        except Exception:
            logger.exception("Failed to refresh MoE thresholds")
            return False
        finally:
            if owns_session:
                await session.close()

    def get_by_name(self, tank_name: str) -> MoeThresholds | None:
        """Look up thresholds by tank name (case-insensitive, fuzzy match).

        Tries exact match first, then fuzzy match with cutoff=0.8.
        The high cutoff avoids false positives like "DBV-152" → "SU-152".
        """
        if not self._name_index:
            return None

        query = tank_name.lower().strip()

        # Exact match
        if query in self._name_index:
            return self._name_index[query]

        # Fuzzy match — cutoff=0.8 to avoid false positives from OCR noise
        matches = difflib.get_close_matches(query, self._all_names, n=1, cutoff=0.8)
        if matches:
            logger.info(
                "Fuzzy-matched '%s' → '%s' (not exact)",
                tank_name, self._name_index[matches[0]].tank_name,
            )
            return self._name_index[matches[0]]

        logger.warning("No threshold match for '%s'", tank_name)
        return None

    # --- Private helpers ---

    async def _fetch_page(self, session: aiohttp.ClientSession) -> str | None:
        """Fetch the marks page HTML."""
        try:
            async with session.get(MARKS_PAGE_URL, timeout=_FETCH_TIMEOUT) as resp:
                if resp.status != 200:
                    logger.warning(
                        "wotconsole.info returned HTTP %d", resp.status
                    )
                    return None
                return await resp.text()
        except Exception:
            logger.exception("Failed to fetch %s", MARKS_PAGE_URL)
            return None

    def _rebuild_index(self, entries: list[dict]) -> None:
        """Rebuild the in-memory name index from parsed entries."""
        self._name_index.clear()
        for entry in entries:
            thresholds = MoeThresholds.from_dict(entry)
            key = thresholds.tank_name.lower().strip()
            self._name_index[key] = thresholds
        self._all_names = list(self._name_index.keys())

    def _load_from_disk(self) -> None:
        """Load cached data from disk into memory."""
        if not self._cache_file.exists():
            return
        try:
            raw = json.loads(self._cache_file.read_text())
            meta = raw.get("_meta", {})
            self._fetched_at = meta.get("fetched_at", 0.0)
            entries = raw.get("tanks", [])
            if entries:
                self._rebuild_index(entries)
                logger.debug(
                    "Loaded %d tanks from threshold cache (age %.0f min)",
                    len(entries),
                    (time.time() - self._fetched_at) / 60,
                )
        except (json.JSONDecodeError, OSError, KeyError):
            logger.warning("Corrupt threshold cache — will re-fetch")

    def _save_to_disk(self, entries: list[dict]) -> None:
        """Persist parsed data to the JSON cache file."""
        payload = {
            "_meta": {
                "fetched_at": self._fetched_at,
                "source": MARKS_PAGE_URL,
                "tank_count": len(entries),
            },
            "tanks": entries,
        }
        try:
            self._cache_file.write_text(json.dumps(payload, indent=2))
        except OSError:
            logger.exception("Failed to write threshold cache to %s", self._cache_file)
