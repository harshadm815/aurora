"""
The Command Centre's look: deep navy and charcoal surfaces with one
accent colour (cyan, electric blue or violet). Everything is defined here
once; the stylesheet is generated from these values.
"""

from dataclasses import dataclass

ACCENTS = {
    "cyan": ("#22d3ee", "#0e7490"),
    "blue": ("#3b82f6", "#1d4ed8"),
    "purple": ("#a78bfa", "#6d28d9"),
}


@dataclass(frozen=True)
class Palette:
    accent: str
    accent_dim: str
    background: str = "#070b14"
    surface: str = "#0d1424"
    surface_raised: str = "#121b2e"
    border: str = "#1e2a44"
    text: str = "#e2e8f0"
    text_dim: str = "#8b9bb4"
    text_faint: str = "#5b6b85"
    ok: str = "#34d399"
    warning: str = "#fbbf24"
    error: str = "#f87171"
    violet: str = "#8b5cf6"
    user_bubble: str = "#14213a"
    aurora_bubble: str = "#0f1a2c"


def palette(accent: str = "cyan") -> Palette:
    bright, dim = ACCENTS.get(accent, ACCENTS["cyan"])
    return Palette(accent=bright, accent_dim=dim)


def stylesheet(p: Palette) -> str:
    return f"""
* {{
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 10pt;
    color: {p.text};
}}
QMainWindow, QWidget#Root {{ background: {p.background}; }}
QWidget {{ background: transparent; }}
QToolTip {{ background: {p.surface_raised}; color: {p.text}; border: 1px solid {p.border}; padding: 6px; }}

/* Sidebar */
QFrame#Sidebar {{ background: {p.surface}; border-right: 1px solid {p.border}; }}
QPushButton#NavButton {{
    text-align: left; padding: 9px 14px; margin: 2px 10px; border: none; border-radius: 8px;
    color: {p.text_dim}; font-size: 10.5pt;
}}
QPushButton#NavButton:hover {{ background: {p.surface_raised}; color: {p.text}; }}
QPushButton#NavButton:checked {{
    background: {p.surface_raised}; color: {p.accent}; border-left: 3px solid {p.accent};
}}
QLabel#Brand {{ font-size: 15pt; font-weight: 600; letter-spacing: 4px; color: {p.text}; }}
QLabel#BrandSub {{ color: {p.text_faint}; font-size: 8.5pt; letter-spacing: 1px; }}

/* Headings */
QLabel#PageTitle {{ font-size: 18pt; font-weight: 600; }}
QLabel#PageSubtitle {{ color: {p.text_dim}; }}
QLabel#SectionTitle {{ color: {p.text_dim}; font-size: 8.5pt; font-weight: 600; letter-spacing: 1.5px; }}
QLabel#Dim {{ color: {p.text_dim}; }}
QLabel#Faint {{ color: {p.text_faint}; font-size: 9pt; }}
QLabel#BigValue {{ font-size: 20pt; font-weight: 600; }}
QLabel#Clock {{ font-size: 26pt; font-weight: 300; color: {p.text}; }}
QLabel#Accent {{ color: {p.accent}; }}

/* Panels */
QFrame#Panel {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 14px; }}
QFrame#Card {{ background: {p.surface_raised}; border: 1px solid {p.border}; border-radius: 12px; }}
QFrame#Divider {{ background: {p.border}; max-height: 1px; min-height: 1px; }}

/* Buttons */
QPushButton {{
    background: {p.surface_raised}; border: 1px solid {p.border}; border-radius: 8px;
    padding: 7px 14px; color: {p.text};
}}
QPushButton:hover {{ border-color: {p.accent_dim}; }}
QPushButton:pressed {{ background: {p.border}; }}
QPushButton:disabled {{ color: {p.text_faint}; border-color: {p.border}; }}
QPushButton#Primary {{ background: {p.accent_dim}; border-color: {p.accent}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {p.accent}; color: {p.background}; }}
QPushButton#Danger {{ border-color: #7f1d1d; color: {p.error}; }}
QPushButton#Danger:hover {{ background: #2a1215; }}
QPushButton#Quick {{ padding: 12px; text-align: left; }}

/* Inputs */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{
    background: {p.background}; border: 1px solid {p.border}; border-radius: 8px; padding: 6px 8px;
    selection-background-color: {p.accent_dim};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {p.accent}; }}
QComboBox QAbstractItemView {{ background: {p.surface_raised}; border: 1px solid {p.border}; selection-background-color: {p.accent_dim}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {p.border}; background: {p.background}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}

/* Tables and lists */
QTableWidget, QListWidget, QTreeWidget {{
    background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px;
    gridline-color: {p.border}; alternate-background-color: #0b1220;
}}
QTableWidget::item, QListWidget::item {{ padding: 6px; }}
QTableWidget::item:selected, QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {p.accent_dim}; color: white;
}}
QHeaderView::section {{
    background: {p.surface_raised}; color: {p.text_dim}; border: none;
    border-bottom: 1px solid {p.border}; padding: 6px; font-weight: 600;
}}

/* Tabs */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {p.text_dim}; padding: 8px 16px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {p.accent}; border-bottom: 2px solid {p.accent}; }}

/* Scrollbars */
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p.accent_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 4px; }}

/* Chat */
QFrame#UserBubble {{ background: {p.user_bubble}; border: 1px solid {p.border}; border-radius: 14px; }}
QFrame#AuroraBubble {{ background: {p.aurora_bubble}; border: 1px solid {p.border}; border-left: 3px solid {p.accent}; border-radius: 14px; }}
QFrame#ErrorBubble {{ background: #1c1014; border: 1px solid #7f1d1d; border-radius: 14px; }}
QLabel#BubbleMeta {{ color: {p.text_faint}; font-size: 8.5pt; }}

/* Progress */
QProgressBar {{ background: {p.background}; border: 1px solid {p.border}; border-radius: 5px; height: 10px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}

/* Status bar */
QStatusBar {{ background: {p.surface}; color: {p.text_faint}; border-top: 1px solid {p.border}; }}
QMessageBox, QDialog {{ background: {p.surface}; }}
"""
