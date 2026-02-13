"""Entry point for Tank Vision application."""

import asyncio
import logging
import signal
import sys

from tankvision.config import load_config
from tankvision.capture.screen_capture import ScreenCapture
from tankvision.ocr.ocr_pipeline import OcrPipeline
from tankvision.calculation.moe_calculator import MoeCalculator
from tankvision.server.websocket_server import MoeWebSocketServer

logger = logging.getLogger("tankvision")


async def run(config_path: str = "config.toml") -> None:
    config = load_config(config_path)

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
        current_moe=config["moe"]["current_moe_percent"],
        target_damage=config["moe"]["target_damage"],
        ema_alpha=config["moe"]["ema_alpha"],
    )

    server = MoeWebSocketServer(
        ws_port=config["server"]["ws_port"],
        http_port=config["server"]["http_port"],
    )

    stop_event = asyncio.Event()

    def handle_signal() -> None:
        logger.info("Shutting down...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for SIGTERM
            pass

    await server.start()
    logger.info(
        "Tank Vision running. Add Browser Source in OBS: http://localhost:%d",
        config["server"]["http_port"],
    )

    try:
        while not stop_event.is_set():
            frame = capture.grab_frame()
            if frame is not None:
                damage_values = ocr.process_frame(frame)
                if damage_values is not None:
                    state = calculator.update(damage_values)
                    await server.broadcast(state)
            await asyncio.sleep(1.0 / config["ocr"]["sample_rate"])
    finally:
        await server.stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.toml"
    asyncio.run(run(config_path))


if __name__ == "__main__":
    main()
