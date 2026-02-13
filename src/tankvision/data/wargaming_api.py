"""Wargaming API client for World of Tanks Console (WOTX)."""

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Base URLs per platform
BASE_URLS = {
    "xbox": "https://api-xbox-console.worldoftanks.com/wotx",
    "ps": "https://api-ps4-console.worldoftanks.com/wotx",
}

# Demo application_id for development/testing.
# Users should register their own at https://developers.wargaming.net/
DEMO_APP_ID = "demo"


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
        application_id: str = DEMO_APP_ID,
        platform: str = "xbox",
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        if platform not in BASE_URLS:
            raise ValueError(f"Unknown platform: {platform!r}. Must be 'xbox' or 'ps'.")
        self.application_id = application_id
        self.platform = platform
        self.base_url = BASE_URLS[platform]
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
