"""Cross-thread bridge between the asyncio worker and the Qt UI."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal


@dataclass
class AppSnapshot:
    """Immutable snapshot of the application state for the UI to read."""

    status: str = "idle"  # idle, battle_active, battle_ended, paused, stopped
    tank_name: str = ""
    moe_percent: float = 0.0
    projected_moe: float = 0.0
    delta: float = 0.0
    direct_damage: int = 0
    assisted_damage: int = 0
    combined_damage: int = 0
    battles_this_session: int = 0
    last_frame: np.ndarray | None = field(default=None, repr=False)
    last_ocr_text: str = ""
    last_confidence: float = 0.0
    sample_rate_actual: float = 0.0


class AppStateBridge(QObject):
    """Thread-safe bridge: asyncio worker emits data, Qt UI consumes it via signals.

    Signals (worker → UI):
        state_updated: Emitted with an AppSnapshot whenever the main loop completes a cycle.
        log_message: Emitted with a formatted log line.

    Commands (UI → worker):
        Pause/resume/stop via threading.Event (polled by the worker each iteration).
        Config changes via a locked dict (polled and drained each iteration).
    """

    state_updated = pyqtSignal(object)
    log_message = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()
        self._ocr_preview_active = threading.Event()
        self._lock = threading.Lock()
        self._config_changes: dict[str, object] = {}

    # --- Called from asyncio worker thread ---

    def publish_state(self, snapshot: AppSnapshot) -> None:
        """Push a new state snapshot to the UI (thread-safe signal emission)."""
        self.state_updated.emit(snapshot)

    def publish_log(self, message: str) -> None:
        """Forward a log line to the UI (thread-safe signal emission)."""
        self.log_message.emit(message)

    # --- Called from Qt main thread ---

    def request_pause(self) -> None:
        self._pause_event.set()

    def request_resume(self) -> None:
        self._pause_event.clear()

    def request_stop(self) -> None:
        self._stop_event.set()

    def set_ocr_preview_active(self, active: bool) -> None:
        if active:
            self._ocr_preview_active.set()
        else:
            self._ocr_preview_active.clear()

    def push_config_change(self, section: str, key: str, value: object) -> None:
        with self._lock:
            self._config_changes[f"{section}.{key}"] = value

    # --- Polled from asyncio worker thread ---

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    @property
    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    @property
    def ocr_preview_active(self) -> bool:
        return self._ocr_preview_active.is_set()

    def pop_config_changes(self) -> dict[str, object]:
        """Drain and return all pending config changes."""
        with self._lock:
            changes = self._config_changes.copy()
            self._config_changes.clear()
            return changes
