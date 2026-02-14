"""Tests for Wargaming API client."""

from unittest.mock import AsyncMock, patch

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
