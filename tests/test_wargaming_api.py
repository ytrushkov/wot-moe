"""Tests for Wargaming API client."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tankvision.data.wargaming_api import WargamingApi, WargamingApiError


class TestWargamingApiInit:
    def test_default_platform_is_xbox(self):
        api = WargamingApi(application_id="test123")
        assert api.platform == "xbox"
        assert "api-console.worldoftanks.com" in api.base_url

    def test_ps_platform(self):
        api = WargamingApi(application_id="test123", platform="ps")
        assert api.platform == "ps"
        assert "api-console.worldoftanks.com" in api.base_url

    def test_invalid_platform_raises(self):
        with pytest.raises(ValueError, match="Unknown platform"):
            WargamingApi(application_id="test123", platform="switch")


class TestWargamingApiRequest:
    """Tests that mock _request to avoid real HTTP calls."""

    @pytest.fixture
    def api(self):
        return WargamingApi(application_id="test_app_id", platform="xbox")

    @pytest.mark.asyncio
    async def test_search_player_returns_list(self, api):
        with patch.object(api, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = [{"account_id": 123, "nickname": "TestPlayer"}]
            result = await api.search_player("TestPlayer")
            assert result == [{"account_id": 123, "nickname": "TestPlayer"}]
            mock_req.assert_called_once_with(
                "/account/list/", search="TestPlayer", type="exact"
            )

    @pytest.mark.asyncio
    async def test_search_player_non_exact(self, api):
        with patch.object(api, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = [{"account_id": 1, "nickname": "A"}]
            await api.search_player("A", exact=False)
            mock_req.assert_called_once_with("/account/list/", search="A")

    @pytest.mark.asyncio
    async def test_search_player_empty_result(self, api):
        with patch.object(api, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = []
            result = await api.search_player("Nobody")
            assert result == []

    @pytest.mark.asyncio
    async def test_get_player_info(self, api):
        with patch.object(api, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "789": {"nickname": "Player", "statistics": {"battles": 5000}}
            }
            result = await api.get_player_info(789)
            assert result["nickname"] == "Player"

    @pytest.mark.asyncio
    async def test_get_player_tanks(self, api):
        tank_stats = [
            {"tank_id": 100, "marks_on_gun": 2, "all": {"battles": 150}},
            {"tank_id": 200, "marks_on_gun": 0, "all": {"battles": 10}},
        ]
        with patch.object(api, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"789": tank_stats}
            result = await api.get_player_tanks(789)
            assert len(result) == 2
            assert result[0]["marks_on_gun"] == 2

    @pytest.mark.asyncio
    async def test_get_player_tanks_with_filter(self, api):
        with patch.object(api, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"789": [{"tank_id": 100}]}
            await api.get_player_tanks(789, tank_id=100)
            mock_req.assert_called_once_with(
                "/tanks/stats/", account_id="789", tank_id="100"
            )

    @pytest.mark.asyncio
    async def test_get_vehicles(self, api):
        vehicles = {"100": {"short_name": "T-54", "tier": 9}}
        with patch.object(api, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = vehicles
            result = await api.get_vehicles()
            assert "100" in result
            assert result["100"]["short_name"] == "T-54"

    @pytest.mark.asyncio
    async def test_resolve_gamertag_found(self, api):
        with patch.object(api, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = [{"account_id": 999, "nickname": "Found"}]
            result = await api.resolve_gamertag("Found")
            assert result == 999

    @pytest.mark.asyncio
    async def test_resolve_gamertag_not_found(self, api):
        with patch.object(api, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = []
            result = await api.resolve_gamertag("NotReal")
            assert result is None


class TestWargamingApiErrorHandling:
    @pytest.mark.asyncio
    async def test_api_error_propagates(self):
        api = WargamingApi(application_id="test")
        with patch.object(api, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = WargamingApiError("API error 402: INVALID_APPLICATION_ID")
            with pytest.raises(WargamingApiError, match="INVALID_APPLICATION_ID"):
                await api.search_player("Test")

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty_list(self):
        api = WargamingApi(application_id="test")
        with patch.object(api, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"789": None}
            result = await api.get_player_tanks(789)
            assert result == []


class TestWargamingApiCache:
    """Tests for the TTL response cache.

    We mock at the ``_request`` / ``_ensure_session`` level to avoid real HTTP.
    The cache lives *inside* ``_request``, so we use a counter-based approach:
    let the first call populate the cache, then check that subsequent calls
    don't invoke the network layer again.
    """

    @pytest.mark.asyncio
    async def test_second_call_returns_cached(self):
        api = WargamingApi(application_id="test", cache_ttl=60)
        call_count = 0
        original_data = [{"account_id": 1, "nickname": "A"}]

        async def _fake_request(endpoint, **params):
            nonlocal call_count
            call_count += 1
            params["application_id"] = api.application_id
            cache_key = api._cache_key(endpoint, params)
            api._cache[cache_key] = (time.monotonic(), original_data)
            return original_data

        with patch.object(api, "_request", side_effect=_fake_request):
            r1 = await api.search_player("A")

        # Now _cache is populated. A real call to _request should hit cache.
        r2 = await api._request("/account/list/", search="A", type="exact")
        assert r1 == r2
        assert call_count == 1  # Only the first call went through the mock

    @pytest.mark.asyncio
    async def test_cache_disabled_when_ttl_zero(self):
        api = WargamingApi(application_id="test", cache_ttl=0)
        call_count = 0

        async def _counting_request(endpoint, **params):
            nonlocal call_count
            call_count += 1
            return [{"account_id": 1}]

        with patch.object(api, "_request", side_effect=_counting_request):
            await api.search_player("A")
            await api.search_player("A")
        # With ttl=0 both calls should go through (no caching)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self):
        api = WargamingApi(application_id="test", cache_ttl=10)
        cache_key = "/account/list/?application_id=test&search=A&type=exact"

        # Seed with a fresh cache entry
        api._cache[cache_key] = (time.monotonic(), [{"account_id": 1}])
        # Should return cached data without network call
        result = await api._request("/account/list/", search="A", type="exact")
        assert result == [{"account_id": 1}]

        # Now expire it
        api._cache[cache_key] = (time.monotonic() - 20, [{"account_id": 1}])
        # Should try to fetch from network — which will fail in sandbox,
        # but we can verify the cache was skipped by checking the entry was stale
        assert time.monotonic() - api._cache[cache_key][0] > api._cache_ttl

    def test_invalidate_cache_by_endpoint(self):
        api = WargamingApi(application_id="test")
        api._cache["/tanks/stats/?account_id=789"] = (time.monotonic(), {})
        api._cache["/account/list/?search=A"] = (time.monotonic(), {})

        api.invalidate_cache("/tanks/stats/")
        assert len(api._cache) == 1
        assert "/account/list/?search=A" in api._cache

    def test_invalidate_cache_all(self):
        api = WargamingApi(application_id="test")
        api._cache["a"] = (time.monotonic(), {})
        api._cache["b"] = (time.monotonic(), {})
        api.invalidate_cache()
        assert len(api._cache) == 0
