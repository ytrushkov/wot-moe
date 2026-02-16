"""Visual ROI picker: transparent overlay where the user drags a rectangle.

Two-step flow:
1. Dialog to select which screen or window to capture (with thumbnails).
2. Fullscreen overlay on that screen to drag-select the ROI.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from pathlib import Path


_MODES = {
    "garage": {
        "title": "WoT Console Assistant — Select Tank Name Region",
        "instruction": (
            "Drag a rectangle over the tank name in the garage.\n"
            "Press ENTER to confirm, ESC to cancel."
        ),
        "section": "garage",
    },
    "ocr": {
        "title": "WoT Console Assistant — Select Damage Number Region",
        "instruction": (
            "Drag a rectangle over the damage numbers shown during battle.\n"
            "Include both direct and assisted damage areas.\n"
            "Press ENTER to confirm, ESC to cancel."
        ),
        "section": "ocr",
    },
}

# Thumbnail size for the picker dialog
_THUMB_WIDTH = 240
_THUMB_HEIGHT = 135


def _save_roi_to_config(
    roi: tuple[int, int, int, int],
    config_path: Path,
    section: str = "garage",
) -> None:
    """Write the ROI into the given config section, preserving other settings."""
    x, y, w, h = roi
    section_header = f"[{section}]"

    if config_path.exists():
        text = config_path.read_text()
    else:
        text = ""

    if section_header in text:
        lines = text.splitlines(keepends=True)
        new_lines = []
        in_section = False
        keys_written: set[str] = set()
        for line in lines:
            stripped = line.strip()
            if stripped == section_header:
                in_section = True
                new_lines.append(line)
                continue
            elif stripped.startswith("[") and stripped.endswith("]"):
                if in_section:
                    for key, val in [
                        ("roi_x", x), ("roi_y", y),
                        ("roi_width", w), ("roi_height", h),
                    ]:
                        if key not in keys_written:
                            new_lines.append(f"{key} = {val}\n")
                in_section = False
                new_lines.append(line)
                continue

            if in_section:
                for key, val in [
                    ("roi_x", x), ("roi_y", y),
                    ("roi_width", w), ("roi_height", h),
                ]:
                    if stripped.startswith(f"{key}"):
                        new_lines.append(f"{key} = {val}\n")
                        keys_written.add(key)
                        break
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        if in_section:
            for key, val in [
                ("roi_x", x), ("roi_y", y),
                ("roi_width", w), ("roi_height", h),
            ]:
                if key not in keys_written:
                    new_lines.append(f"{key} = {val}\n")

        text = "".join(new_lines)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += (
            f"\n{section_header}\n"
            f"roi_x = {x}\n"
            f"roi_y = {y}\n"
            f"roi_width = {w}\n"
            f"roi_height = {h}\n"
        )
        if section == "garage":
            text += "poll_interval = 3.0\n"

    config_path.write_text(text)


def _bgra_to_qpixmap(frame, QImage, QPixmap):
    """Convert a BGRA numpy array to a QPixmap."""
    h, w, _ = frame.shape
    frame_rgba = frame.copy()
    frame_rgba[:, :, 0], frame_rgba[:, :, 2] = (
        frame[:, :, 2].copy(),
        frame[:, :, 0].copy(),
    )
    qimage = QImage(frame_rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimage.copy())


def _list_macos_windows() -> list[dict]:
    """List visible application windows on macOS using JXA (JavaScript for Automation).

    Returns a list of dicts with keys: app, title, x, y, width, height.
    Returns an empty list on failure or non-macOS platforms.
    """
    if platform.system() != "Darwin":
        return []

    # JXA script to enumerate visible windows with their bounds
    script = """\
var se = Application("System Events");
var procs = se.processes.whose({visible: true});
var result = [];
for (var i = 0; i < procs.length; i++) {
    try {
        var app = procs[i];
        var appName = app.name();
        var wins = app.windows();
        for (var j = 0; j < wins.length; j++) {
            try {
                var w = wins[j];
                var pos = w.position();
                var sz = w.size();
                if (sz[0] > 50 && sz[1] > 50) {
                    result.push({
                        app: appName,
                        title: w.name() || "",
                        x: pos[0], y: pos[1],
                        width: sz[0], height: sz[1]
                    });
                }
            } catch(e) {}
        }
    } catch(e) {}
}
JSON.stringify(result);"""

    try:
        proc = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout.strip())
    except Exception:
        pass
    return []


# Type tag stored in QListWidgetItem.data(UserRole+1) to distinguish
# screen items from window items.
_TAG_SCREEN = "screen"
_TAG_WINDOW = "window"


def run_roi_picker(
    config_path: str = "config.toml",
    mode: str = "garage",
) -> tuple[int, int, int, int] | None:
    """Launch the ROI picker and save result to config.

    Args:
        config_path: Path to the TOML config file.
        mode: "garage" for tank name region, "ocr" for damage number region.

    Returns:
        The selected (x, y, width, height) or None if cancelled.
    """
    if mode not in _MODES:
        print(f"Unknown calibration mode '{mode}'. Use 'garage' or 'ocr'.")
        return None

    try:
        from PyQt6.QtCore import QPoint, QRect, QSize, Qt
        from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
        from PyQt6.QtWidgets import (
            QApplication,
            QDialog,
            QHBoxLayout,
            QLabel,
            QListWidget,
            QListWidgetItem,
            QMainWindow,
            QPushButton,
            QTabWidget,
            QVBoxLayout,
            QWidget,
        )
    except ImportError:
        print(
            "PyQt6 is required for the calibration UI.\n"
            "Install it with: pip install 'wot-console-overlay[ui]'"
        )
        return None

    import mss
    import numpy as np

    mode_info = _MODES[mode]

    # ---- Picker dialog (screens + windows tabs) ----

    class PickerDialog(QDialog):
        """Dialog with tabs for Screens and Windows, each with thumbnails."""

        def __init__(
            self,
            screen_thumbs: list[tuple[QPixmap, str, int]],
            window_entries: list[dict],
            full_screenshots: dict[int, QPixmap],
        ) -> None:
            super().__init__()
            self.selected_tag: str | None = None  # "screen" or "window"
            self.selected_data: dict | None = None

            self.setWindowTitle("WoT Console Assistant — Choose What to Capture")
            self.setMinimumSize(560, 400)

            layout = QVBoxLayout(self)

            label = QLabel(
                "Select a screen or application window, "
                "then click Continue to draw the capture region."
            )
            label.setWordWrap(True)
            layout.addWidget(label)

            tabs = QTabWidget()

            # --- Screens tab ---
            screen_tab = QWidget()
            sl = QVBoxLayout(screen_tab)
            self._screen_list = QListWidget()
            self._screen_list.setViewMode(QListWidget.ViewMode.IconMode)
            self._screen_list.setIconSize(QSize(_THUMB_WIDTH, _THUMB_HEIGHT))
            self._screen_list.setSpacing(12)
            self._screen_list.setMovement(QListWidget.Movement.Static)
            self._screen_list.setResizeMode(QListWidget.ResizeMode.Adjust)
            self._screen_list.setMinimumHeight(_THUMB_HEIGHT + 60)
            self._screen_list.setWordWrap(True)
            for pixmap, lbl, idx in screen_thumbs:
                item = QListWidgetItem(QIcon(pixmap), lbl)
                item.setData(Qt.ItemDataRole.UserRole, {
                    "tag": _TAG_SCREEN, "screen_idx": idx,
                })
                item.setSizeHint(QSize(_THUMB_WIDTH + 20, _THUMB_HEIGHT + 40))
                self._screen_list.addItem(item)
            if self._screen_list.count() > 0:
                self._screen_list.setCurrentRow(0)
            self._screen_list.itemDoubleClicked.connect(self._accept)
            sl.addWidget(self._screen_list)
            tabs.addTab(screen_tab, "Screens")

            # --- Windows tab ---
            win_tab = QWidget()
            wl = QVBoxLayout(win_tab)
            self._win_list = QListWidget()
            self._win_list.setViewMode(QListWidget.ViewMode.IconMode)
            self._win_list.setIconSize(QSize(_THUMB_WIDTH, _THUMB_HEIGHT))
            self._win_list.setSpacing(12)
            self._win_list.setMovement(QListWidget.Movement.Static)
            self._win_list.setResizeMode(QListWidget.ResizeMode.Adjust)
            self._win_list.setMinimumHeight(_THUMB_HEIGHT + 60)
            self._win_list.setWordWrap(True)

            for winfo in window_entries:
                # Crop the window region from the full screen screenshot
                # to create a thumbnail
                wx, wy = int(winfo["x"]), int(winfo["y"])
                ww, wh = int(winfo["width"]), int(winfo["height"])
                # Find which screen contains this window
                best_screen_idx = 0
                for si, (_, _, sidx) in enumerate(screen_thumbs):
                    s = QApplication.screens()[sidx]
                    sg = s.geometry()
                    if sg.contains(QPoint(wx + ww // 2, wy + wh // 2)):
                        best_screen_idx = sidx
                        break

                full_pm = full_screenshots.get(best_screen_idx)
                if full_pm is not None:
                    sg = QApplication.screens()[best_screen_idx].geometry()
                    # Window coords relative to the screen
                    rx = wx - sg.x()
                    ry = wy - sg.y()
                    crop_rect = QRect(rx, ry, ww, wh)
                    crop_rect = crop_rect.intersected(QRect(0, 0, full_pm.width(), full_pm.height()))
                    if crop_rect.width() > 10 and crop_rect.height() > 10:
                        win_pixmap = full_pm.copy(crop_rect)
                    else:
                        win_pixmap = full_pm
                else:
                    win_pixmap = QPixmap(_THUMB_WIDTH, _THUMB_HEIGHT)
                    win_pixmap.fill(QColor(40, 40, 40))

                thumb = win_pixmap.scaled(
                    _THUMB_WIDTH, _THUMB_HEIGHT,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

                title = winfo.get("title", "")
                app_name = winfo.get("app", "")
                lbl = app_name
                if title and title != app_name:
                    lbl += f"\n{title[:40]}"

                item = QListWidgetItem(QIcon(thumb), lbl)
                item.setData(Qt.ItemDataRole.UserRole, {
                    "tag": _TAG_WINDOW,
                    "screen_idx": best_screen_idx,
                    "x": wx, "y": wy, "width": ww, "height": wh,
                })
                item.setSizeHint(QSize(_THUMB_WIDTH + 20, _THUMB_HEIGHT + 40))
                self._win_list.addItem(item)

            if self._win_list.count() > 0:
                self._win_list.setCurrentRow(0)
            self._win_list.itemDoubleClicked.connect(self._accept)
            wl.addWidget(self._win_list)

            if self._win_list.count() > 0:
                tabs.addTab(win_tab, "Windows")

            self._tabs = tabs
            layout.addWidget(tabs)

            # Buttons
            btn_layout = QHBoxLayout()
            cancel_btn = QPushButton("Cancel")
            cancel_btn.clicked.connect(self.reject)
            btn_layout.addWidget(cancel_btn)

            ok_btn = QPushButton("Continue")
            ok_btn.setDefault(True)
            ok_btn.clicked.connect(self._accept)
            btn_layout.addWidget(ok_btn)

            layout.addLayout(btn_layout)

        def _current_list(self) -> QListWidget:
            """Return the list widget in the active tab."""
            w = self._tabs.currentWidget()
            if w is None:
                return self._screen_list
            lst = w.findChild(QListWidget)
            return lst if lst is not None else self._screen_list

        def _accept(self) -> None:
            item = self._current_list().currentItem()
            if item is not None:
                data = item.data(Qt.ItemDataRole.UserRole)
                self.selected_tag = data["tag"]
                self.selected_data = data
            self.accept()

    # ---- ROI picker overlay ----

    class RoiPickerWindow(QMainWindow):
        """Fullscreen window showing a screenshot with a selection overlay."""

        def __init__(self, background: QPixmap, screen_offset: QPoint) -> None:
            super().__init__()
            self._bg = background
            self._screen_offset = screen_offset
            self._start: QPoint | None = None
            self._end: QPoint | None = None
            self._confirmed = False

            self.setWindowTitle(mode_info["title"])
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
            )

        def paintEvent(self, event) -> None:  # noqa: N802
            painter = QPainter(self)
            painter.drawPixmap(self.rect(), self._bg)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

            painter.setPen(QPen(QColor(255, 255, 255)))
            font = QFont("Arial", 18)
            painter.setFont(font)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                "\n\n" + mode_info["instruction"],
            )

            if self._start and self._end:
                rect = QRect(self._start, self._end).normalized()
                painter.drawPixmap(rect, self._bg, rect)
                pen = QPen(QColor(0, 255, 0), 2)
                painter.setPen(pen)
                painter.drawRect(rect)

                abs_x = rect.x() + self._screen_offset.x()
                abs_y = rect.y() + self._screen_offset.y()
                dim_font = QFont("Arial", 12)
                painter.setFont(dim_font)
                painter.setPen(QPen(QColor(0, 255, 0)))
                painter.drawText(
                    rect.x(),
                    rect.y() - 5,
                    f"{rect.width()}x{rect.height()} at ({abs_x}, {abs_y})",
                )

            painter.end()

        def mousePressEvent(self, event) -> None:  # noqa: N802
            if event.button() == Qt.MouseButton.LeftButton:
                self._start = event.pos()
                self._end = event.pos()
                self.update()

        def mouseMoveEvent(self, event) -> None:  # noqa: N802
            if self._start is not None:
                self._end = event.pos()
                self.update()

        def mouseReleaseEvent(self, event) -> None:  # noqa: N802
            if event.button() == Qt.MouseButton.LeftButton and self._start:
                self._end = event.pos()
                self.update()

        def keyPressEvent(self, event) -> None:  # noqa: N802
            if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
                if self._start and self._end:
                    self._confirmed = True
                    self.close()
            elif event.key() == Qt.Key.Key_Escape:
                self._confirmed = False
                self.close()

        @property
        def selected_roi(self) -> tuple[int, int, int, int] | None:
            """Return absolute (x, y, width, height) or None if cancelled."""
            if not self._confirmed or not self._start or not self._end:
                return None
            rect = QRect(self._start, self._end).normalized()
            if rect.width() < 10 or rect.height() < 10:
                return None
            abs_x = rect.x() + self._screen_offset.x()
            abs_y = rect.y() + self._screen_offset.y()
            return (abs_x, abs_y, rect.width(), rect.height())

    # ---- Launch ----

    app = QApplication.instance() or QApplication(sys.argv)

    # Capture full screenshots for each screen (used for thumbnails + cropping)
    screens = QApplication.screens()
    screen_thumbs: list[tuple[QPixmap, str, int]] = []
    full_screenshots: dict[int, QPixmap] = {}

    with mss.mss() as sct:
        for i, screen in enumerate(screens):
            geo = screen.geometry()
            mss_idx = i + 1
            if mss_idx < len(sct.monitors):
                monitor = sct.monitors[mss_idx]
            else:
                monitor = {
                    "left": geo.x(), "top": geo.y(),
                    "width": geo.width(), "height": geo.height(),
                }
            shot = sct.grab(monitor)
            frame = np.array(shot, dtype=np.uint8)
            pixmap = _bgra_to_qpixmap(frame, QImage, QPixmap)
            full_screenshots[i] = pixmap
            thumb = pixmap.scaled(
                _THUMB_WIDTH, _THUMB_HEIGHT,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            name = screen.name() or f"Screen {i + 1}"
            label = f"{name}\n{geo.width()}x{geo.height()}"
            screen_thumbs.append((thumb, label, i))

    # Enumerate application windows (macOS only, best-effort)
    window_entries = _list_macos_windows()

    # Step 1: show the picker dialog
    dialog = PickerDialog(screen_thumbs, window_entries, full_screenshots)
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_data is None:
        print("Calibration cancelled.")
        return None

    data = dialog.selected_data
    screen_idx = data["screen_idx"]
    screen = screens[screen_idx]
    geo = screen.geometry()

    if data["tag"] == _TAG_WINDOW:
        print(f"Selected window on screen {screen.name()}")
    else:
        print(
            f"Selected screen: {screen.name()} "
            f"({geo.width()}x{geo.height()} at {geo.x()},{geo.y()})"
        )

    # Fully destroy the dialog before taking the fresh screenshot
    dialog.hide()
    dialog.destroy()
    app.processEvents()
    time.sleep(0.3)
    app.processEvents()
    time.sleep(0.3)

    # Step 2: take a fresh screenshot (dialog is gone)
    mss_monitor_idx = screen_idx + 1
    with mss.mss() as sct:
        if mss_monitor_idx < len(sct.monitors):
            monitor = sct.monitors[mss_monitor_idx]
        else:
            monitor = {
                "left": geo.x(), "top": geo.y(),
                "width": geo.width(), "height": geo.height(),
            }
        shot = sct.grab(monitor)
        frame = np.array(shot, dtype=np.uint8)

    bg_pixmap = _bgra_to_qpixmap(frame, QImage, QPixmap)

    # Step 3: show the ROI picker on the selected screen
    screen_offset = QPoint(geo.x(), geo.y())
    picker = RoiPickerWindow(bg_pixmap, screen_offset)
    picker.setGeometry(geo)
    picker.showFullScreen()

    # Use a local event loop so this works both standalone and
    # when called from within an already-running QApplication (tray mode).
    from PyQt6.QtCore import QEventLoop

    loop = QEventLoop()
    # QMainWindow doesn't have a "finished" signal, so we poll via destroyed
    # after close. Override closeEvent to quit our local loop instead.
    original_close = picker.closeEvent

    def _on_close(event):
        original_close(event)
        loop.quit()

    picker.closeEvent = _on_close
    loop.exec()

    roi = picker.selected_roi
    if roi is None:
        print("Calibration cancelled.")
        return None

    x, y, w, h = roi
    print(f"Selected region: {w}x{h} at ({x}, {y})")

    section = mode_info["section"]
    _save_roi_to_config(roi, Path(config_path), section=section)
    print(f"Saved [{section}] ROI to {config_path}")
    return roi
