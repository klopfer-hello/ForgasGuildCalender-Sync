"""Modern theme — follows system dark/light mode."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

ACCENT = "#7c4dff"
ACCENT_HOVER = "#9e7bff"
ACCENT_PRESSED = "#5c35cc"

# Color constants for preview_dialog
DANGER = "#ef5350"
SUCCESS = "#66bb6a"
WARNING = "#ffa726"


def is_system_dark_mode() -> bool:
    """Check if Windows is using dark mode."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return value == 0
    except Exception:
        return False


def get_stylesheet() -> str:
    """Generate stylesheet that adapts to system dark/light mode."""
    dark = is_system_dark_mode()

    if dark:
        bg = "#1e1e2e"
        bg_control = "#2e2e42"
        bg_hover = "#3e3e58"
        text = "#e0e0ef"
        text_dim = "#a0a0bf"
        border = "#4a4a66"
        surface = "#262640"
    else:
        bg = "#f5f5fa"
        bg_control = "#ffffff"
        bg_hover = "#e8e8f0"
        text = "#1a1a2e"
        text_dim = "#606080"
        border = "#d0d0e0"
        surface = "#ffffff"

    return f"""
* {{
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
    color: {text};
}}

QDialog, QWizard {{
    background-color: {bg};
}}

QWizardPage {{
    background-color: {bg};
}}

QLabel {{
    background-color: transparent;
    padding: 2px 0;
}}

QLineEdit, QComboBox, QSpinBox {{
    background-color: {bg_control};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 8px 12px;
    color: {text};
    selection-background-color: {ACCENT};
    selection-color: white;
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {ACCENT};
}}

QComboBox::drop-down {{
    border: none;
    padding-right: 8px;
}}

QComboBox QAbstractItemView {{
    background-color: {bg_control};
    border: 1px solid {border};
    border-radius: 4px;
    color: {text};
    selection-background-color: {ACCENT};
    selection-color: white;
    padding: 4px;
}}

QPushButton {{
    background-color: {bg_control};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 8px 20px;
    min-width: 80px;
    color: {text};
}}

QPushButton:hover {{
    background-color: {bg_hover};
    border-color: {ACCENT};
}}

QPushButton:pressed {{
    background-color: {ACCENT_PRESSED};
    color: white;
}}

QPushButton[primary="true"] {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    color: white;
    font-weight: bold;
}}

QPushButton[primary="true"]:hover {{
    background-color: {ACCENT_HOVER};
}}

QPushButton[primary="true"]:pressed {{
    background-color: {ACCENT_PRESSED};
}}

QTableWidget {{
    background-color: {surface};
    border: 1px solid {border};
    border-radius: 6px;
    gridline-color: {border};
    color: {text};
    selection-background-color: {ACCENT};
    selection-color: white;
}}

QTableWidget::item {{
    padding: 6px 8px;
}}

QHeaderView::section {{
    background-color: {bg};
    border: none;
    border-bottom: 1px solid {border};
    padding: 8px;
    font-weight: bold;
    color: {text_dim};
}}

QMenu {{
    background-color: {bg_control};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 4px;
}}

QMenu::item {{
    padding: 8px 24px;
    border-radius: 4px;
    color: {text};
}}

QMenu::item:selected {{
    background-color: {ACCENT};
    color: white;
}}

QMenu::item:disabled {{
    color: {text_dim};
}}

QMenu::separator {{
    height: 1px;
    background: {border};
    margin: 4px 8px;
}}

QScrollBar:vertical {{
    background: {bg};
    width: 8px;
    border-radius: 4px;
}}

QScrollBar::handle:vertical {{
    background: {border};
    border-radius: 4px;
    min-height: 20px;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
"""


def apply_acrylic(widget):
    """No-op — reserved for future Windows 11 acrylic support."""
    pass
