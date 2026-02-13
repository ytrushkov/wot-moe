"""Entry point for WoT Console Overlay application."""

import asyncio
import logging
import signal
import sys

from tankvision.calculation.moe_calculator import MoeCalculator
from tankvision.capture.screen_capture import ScreenCapture
from tankvision.config import load_config
from tankvision.data.session_store import SessionStore
from tankvision.data.threshold_provider import ThresholdProvider
from tankvision.data.wargaming_api import DEFAULT_APP_ID, TankSnapshot, WargamingApi
from tankvision.ocr.ocr_pipeline import OcrPipeline
from tankvision.server.websocket_server import MoeWebSocketServer

logger = logging.getLogger("tankvision")

# How many times (and with what backoff) to poll the API after a battle ends.
# WG API updates can lag 30-60s behind the actual game.
_API_POLL_ATTEMPTS = 6
_API_POLL_BASE_DELAY = 5.0  # seconds


async def _resolve_startup_data(config: dict, api: WargamingApi) -> dict:
    """Resolve player identity and active tank from the API.

    Returns a dict with keys: account_id, tank_id, tank_name, marks_on_gun,
    api_snapshot. All values default to None/0 on failure so the app degrades
    gracefully to offline mode.
    """
    result: dict = {
        "account_id": None,
        "tank_id": 0,
        "tank_name": "",
        "marks_on_gun": 0,
        "api_snapshot": None,
    }

    gamertag = config["player"]["gamertag"]
    if not gamertag:
        logger.info("No gamertag configured — running in offline mode")
        return result

    try:
        account_id = await api.resolve_gamertag(gamertag)
    except Exception:
        logger.exception("Failed to resolve gamertag '%s'", gamertag)
        return result

    if account_id is None:
        logger.warning("Gamertag '%s' not found on %s", gamertag, config["player"]["platform"])
        return result

    result["account_id"] = account_id
    logger.info("Resolved '%s' → account_id=%d", gamertag, account_id)

    # Detect most recently played tank
    try:
        tank_data = await api.detect_active_tank(account_id)
    except Exception:
        logger.exception("Failed to detect active tank")
        return result

    if tank_data is None:
        logger.warning("No tank data found for account %d", account_id)
        return result

    tank_id = tank_data.get("tank_id", 0)
    marks_on_gun = tank_data.get("marks_on_gun", 0)
    result["tank_id"] = tank_id
    result["marks_on_gun"] = marks_on_gun

    # Look up tank name from encyclopedia
    try:
        vehicles = await api.get_vehicles(tank_id)
        vehicle_info = vehicles.get(str(tank_id), {})
        result["tank_name"] = vehicle_info.get("short_name", f"Tank #{tank_id}")
    except Exception:
        logger.warning("Failed to fetch vehicle name for tank_id=%d", tank_id)
        result["tank_name"] = f"Tank #{tank_id}"

    # Take an API snapshot for post-battle correction
    try:
        result["api_snapshot"] = await api.get_tank_snapshot(account_id, tank_id)
    except Exception:
        logger.warning("Failed to take initial API snapshot")

    logger.info(
        "Active tank: %s (id=%d, marks=%d, battles=%d)",
        result["tank_name"],
        tank_id,
        marks_on_gun,
        result["api_snapshot"].battles if result["api_snapshot"] else 0,
    )

    return result


async def _resolve_target_damage(
    config: dict,
    tank_id: int,
    tank_name: str,
    marks_on_gun: int,
    threshold_provider: ThresholdProvider,
) -> int:
    """Determine the target damage threshold for MoE calculation.

    Priority: ThresholdProvider (cached/remote) > config fallback.
    """
    if tank_id:
        thresholds = await threshold_provider.get_thresholds(tank_id, tank_name)
        if thresholds:
            next_mark = min(marks_on_gun + 1, 3)
            target = int(thresholds.target_for_mark(next_mark))
            logger.info("Threshold for %d-mark: %d (from provider)", next_mark, target)
            return target

    target = config["moe"]["target_damage"]
    if target > 0:
        logger.info("Using config target_damage=%d", target)
    return target


async def _poll_api_correction(
    api: WargamingApi,
    calculator: MoeCalculator,
    store: SessionStore,
    account_id: int,
    tank_id: int,
    session_id: int | None,
    before: TankSnapshot,
    server: MoeWebSocketServer,
) -> TankSnapshot:
    """Background task: poll API after battle ends and correct EMA with real data.

    The in-game HUD shows combined assisted damage (tracking + spotting), but
    WG's MoE formula uses max(tracking, spotting). Our OCR-based estimate is
    therefore inflated. After the API processes the battle, we compute the
    per-battle delta from cumulative stats to get the server-side value.

    Returns the updated TankSnapshot for use as the next "before" baseline.
    """
    for attempt in range(_API_POLL_ATTEMPTS):
        delay = _API_POLL_BASE_DELAY * (2**attempt)
        await asyncio.sleep(delay)

        try:
            after = await api.get_tank_snapshot(account_id, tank_id)
        except Exception:
            logger.warning("API poll attempt %d failed", attempt + 1)
            continue

        if after is None:
            continue

        if after.battles <= before.battles:
            logger.debug(
                "API not yet updated (battles: %d, expected >%d), retrying...",
                after.battles,
                before.battles,
            )
            continue

        # API has processed the battle
        delta = before.battle_delta(after)
        if delta is not None:
            # Single new battle — correct with precise damage
            logger.info(
                "API correction: ocr_combined=%d, api_combined=%d "
                "(dealt=%d + assisted=%d)",
                calculator._last_battle_damage,
                delta.combined,
                delta.damage_dealt,
                delta.damage_assisted,
            )
            corrected_state = calculator.correct_last_battle(delta.combined)

            if corrected_state:
                await server.broadcast(corrected_state)

            if delta.marks_changed:
                logger.info(
                    "Mark changed: %d → %d!",
                    delta.marks_on_gun_before,
                    delta.marks_on_gun_after,
                )
        else:
            # Multiple battles processed at once — can't isolate one
            logger.info(
                "Multiple battles processed by API (%d → %d), skipping correction",
                before.battles,
                after.battles,
            )

        # Persist corrected state
        store.save_ema(tank_id, calculator.ema, calculator.current_moe)
        if session_id is not None:
            store.update_session(
                session_id,
                end_moe=calculator.current_moe,
                end_ema=calculator.ema,
                battles=calculator.battles_this_session,
            )

        return after

    logger.warning("API correction timed out after %d attempts", _API_POLL_ATTEMPTS)
    return before


async def run(config_path: str = "config.toml") -> None:
    config = load_config(config_path)

    # --- Data layer ---
    app_id = config["api"]["application_id"] or DEFAULT_APP_ID
    platform = config["player"]["platform"]

    api = WargamingApi(application_id=app_id, platform=platform)
    threshold_provider = ThresholdProvider()
    store = SessionStore()

    # --- Startup: resolve player & tank ---
    startup = await _resolve_startup_data(config, api)
    account_id = startup["account_id"]
    tank_id: int = startup["tank_id"]
    tank_name: str = startup["tank_name"]
    api_snapshot: TankSnapshot | None = startup["api_snapshot"]

    # --- Determine MoE parameters ---
    target_damage = await _resolve_target_damage(
        config,
        tank_id,
        tank_name,
        startup["marks_on_gun"],
        threshold_provider,
    )

    # Try to restore persisted EMA state
    current_moe = config["moe"]["current_moe_percent"]
    if tank_id:
        ema_snapshot = store.load_ema(tank_id)
        if ema_snapshot:
            current_moe = ema_snapshot.moe_percent
            logger.info("Restored persisted MoE: %.2f%%", current_moe)

    # --- Build pipeline components ---
    capture = ScreenCapture(
        roi=(
            config["ocr"]["roi_x"],
            config["ocr"]["roi_y"],
            config["ocr"]["roi_width"],
            config["ocr"]["roi_height"],
        ),
        sample_rate=config["ocr"]["sample_rate"],
    )

    ocr = OcrPipeline(
        confidence_threshold=config["ocr"]["confidence_threshold"],
    )

    calculator = MoeCalculator(
        current_moe=current_moe,
        target_damage=target_damage,
        ema_alpha=config["moe"]["ema_alpha"],
        tank_name=tank_name,
    )

    server = MoeWebSocketServer(
        ws_port=config["server"]["ws_port"],
        http_port=config["server"]["http_port"],
    )

    # --- Session tracking ---
    session_id: int | None = None
    if tank_id:
        session_id = store.start_session(tank_id, tank_name, current_moe, calculator.ema)

    # --- Signal handling ---
    stop_event = asyncio.Event()

    def handle_signal() -> None:
        logger.info("Shutting down...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            pass  # Windows

    # --- Start servers ---
    await server.start()

    mode = "online" if account_id else "offline"
    logger.info(
        "WoT Console Overlay running (%s mode). "
        "Add Browser Source in OBS: http://localhost:%d",
        mode,
        config["server"]["http_port"],
    )

    # --- Main loop ---
    correction_task: asyncio.Task | None = None

    try:
        while not stop_event.is_set():
            frame = capture.grab_frame()
            if frame is not None:
                damage_values = ocr.process_frame(frame)
                if damage_values is not None:
                    state = calculator.update(damage_values)

                    if state.status == "battle_ended":
                        # Persist estimated state immediately
                        if tank_id:
                            ema_pre = calculator._ema_before_last_battle or 0.0
                            store.save_ema(tank_id, calculator.ema, calculator.current_moe)
                            store.log_battle(
                                session_id=session_id,
                                tank_id=tank_id,
                                direct_damage=state.direct_damage,
                                assisted_damage=state.assisted_damage,
                                combined_damage=state.combined_damage,
                                ema_before=ema_pre,
                                ema_after=calculator.ema,
                                moe_before=calculator._ema_to_moe(ema_pre),
                                moe_after=calculator.current_moe,
                            )

                        # Launch background API correction (if online)
                        if account_id and tank_id and api_snapshot:
                            if correction_task and not correction_task.done():
                                correction_task.cancel()

                            snapshot_before = api_snapshot

                            async def _do_correction(snap: TankSnapshot = snapshot_before) -> None:
                                nonlocal api_snapshot
                                api_snapshot = await _poll_api_correction(
                                    api,
                                    calculator,
                                    store,
                                    account_id,
                                    tank_id,
                                    session_id,
                                    snap,
                                    server,
                                )

                            correction_task = asyncio.create_task(_do_correction())

                    await server.broadcast(state)

            await asyncio.sleep(1.0 / config["ocr"]["sample_rate"])
    finally:
        # Cancel any pending correction
        if correction_task and not correction_task.done():
            correction_task.cancel()
            try:
                await correction_task
            except asyncio.CancelledError:
                pass

        # Finalize session
        if session_id is not None:
            store.update_session(
                session_id,
                end_moe=calculator.current_moe,
                end_ema=calculator.ema,
                battles=calculator.battles_this_session,
            )

        # Clean up resources
        await server.stop()
        await api.close()
        store.close()
        capture.close()
        logger.info("Shutdown complete")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.toml"
    asyncio.run(run(config_path))


if __name__ == "__main__":
    main()
