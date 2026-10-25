"""Dark theme: colour tokens, Fusion palette and stylesheet loading."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from string import Template

from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import QApplication

from resources.icons import ensure_icons

RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"


@dataclass(frozen=True)
class Colors:
    bg: str = "#0e1014"
    surface: str = "#15181e"
    raised: str = "#1c2027"
    raised_hover: str = "#242933"
    input: str = "#11141a"
    border: str = "#272c36"
    border_light: str = "#363c49"
    text: str = "#e8eaee"
    muted: str = "#9099a8"
    muted_dim: str = "#5d6573"
    accent: str = "#ff7a2f"
    accent_hover: str = "#ff9250"
    accent_pressed: str = "#e0661f"
    accent_disabled: str = "#3a2a1f"
    accent_dim: str = "#3a2314"
    accent_faint: str = "#1d150f"
    accent_text: str = "#1b0e04"
    success: str = "#3fcf8e"
    warning: str = "#f5b84b"
    danger: str = "#ff5d6c"


COLORS = Colors()


def _palette() -> QPalette:
    c = COLORS
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: c.bg,
        QPalette.ColorRole.WindowText: c.text,
        QPalette.ColorRole.Base: c.input,
        QPalette.ColorRole.AlternateBase: c.raised,
        QPalette.ColorRole.ToolTipBase: c.raised,
        QPalette.ColorRole.ToolTipText: c.text,
        QPalette.ColorRole.Text: c.text,
        QPalette.ColorRole.Button: c.raised,
        QPalette.ColorRole.ButtonText: c.text,
        QPalette.ColorRole.BrightText: c.danger,
        QPalette.ColorRole.Link: c.accent,
        QPalette.ColorRole.Highlight: c.accent,
        QPalette.ColorRole.HighlightedText: c.accent_text,
        QPalette.ColorRole.PlaceholderText: c.muted_dim,
    }
    for role, value in roles.items():
        palette.setColor(role, QColor(value))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(c.muted_dim))
    return palette


def apply_theme(app: QApplication) -> None:
    """Apply the Fusion style, dark palette and the ImageForge stylesheet."""
    app.setStyle("Fusion")
    app.setPalette(_palette())
    font = QFont(app.font())
    font.setPointSizeF(9.5)
    app.setFont(font)

    icons = ensure_icons(arrow_color=COLORS.muted, check_color=COLORS.accent_text)
    template = Template((RESOURCES_DIR / "style.qss").read_text(encoding="utf-8"))
    app.setStyleSheet(template.safe_substitute({**asdict(COLORS), **icons}))


def repolish(widget) -> None:  # noqa: ANN001
    """Re-evaluate dynamic-property selectors (e.g. ``[state="ready"]``)."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
