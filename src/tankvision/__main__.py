"""Entry point for WoT Console Overlay application."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time

from tankvision.calculation.moe_calculator import MoeCalculator
from tankvision.capture.screen_capture import ScreenCapture
from tankvision.config import load_config
from tankvision.data.session_store import SessionStore
from tankvision.data.threshold_provider import ThresholdProvider
from tankvision.data.wargaming_api import DEFAULT_APP_ID, TankSnapshot, WargamingApi
from tankvision.ocr.garage_detector import GarageDetector, build_vehicle_lookup
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

        # Must bypass cache — we're waiting for fresh post-battle data
        api.invalidate_cache("/tanks/stats/")
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


def _garage_enabled(config: dict) -> bool:
    """Return True if the garage ROI is configured (non-zero dimensions)."""
    g = config["garage"]
    return g["roi_width"] > 0 and g["roi_height"] > 0


async def _handle_tank_switch(
    new_tank_id: int,
    new_tank_name: str,
    *,
    api: WargamingApi,
    calculator: MoeCalculator,
    store: SessionStore,
    threshold_provider: ThresholdProvider,
    server: MoeWebSocketServer,
    config: dict,
    account_id: int | None,
    old_session_id: int | None,
) -> tuple[int, str, int | None, TankSnapshot | None]:
    """Update all tracking state when the user switches tanks in the garage.

    Returns:
        (tank_id, tank_name, new_session_id, api_snapshot)
    """
    # Finalize old session
    if old_session_id is not None:
        store.update_session(
            old_session_id,
            end_moe=calculator.current_moe,
            end_ema=calculator.ema,
            battles=calculator.battles_this_session,
        )

    # Fetch marks and target damage for the new tank
    marks_on_gun = 0
    api_snapshot: TankSnapshot | None = None
    if account_id:
        try:
            tanks = await api.get_player_tanks(account_id, tank_id=new_tank_id)
            if tanks:
                marks_on_gun = tanks[0].get("marks_on_gun", 0)
        except Exception:
            logger.warning("Failed to fetch marks for tank %d", new_tank_id)

        try:
            api_snapshot = await api.get_tank_snapshot(account_id, new_tank_id)
        except Exception:
            logger.warning("Failed to take API snapshot for tank %d", new_tank_id)

    target_damage = await _resolve_target_damage(
        config, new_tank_id, new_tank_name, marks_on_gun, threshold_provider,
    )

    # Restore persisted EMA or start fresh
    current_moe = 0.0
    ema_snap = store.load_ema(new_tank_id)
    if ema_snap:
        current_moe = ema_snap.moe_percent
        logger.info("Restored MoE for %s: %.2f%%", new_tank_name, current_moe)

    calculator.set_tank(new_tank_name, target_damage, current_moe)

    # Start new session
    new_session_id = store.start_session(
        new_tank_id, new_tank_name, current_moe, calculator.ema,
    )

    # Broadcast the switch immediately
    state = calculator._build_state("idle")
    await server.broadcast(state)

    logger.info(
        "Switched to %s (id=%d, marks=%d, target=%d)",
        new_tank_name, new_tank_id, marks_on_gun, target_damage,
    )
    return new_tank_id, new_tank_name, new_session_id, api_snapshot


# TYPE_CHECKING-style import to avoid hard dependency on PyQt6
# for the headless path. The actual import happens at runtime in tray mode.
try:
    from tankvision.tray.state_bridge import AppStateBridge
except ImportError:  # PyQt6 not installed
    AppStateBridge = None  # type: ignore[misc,assignment]


class _BridgeLogHandler(logging.Handler):
    """Forwards log records to the tray UI via the bridge."""

    def __init__(self, bridge: AppStateBridge) -> None:  # type: ignore[type-arg]
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._bridge.publish_log(msg)
        except Exception:
            self.handleError(record)


def _install_bridge_log_handler(bridge: AppStateBridge) -> None:  # type: ignore[type-arg]
    handler = _BridgeLogHandler(bridge)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    logging.getLogger("tankvision").addHandler(handler)


def _publish_tray_state(
    bridge: AppStateBridge,  # type: ignore[type-arg]
    status: str,
    tank_name: str,
    calculator: MoeCalculator,
    *,
    frame,
    ocr_text: str,
    ocr_confidence: float,
    sample_rate_actual: float,
) -> None:
    """Build an AppSnapshot and publish it to the tray UI."""
    from tankvision.tray.state_bridge import AppSnapshot

    bridge.publish_state(AppSnapshot(
        status=status,
        tank_name=tank_name,
        moe_percent=calculator.current_moe,
        battles_this_session=calculator.battles_this_session,
        last_frame=frame,
        last_ocr_text=ocr_text,
        last_confidence=ocr_confidence,
        sample_rate_actual=sample_rate_actual,
    ))


def _apply_config_changes(
    changes: dict[str, object],
    config: dict,
    ocr: OcrPipeline,
    current_sample_rate: float,
) -> float:
    """Apply runtime config changes from the tray settings dialog.

    Returns the (potentially updated) sample rate.
    """
    sample_rate = current_sample_rate

    for key, value in changes.items():
        section, name = key.split(".", 1)
        logger.info("Config change: [%s] %s = %r", section, name, value)
        config.setdefault(section, {})[name] = value

        if key == "ocr.sample_rate":
            sample_rate = float(value)  # type: ignore[arg-type]
        elif key == "ocr.confidence_threshold":
            ocr.matcher.confidence_threshold = float(value)  # type: ignore[arg-type]
        elif key in ("player.gamertag", "player.platform"):
            logger.warning(
                "Changing %s requires a restart to take effect.", key,
            )

    return sample_rate


async def run(
    config_path: str = "config.toml",
    bridge: AppStateBridge | None = None,
) -> None:
    """Run the main capture/OCR/broadcast loop.

    Args:
        config_path: Path to the TOML config file.
        bridge: Optional cross-thread bridge for the system tray UI.
                When None, the app runs in headless CLI mode (original behavior).
    """
    # Attach a log handler that forwards to the tray UI
    if bridge is not None:
        _install_bridge_log_handler(bridge)

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
    ocr_roi = (
        config["ocr"]["roi_x"],
        config["ocr"]["roi_y"],
        config["ocr"]["roi_width"],
        config["ocr"]["roi_height"],
    )
    capture = ScreenCapture(roi=ocr_roi, sample_rate=config["ocr"]["sample_rate"])

    # Log the capture region so the user knows what's being OCR'd
    logger.info(
        "OCR capture region: %dx%d at (%d, %d)",
        ocr_roi[2], ocr_roi[3], ocr_roi[0], ocr_roi[1],
    )
    if ocr_roi == (0, 0, 300, 100):
        logger.warning(
            "OCR region is at default values — damage numbers probably won't be detected. "
            "Run `tankvision --calibrate ocr` to select the damage number area on screen."
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

    # --- Garage detector (optional) ---
    garage_detector: GarageDetector | None = None
    if _garage_enabled(config):
        try:
            vehicles = await api.get_vehicles()
            vehicle_lookup = build_vehicle_lookup(vehicles)
            g = config["garage"]
            garage_detector = GarageDetector(
                roi=(g["roi_x"], g["roi_y"], g["roi_width"], g["roi_height"]),
                vehicle_lookup=vehicle_lookup,
            )
            logger.info(
                "Garage detection enabled (ROI: %dx%d at %d,%d, %d vehicles loaded)",
                g["roi_width"], g["roi_height"], g["roi_x"], g["roi_y"],
                len(vehicle_lookup),
            )
        except Exception:
            logger.exception("Failed to initialize garage detector — falling back to API")

    # --- Session tracking ---
    session_id: int | None = None
    if tank_id:
        session_id = store.start_session(tank_id, tank_name, current_moe, calculator.ema)

    # --- Signal handling ---
    stop_event = asyncio.Event()

    def handle_signal() -> None:
        logger.info("Shutting down...")
        stop_event.set()

    # When running under the tray UI, Qt owns the main thread and signal
    # handlers.  We rely on bridge.should_stop instead.
    if bridge is None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, handle_signal)
            except NotImplementedError:
                pass  # Windows

    def _should_stop() -> bool:
        if stop_event.is_set():
            return True
        if bridge is not None and bridge.should_stop:
            return True
        return False

    # --- Start servers ---
    await server.start()

    mode = "online" if account_id else "offline"
    logger.info(
        "WoT Console Overlay running (%s mode). "
        "Add Browser Source in OBS: http://localhost:%d",
        mode,
        config["server"]["http_port"],
    )

    # --- Background garage polling ---
    async def _garage_poll_loop() -> None:
        nonlocal tank_id, tank_name, session_id, api_snapshot
        assert garage_detector is not None
        interval = config["garage"]["poll_interval"]
        while not _should_stop():
            await asyncio.sleep(interval)
            try:
                switch = garage_detector.detect_switch()
            except Exception:
                logger.exception("Garage poll error")
                continue
            if switch is None:
                continue
            new_tank_id, new_tank_name = switch
            tank_id, tank_name, session_id, api_snapshot = await _handle_tank_switch(
                new_tank_id,
                new_tank_name,
                api=api,
                calculator=calculator,
                store=store,
                threshold_provider=threshold_provider,
                server=server,
                config=config,
                account_id=account_id,
                old_session_id=session_id,
            )

    garage_task: asyncio.Task | None = None
    if garage_detector is not None:
        garage_task = asyncio.create_task(_garage_poll_loop())

    # --- Main loop ---
    correction_task: asyncio.Task | None = None
    sample_rate = config["ocr"]["sample_rate"]

    try:
        while not _should_stop():
            # --- Pause support (tray only) ---
            if bridge is not None and bridge.is_paused:
                _publish_tray_state(
                    bridge, "paused", tank_name, calculator, frame=None,
                    ocr_text="", ocr_confidence=0.0, sample_rate_actual=0.0,
                )
                await asyncio.sleep(0.5)
                continue

            # --- Apply runtime config changes (tray only) ---
            if bridge is not None:
                changes = bridge.pop_config_changes()
                if changes:
                    sample_rate = _apply_config_changes(
                        changes, config, ocr, sample_rate,
                    )

            loop_start = time.monotonic()

            frame = capture.grab_frame()
            if frame is not None:
                # Use detailed OCR when the preview window is open
                ocr_text = ""
                ocr_confidence = 0.0
                if bridge is not None and bridge.ocr_preview_active:
                    detailed = ocr.process_frame_detailed(frame)
                    damage_values = detailed.reading
                    ocr_text = str(detailed.reading.combined) if detailed.reading else ""
                    ocr_confidence = detailed.overall_confidence
                else:
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

                # Publish state to tray UI
                if bridge is not None:
                    elapsed = time.monotonic() - loop_start
                    actual_rate = 1.0 / elapsed if elapsed > 0 else 0.0
                    status = "idle"
                    if damage_values is not None:
                        status = getattr(state, "status", "idle")
                    _publish_tray_state(
                        bridge,
                        status,
                        tank_name,
                        calculator,
                        frame=frame if bridge.ocr_preview_active else None,
                        ocr_text=ocr_text,
                        ocr_confidence=ocr_confidence,
                        sample_rate_actual=actual_rate,
                    )

            await asyncio.sleep(1.0 / sample_rate)
    finally:
        # Cancel background tasks
        for task in (garage_task, correction_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
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
        if garage_detector:
            garage_detector.close()
        logger.info("Shutdown complete")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Parse CLI flags
    args = sys.argv[1:]

    calibrate_mode: str | None = None
    if "--calibrate" in args:
        idx = args.index("--calibrate")
        args.pop(idx)
        # Next arg is the mode (garage or ocr), default to "ocr"
        if idx < len(args) and not args[idx].endswith(".toml"):
            calibrate_mode = args.pop(idx)
        else:
            calibrate_mode = "ocr"

    tray_mode = "--tray" in args
    if tray_mode:
        args.remove("--tray")

    config_path = args[0] if args else "config.toml"

    if calibrate_mode is not None:
        from tankvision.calibration.roi_picker import run_roi_picker

        run_roi_picker(config_path, mode=calibrate_mode)
        return

    if tray_mode:
        try:
            from tankvision.tray.app import TrayApplication
        except ImportError:
            print(
                "PyQt6 is required for the tray UI.\n"
                "Install it with: pip install 'wot-console-overlay[ui]'"
            )
            sys.exit(1)
        app = TrayApplication(config_path)
        sys.exit(app.run())

    asyncio.run(run(config_path))


if __name__ == "__main__":
    main()
