"""Wargaming API client for World of Tanks Console (WoTC)."""

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Unified WoT Console API base URL (post-crossplay).
# The old per-platform endpoints (api-xbox-console / api-ps4-console) with the
# /wotx path prefix were deprecated when Wargaming introduced cross-platform
# play.  The unified endpoint uses /wotc for all console platforms.
BASE_URL = "https://api-console.worldoftanks.com/wotc"

# Default application_id shipped with TankVision.
# Users can override this in config.toml [api] section.
# This is a public app identifier, not a secret.
DEFAULT_APP_ID = "4d5d0fe3b7b665ff721b824118775030"


class WargamingApiError(Exception):
    """Raised when the Wargaming API returns an error."""


class WargamingApi:
    """Async client for the Wargaming WoT Console API.

    Args:
        application_id: API key from developers.wargaming.net. Use "demo" for testing.
        platform: "xbox" or "ps".
        session: Optional aiohttp session to reuse. If None, creates one internally.
    """

    def __init__(
        self,
        application_id: str = DEFAULT_APP_ID,
        platform: str = "xbox",
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        if platform not in ("xbox", "ps"):
            raise ValueError(f"Unknown platform: {platform!r}. Must be 'xbox' or 'ps'.")
        self.application_id = application_id
        self.platform = platform
        self.base_url = BASE_URL
        self._session = session
        self._owns_session = session is None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, endpoint: str, **params: Any) -> dict:
        """Make a GET request to the Wargaming API.

        Args:
            endpoint: API path (e.g., "/account/list/").
            **params: Query parameters.

        Returns:
            The "data" field from the API response.

        Raises:
            WargamingApiError: If the API returns an error status.
        """
        session = await self._ensure_session()
        params["application_id"] = self.application_id
        url = f"{self.base_url}{endpoint}"

        async with session.get(url, params=params) as resp:
            body = await resp.json()

        if body.get("status") != "ok":
            error = body.get("error", {})
            msg = error.get("message", "Unknown API error")
            code = error.get("code", 0)
            raise WargamingApiError(f"API error {code}: {msg}")

        return body.get("data", {})

    # --- Player endpoints ---

    async def search_player(self, gamertag: str, exact: bool = True) -> list[dict]:
        """Search for a player by gamertag.

        Args:
            gamertag: Player name to search for.
            exact: If True, search for exact match only.

        Returns:
            List of dicts with "account_id" and "nickname" keys.
        """
        params = {"search": gamertag}
        if exact:
            params["type"] = "exact"
        data = await self._request("/account/list/", **params)
        return data if isinstance(data, list) else []

    async def get_player_info(self, account_id: int) -> dict:
        """Get detailed player information.

        Args:
            account_id: Player's numeric account ID.

        Returns:
            Player info dict with statistics, nickname, etc.
        """
        data = await self._request("/account/info/", account_id=str(account_id))
        return data.get(str(account_id), {})

    # --- Tank statistics ---

    async def get_player_tanks(
        self, account_id: int, tank_id: int | None = None
    ) -> list[dict]:
        """Get per-tank statistics for a player.

        Args:
            account_id: Player's numeric account ID.
            tank_id: Optional specific tank ID to filter.

        Returns:
            List of tank stat dicts with marks_on_gun, battles, etc.
        """
        params: dict[str, str] = {"account_id": str(account_id)}
        if tank_id is not None:
            params["tank_id"] = str(tank_id)
        data = await self._request("/tanks/stats/", **params)
        return data.get(str(account_id), []) or []

    # --- Tank encyclopedia ---

    async def get_vehicles(self, tank_id: int | None = None) -> dict[str, dict]:
        """Get tank encyclopedia data.

        Args:
            tank_id: Optional specific tank ID.

        Returns:
            Dict mapping tank_id (as string) to vehicle info dicts
            containing name, short_name, tier, type, nation, etc.
        """
        params: dict[str, str] = {}
        if tank_id is not None:
            params["tank_id"] = str(tank_id)
        data = await self._request("/encyclopedia/vehicles/", **params)
        return data if isinstance(data, dict) else {}

    async def resolve_gamertag(self, gamertag: str) -> int | None:
        """Look up an account_id from a gamertag.

        Returns:
            The account_id, or None if not found.
        """
        results = await self.search_player(gamertag, exact=True)
        if results:
            return results[0].get("account_id")
        return None

    async def get_tank_snapshot(self, account_id: int, tank_id: int) -> "TankSnapshot | None":
        """Get a snapshot of a tank's cumulative stats for post-battle comparison.

        Returns:
            TankSnapshot with current cumulative stats, or None if not found.
        """
        tanks = await self.get_player_tanks(account_id, tank_id)
        if not tanks:
            return None
        t = tanks[0]
        all_stats = t.get("all", {})
        return TankSnapshot(
            tank_id=t.get("tank_id", tank_id),
            battles=all_stats.get("battles", 0),
            marks_on_gun=t.get("marks_on_gun", 0),
            damage_dealt=all_stats.get("damage_dealt", 0),
            damage_assisted=all_stats.get("damage_assisted", 0),
        )

    async def detect_active_tank(self, account_id: int) -> dict | None:
        """Find the most recently played tank for a player.

        Returns:
            The tank stats dict for the most recently played tank, or None.
        """
        tanks = await self.get_player_tanks(account_id)
        if not tanks:
            return None
        # Sort by last_battle_time descending; fall back to 0 if missing
        tanks.sort(key=lambda t: t.get("last_battle_time", 0), reverse=True)
        return tanks[0]


@dataclass
class TankSnapshot:
    """Cumulative stats snapshot for a specific tank, used for post-battle correction."""

    tank_id: int
    battles: int
    marks_on_gun: int
    damage_dealt: int
    damage_assisted: int

    def battle_delta(self, after: "TankSnapshot") -> "BattleDelta | None":
        """Compute per-battle damage delta between this snapshot and a later one.

        Returns:
            BattleDelta if exactly one new battle was played, None otherwise.
        """
        battles_diff = after.battles - self.battles
        if battles_diff != 1:
            return None
        return BattleDelta(
            damage_dealt=after.damage_dealt - self.damage_dealt,
            damage_assisted=after.damage_assisted - self.damage_assisted,
            marks_on_gun_before=self.marks_on_gun,
            marks_on_gun_after=after.marks_on_gun,
        )


@dataclass
class BattleDelta:
    """Per-battle damage values computed from API cumulative stat deltas.

    The damage_assisted here reflects WG's server-side calculation, which uses
    max(tracking, spotting) rather than the sum shown on the in-game HUD.
    This makes it more accurate than our OCR-derived estimate.
    """

    damage_dealt: int
    damage_assisted: int
    marks_on_gun_before: int
    marks_on_gun_after: int

    @property
    def combined(self) -> int:
        return self.damage_dealt + self.damage_assisted

    @property
    def marks_changed(self) -> bool:
        return self.marks_on_gun_after != self.marks_on_gun_before
