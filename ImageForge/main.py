"""ImageForge - batch image processing.   Run with:  python main.py"""
from __future__ import annotations

import sys
from pathlib import Path

MIN_PYTHON = (3, 10)
RECOMMENDED_PYTHON = (3, 12)

# Make imports work no matter which directory the app is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _check_environment() -> None:
    if sys.version_info < MIN_PYTHON:
        sys.exit(f"ImageForge needs Python {RECOMMENDED_PYTHON[0]}.{RECOMMENDED_PYTHON[1]}+ "
                 f"(found {sys.version.split()[0]}).")
    if sys.version_info < RECOMMENDED_PYTHON:
        print(f"Warning: Python {RECOMMENDED_PYTHON[0]}.{RECOMMENDED_PYTHON[1]}+ is recommended "
              f"(running {sys.version.split()[0]}).", file=sys.stderr)

    missing = []
    for module, package in (
        ("PyQt6", "PyQt6"),
        ("PIL", "Pillow"),
        ("cv2", "opencv-python-headless"),
        ("numpy", "numpy"),
    ):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        sys.exit(
            "Missing required packages: " + ", ".join(missing) + "\n"
            "Install everything with:  pip install -r requirements.txt"
        )


def main() -> int:
    _check_environment()

    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    from resources.icons import create_app_icon
    from ui.dialogs import ErrorReporter
    from ui.main_window import MainWindow
    from ui.theme import apply_theme
    from utils.constants import APP_NAME, ORG_NAME
    from utils.logging_setup import install_exception_hooks, setup_logging
    from utils.paths import download_cache_dir, prune_old_files, thumbnail_dir

    setup_logging()

    if sys.platform == "win32":  # give the taskbar our own identity/icon
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ImageForge.Desktop")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setWindowIcon(create_app_icon())
    apply_theme(app)

    reporter = ErrorReporter(app)
    install_exception_hooks(reporter.report)

    # Housekeeping: drop stale cache files from earlier sessions.
    prune_old_files(download_cache_dir(), max_age_days=3)
    prune_old_files(thumbnail_dir(), max_age_days=30)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
