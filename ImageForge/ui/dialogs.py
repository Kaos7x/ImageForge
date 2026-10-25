"""Friendly dialogs: errors, confirmations, batch summary, shortcuts and about."""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QWidget

from models.results import BatchSummary
from utils.constants import APP_NAME, APP_VERSION
from utils.formatting import format_bytes, format_duration


def show_error(parent: QWidget | None, title: str, message: str, details: str | None = None) -> None:
    box = QMessageBox(QMessageBox.Icon.Critical, title, message, QMessageBox.StandardButton.Ok, parent)
    if details:
        box.setDetailedText(details)
    box.exec()


def show_warning(parent: QWidget | None, title: str, message: str, details: str | None = None) -> None:
    box = QMessageBox(QMessageBox.Icon.Warning, title, message, QMessageBox.StandardButton.Ok, parent)
    if details:
        box.setDetailedText(details)
    box.exec()


def show_info(parent: QWidget | None, title: str, message: str) -> None:
    QMessageBox.information(parent, title, message)


def confirm(parent: QWidget | None, title: str, message: str, accept_text: str = "Continue") -> bool:
    box = QMessageBox(QMessageBox.Icon.Question, title, message, QMessageBox.StandardButton.NoButton, parent)
    accept = box.addButton(accept_text, QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is accept


def show_import_failures(parent: QWidget | None, failures: list[tuple[str, str]], imported: int) -> None:
    """One dialog summarising every file that could not be imported."""
    count = len(failures)
    noun = "file" if count == 1 else "files"
    head = f"{count} {noun} could not be imported."
    if imported:
        head += f"\n{imported} image(s) were imported successfully."
    if count <= 3:
        head += "\n\n" + "\n\n".join(f"{source}\n→ {message}" for source, message in failures)
        show_warning(parent, "Some files were skipped", head)
    else:
        details = "\n\n".join(f"{source}\n→ {message}" for source, message in failures)
        show_warning(parent, "Some files were skipped", head, details)


def show_batch_summary(parent: QWidget | None, summary: BatchSummary) -> bool:
    """Show the result of a batch. Returns True if the user asked to open the output folder."""
    if summary.fatal_error:
        icon, title = QMessageBox.Icon.Critical, "Processing could not start"
        text = summary.fatal_error
    else:
        if summary.cancelled and not summary.succeeded and not summary.failed:
            icon, title = QMessageBox.Icon.Information, "Processing cancelled"
        elif summary.failed:
            icon, title = QMessageBox.Icon.Warning, "Processing finished with errors"
        else:
            icon, title = QMessageBox.Icon.Information, "Processing complete"
        lines = [f"{summary.succeeded} of {summary.total} image(s) saved"]
        if summary.failed:
            lines.append(f"{summary.failed} failed")
        if summary.cancelled:
            lines.append(f"{summary.cancelled} cancelled")
        text = ", ".join(lines) + "."
        if summary.succeeded:
            text += f"\nTotal output size: {format_bytes(summary.bytes_written)}"
        text += f"\nTime: {format_duration(summary.elapsed)}"
        if summary.output_dir:
            text += f"\n\nOutput folder:\n{summary.output_dir}"

    box = QMessageBox(icon, title, text, QMessageBox.StandardButton.NoButton, parent)
    open_button = None
    if summary.output_dir and summary.succeeded:
        open_button = box.addButton("Open Output Folder", QMessageBox.ButtonRole.ActionRole)
    box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
    if summary.errors:
        box.setDetailedText("\n\n".join(f"{name}\n→ {message}" for name, message in summary.errors))
    box.exec()
    return open_button is not None and box.clickedButton() is open_button


_SHORTCUTS = [
    ("Import images", "Ctrl+O"),
    ("Import folder", "Ctrl+Shift+O"),
    ("Import from URL", "Ctrl+U"),
    ("Paste image / link / files", "Ctrl+V"),
    ("Select all images", "Ctrl+A"),
    ("Deselect all", "Ctrl+Shift+A"),
    ("Remove selected", "Delete"),
    ("Clear all", "Ctrl+Shift+Delete"),
    ("Undo last operation", "Ctrl+Z"),
    ("Process selected", "Ctrl+Enter"),
    ("Process all", "Ctrl+Shift+Enter"),
    ("Cancel processing", "Esc"),
    ("Toggle Before / After", "Ctrl+B"),
    ("Zoom in / out", "Ctrl++ / Ctrl+-"),
    ("Fit / actual size", "Ctrl+0 / Ctrl+1"),
    ("Nudge the subject (After view)", "Arrows / Shift+Arrows"),
    ("Open output folder", "Ctrl+E"),
    ("Keyboard shortcuts", "F1"),
    ("Quit", "Ctrl+Q"),
]


def show_shortcuts(parent: QWidget | None) -> None:
    rows = "".join(
        f"<tr><td style='padding:3px 18px 3px 0'>{action}</td><td><b>{keys}</b></td></tr>"
        for action, keys in _SHORTCUTS
    )
    box = QMessageBox(parent)
    box.setWindowTitle("Keyboard shortcuts")
    box.setText(f"<table>{rows}</table><p>Double-click the preview to toggle Fit / 100%.<br>"
                "Mouse wheel zooms; drag to pan.</p>")
    box.setStandardButtons(QMessageBox.StandardButton.Close)
    box.exec()


def show_about(parent: QWidget | None) -> None:
    QMessageBox.about(
        parent,
        f"About {APP_NAME}",
        f"<h3>{APP_NAME} {APP_VERSION}</h3>"
        "<p>Batch image processing: AI background removal, distortion-free resizing, "
        "background compositing, compression and format conversion.</p>"
        "<p>Built with PyQt6, Pillow, OpenCV, NumPy and rembg.</p>",
    )


class ErrorReporter(QObject):
    """Shows unexpected exceptions in a dialog, safely, from any thread."""

    error_occurred = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._showing = False
        self.error_occurred.connect(self._show)

    def report(self, message: str) -> None:
        self.error_occurred.emit(message)  # queued to the GUI thread when emitted from workers

    def _show(self, message: str) -> None:
        if self._showing:  # avoid dialog storms when an error repeats
            return
        self._showing = True
        try:
            show_error(
                None,
                "Unexpected error",
                "Something went wrong, but ImageForge is still running.\n\n"
                f"{message}\n\nDetails were written to the log file.",
            )
        finally:
            self._showing = False
