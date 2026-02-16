"""TrayApplication: launches Qt on main thread, asyncio on worker thread."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading

from PyQt6.QtWidgets import QApplication

from tankvision.tray.state_bridge import AppStateBridge
from tankvision.tray.tray_icon import TankVisionTrayIcon

logger = logging.getLogger("tankvision.tray")


class TrayApplication:
    """Orchestrates the Qt tray UI and the asyncio worker thread."""

    def __init__(self, config_path: str = "config.toml") -> None:
        self._config_path = config_path
        self._bridge = AppStateBridge()
        self._worker_thread: threading.Thread | None = None

    def run(self) -> int:
        """Entry point. Must be called from the main thread.

        Returns the application exit code.
        """
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)

        tray = TankVisionTrayIcon(self._bridge, self._config_path)
        tray.show()

        # Start asyncio worker on a daemon thread
        self._worker_thread = threading.Thread(
            target=self._run_async_worker,
            name="tankvision-worker",
            daemon=True,
        )
        self._worker_thread.start()

        # Wire quit
        tray.quit_requested.connect(lambda: self._shutdown(app))

        exit_code = app.exec()

        # Ensure worker stops
        self._bridge.request_stop()
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5.0)

        return exit_code

    def _run_async_worker(self) -> None:
        """Runs in the worker thread. Starts a new asyncio event loop."""
        from tankvision.__main__ import run

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run(self._config_path, bridge=self._bridge))
        except Exception:
            logger.exception("Worker thread crashed")
        finally:
            loop.close()

    def _shutdown(self, app: QApplication) -> None:
        self._bridge.request_stop()
        app.quit()
