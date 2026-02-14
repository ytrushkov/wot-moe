"""Integration tests for Wargaming API — hits the live WoTC endpoint.

Run with:  pytest tests/test_wargaming_api_integration.py -v
Skip in CI by default via the ``integration`` marker.
"""

import pytest
import pytest_asyncio

from tankvision.data.wargaming_api import DEFAULT_APP_ID, TankSnapshot, WargamingApi

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

GAMERTAG = "YURTRush-x"
PLATFORM = "xbox"


@pytest_asyncio.fixture
async def api():
    client = WargamingApi(application_id=DEFAULT_APP_ID, platform=PLATFORM)
    yield client
    await client.close()


# ------------------------------------------------------------------
# resolve_gamertag / search_player
# ------------------------------------------------------------------


async def test_resolve_gamertag_returns_account_id(api: WargamingApi):
    account_id = await api.resolve_gamertag(GAMERTAG)
    assert account_id is not None, f"Gamertag '{GAMERTAG}' should resolve to an account_id"
    assert isinstance(account_id, int)
    assert account_id > 0


async def test_search_player_exact_match(api: WargamingApi):
    results = await api.search_player(GAMERTAG, exact=True)
    assert len(results) == 1
    assert results[0]["nickname"].lower() == GAMERTAG.lower()
    assert "account_id" in results[0]


async def test_search_player_non_exact(api: WargamingApi):
    # Prefix search using the stem (without "-x") to exercise non-exact mode
    results = await api.search_player("YURTRush", exact=False)
    assert len(results) >= 1
    nicknames = [r["nickname"].lower() for r in results]
    assert GAMERTAG.lower() in nicknames


async def test_resolve_nonexistent_gamertag(api: WargamingApi):
    result = await api.resolve_gamertag("zzzNobodyHasThisTag999xyzxyz")
    assert result is None


# ------------------------------------------------------------------
# get_player_info
# ------------------------------------------------------------------


async def test_get_player_info(api: WargamingApi):
    account_id = await api.resolve_gamertag(GAMERTAG)
    assert account_id is not None

    info = await api.get_player_info(account_id)
    assert info, "Player info should not be empty"
    assert info.get("nickname", "").lower() == GAMERTAG.lower()


# ------------------------------------------------------------------
# get_player_tanks
# ------------------------------------------------------------------


async def test_get_player_tanks_returns_list(api: WargamingApi):
    account_id = await api.resolve_gamertag(GAMERTAG)
    assert account_id is not None

    tanks = await api.get_player_tanks(account_id)
    assert isinstance(tanks, list)
    assert len(tanks) > 0, "Player should have at least one tank"

    first = tanks[0]
    assert "tank_id" in first
    assert "all" in first


async def test_get_player_tanks_filter_by_id(api: WargamingApi):
    account_id = await api.resolve_gamertag(GAMERTAG)
    assert account_id is not None

    all_tanks = await api.get_player_tanks(account_id)
    assert len(all_tanks) > 0

    target_tank_id = all_tanks[0]["tank_id"]
    filtered = await api.get_player_tanks(account_id, tank_id=target_tank_id)
    assert len(filtered) == 1
    assert filtered[0]["tank_id"] == target_tank_id


# ------------------------------------------------------------------
# get_vehicles (encyclopedia)
# ------------------------------------------------------------------


async def test_get_vehicles_returns_catalog(api: WargamingApi):
    vehicles = await api.get_vehicles()
    assert isinstance(vehicles, dict)
    assert len(vehicles) > 0, "Encyclopedia should contain vehicles"

    sample_id, sample = next(iter(vehicles.items()))
    assert "short_name" in sample or "name" in sample


async def test_get_vehicles_by_id(api: WargamingApi):
    account_id = await api.resolve_gamertag(GAMERTAG)
    assert account_id is not None

    tanks = await api.get_player_tanks(account_id)
    tank_id = tanks[0]["tank_id"]

    vehicles = await api.get_vehicles(tank_id)
    assert str(tank_id) in vehicles
    vehicle = vehicles[str(tank_id)]
    assert "short_name" in vehicle or "name" in vehicle


# ------------------------------------------------------------------
# detect_active_tank
# ------------------------------------------------------------------


async def test_detect_active_tank(api: WargamingApi):
    account_id = await api.resolve_gamertag(GAMERTAG)
    assert account_id is not None

    tank = await api.detect_active_tank(account_id)
    assert tank is not None, "Player should have a most-recently-played tank"
    assert "tank_id" in tank
    assert "last_battle_time" in tank
    assert tank["last_battle_time"] > 0


# ------------------------------------------------------------------
# get_tank_snapshot
# ------------------------------------------------------------------


async def test_get_tank_snapshot(api: WargamingApi):
    account_id = await api.resolve_gamertag(GAMERTAG)
    assert account_id is not None

    active = await api.detect_active_tank(account_id)
    assert active is not None
    tank_id = active["tank_id"]

    snapshot = await api.get_tank_snapshot(account_id, tank_id)
    assert snapshot is not None
    assert isinstance(snapshot, TankSnapshot)
    assert snapshot.tank_id == tank_id
    assert snapshot.battles > 0
    assert snapshot.damage_dealt > 0


async def test_get_tank_snapshot_nonexistent(api: WargamingApi):
    account_id = await api.resolve_gamertag(GAMERTAG)
    assert account_id is not None

    snapshot = await api.get_tank_snapshot(account_id, tank_id=999999999)
    assert snapshot is None


# ------------------------------------------------------------------
# Full startup flow (mirrors __main__._resolve_startup_data)
# ------------------------------------------------------------------


async def test_full_startup_flow(api: WargamingApi):
    """End-to-end: resolve gamertag -> detect tank -> look up name -> snapshot."""
    account_id = await api.resolve_gamertag(GAMERTAG)
    assert account_id is not None

    active = await api.detect_active_tank(account_id)
    assert active is not None
    tank_id = active["tank_id"]

    vehicles = await api.get_vehicles(tank_id)
    vehicle_info = vehicles.get(str(tank_id), {})
    tank_name = vehicle_info.get("short_name", "")
    assert tank_name, f"Should resolve a tank name for tank_id={tank_id}"

    snapshot = await api.get_tank_snapshot(account_id, tank_id)
    assert snapshot is not None
    assert snapshot.battles > 0

    print(
        f"\nStartup flow OK: {GAMERTAG} -> account {account_id}, "
        f"active tank: {tank_name} (id={tank_id}, marks={active.get('marks_on_gun', '?')}, "
        f"battles={snapshot.battles})"
    )
