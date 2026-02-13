"""MoE threshold provider: fetches and caches damage thresholds from third-party sources.

The Wargaming API does NOT expose MoE damage thresholds (the combined damage
needed to reach 65%/85%/95%). These must come from community sites that track
server population data.

Primary source: wotconsole.info/marks/
Fallback: manual entry by the user.
"""

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

# wotconsole.info marks page for scraping threshold data
WOTCONSOLE_INFO_URL = "https://wotconsole.info/marks"

# Cache thresholds for 24 hours
CACHE_TTL_SECONDS = 86400


@dataclass
class MoeThresholds:
    """Damage thresholds for a specific tank's Marks of Excellence."""

    tank_id: int
    tank_name: str
    mark_65: float  # Combined damage for 1 mark (65th percentile)
    mark_85: float  # Combined damage for 2 marks (85th percentile)
    mark_95: float  # Combined damage for 3 marks (95th percentile)
    fetched_at: float = 0.0  # Unix timestamp when data was fetched

    @property
    def is_stale(self) -> bool:
        return (time.time() - self.fetched_at) > CACHE_TTL_SECONDS

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
            "tank_id": self.tank_id,
            "tank_name": self.tank_name,
            "mark_65": self.mark_65,
            "mark_85": self.mark_85,
            "mark_95": self.mark_95,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MoeThresholds":
        return cls(
            tank_id=data["tank_id"],
            tank_name=data["tank_name"],
            mark_65=data["mark_65"],
            mark_85=data["mark_85"],
            mark_95=data["mark_95"],
            fetched_at=data.get("fetched_at", 0.0),
        )

    @classmethod
    def manual(cls, tank_id: int, tank_name: str, target_damage: float) -> "MoeThresholds":
        """Create thresholds from a single manual target value.

        When the user only knows the target for their current mark goal,
        we approximate the other thresholds proportionally.
        """
        return cls(
            tank_id=tank_id,
            tank_name=tank_name,
            mark_65=target_damage * (65 / 95),
            mark_85=target_damage * (85 / 95),
            mark_95=target_damage,
            fetched_at=time.time(),
        )


class ThresholdProvider:
    """Fetches and caches MoE damage thresholds.

    Data flow:
        1. Check in-memory cache
        2. Check on-disk JSON cache
        3. Fetch from wotconsole.info
        4. Fall back to manual entry

    Args:
        cache_dir: Directory for on-disk threshold cache.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._memory_cache: dict[int, MoeThresholds] = {}
        self._cache_dir = cache_dir or Path.home() / ".wot-console-overlay" / "cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _cache_file(self) -> Path:
        return self._cache_dir / "moe_thresholds.json"

    def get_cached(self, tank_id: int) -> MoeThresholds | None:
        """Get thresholds from cache (memory or disk) if fresh."""
        # Check memory
        if tank_id in self._memory_cache:
            thresholds = self._memory_cache[tank_id]
            if not thresholds.is_stale:
                return thresholds

        # Check disk
        disk_data = self._load_disk_cache()
        if str(tank_id) in disk_data:
            thresholds = MoeThresholds.from_dict(disk_data[str(tank_id)])
            if not thresholds.is_stale:
                self._memory_cache[tank_id] = thresholds
                return thresholds

        return None

    async def get_thresholds(
        self,
        tank_id: int,
        tank_name: str = "",
        session: aiohttp.ClientSession | None = None,
    ) -> MoeThresholds | None:
        """Get thresholds for a tank, fetching from remote if needed.

        Args:
            tank_id: Wargaming tank ID.
            tank_name: Tank name (for display and cache).
            session: Optional aiohttp session to reuse.

        Returns:
            MoeThresholds if available, None if fetch failed and no cache exists.
        """
        # Try cache first
        cached = self.get_cached(tank_id)
        if cached is not None:
            return cached

        # Fetch from remote
        thresholds = await self._fetch_from_wotconsole_info(tank_id, tank_name, session)
        if thresholds is not None:
            self._store(thresholds)
            return thresholds

        # Return stale cache if available
        disk_data = self._load_disk_cache()
        if str(tank_id) in disk_data:
            logger.warning("Using stale threshold cache for tank %d", tank_id)
            return MoeThresholds.from_dict(disk_data[str(tank_id)])

        return None

    def set_manual(self, tank_id: int, tank_name: str, target_damage: float) -> MoeThresholds:
        """Set thresholds manually (user-provided target damage)."""
        thresholds = MoeThresholds.manual(tank_id, tank_name, target_damage)
        self._store(thresholds)
        return thresholds

    async def _fetch_from_wotconsole_info(
        self,
        tank_id: int,
        tank_name: str,
        session: aiohttp.ClientSession | None = None,
    ) -> MoeThresholds | None:
        """Fetch MoE thresholds from wotconsole.info.

        This is a placeholder for the actual scraping logic. The site structure
        may change, so this needs to be adaptable.
        """
        owns_session = session is None
        if owns_session:
            session = aiohttp.ClientSession()

        try:
            url = f"{WOTCONSOLE_INFO_URL}/{tank_id}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning(
                        "wotconsole.info returned %d for tank %d", resp.status, tank_id
                    )
                    return None

                text = await resp.text()
                return self._parse_wotconsole_info(tank_id, tank_name, text)
        except Exception:
            logger.exception("Failed to fetch thresholds from wotconsole.info for tank %d", tank_id)
            return None
        finally:
            if owns_session:
                await session.close()

    def _parse_wotconsole_info(
        self, tank_id: int, tank_name: str, html: str
    ) -> MoeThresholds | None:
        """Parse MoE threshold data from wotconsole.info HTML.

        This is a best-effort parser. The site may change its structure.
        Returns None if parsing fails.
        """
        # Placeholder: actual parsing depends on the site's HTML structure.
        # In practice, look for damage values associated with 65%, 85%, 95% marks.
        # For now, return None to trigger manual fallback.
        logger.debug("wotconsole.info parsing not yet implemented for tank %d", tank_id)
        return None

    def _store(self, thresholds: MoeThresholds) -> None:
        """Store thresholds in both memory and disk cache."""
        self._memory_cache[thresholds.tank_id] = thresholds

        disk_data = self._load_disk_cache()
        disk_data[str(thresholds.tank_id)] = thresholds.to_dict()
        self._save_disk_cache(disk_data)

    def _load_disk_cache(self) -> dict:
        if self._cache_file.exists():
            try:
                return json.loads(self._cache_file.read_text())
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupt threshold cache, ignoring")
        return {}

    def _save_disk_cache(self, data: dict) -> None:
        try:
            self._cache_file.write_text(json.dumps(data, indent=2))
        except OSError:
            logger.exception("Failed to write threshold cache")
