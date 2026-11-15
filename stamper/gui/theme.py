"""Colors and the application style sheet.

Cool blue-grey ground, white surfaces. Stamp-ink blue is kept for what ends
up on the document and for the main action; amber is for warnings; red
is only for Delete.
Sizes are in device-independent pixels, so they scale with Windows display
scaling (125%, 150%).
"""

from __future__ import annotations

from .fonts import MONO_FAMILY, UI_FAMILY

__all__ = ["COLORS", "STYLE_SHEET"]

COLORS = {
    "ground": "#E9EDF2",
    "surface": "#FFFFFF",
    "well": "#DDE2E9",
    "border": "#CDD4DE",
    "border_strong": "#8B96A8",
    "control_border": "#7A8699",  # resting edge of fields and buttons; 3.7:1 on white (UX-041)
    "text": "#18202E",
    "muted": "#4F5B6E",
    "ink": "#2B3A8F",
    "ink_dark": "#1C276A",
    "ink_tint": "#E3E7F7",
    "amber_bg": "#FDF1DE",
    "amber_border": "#E9C48A",
    "amber_text": "#5C3205",
    "amber_icon": "#8A4B08",
    "amber_field": "#C27A1A",
    "disabled_bg": "#DDE2E9",
    "danger": "#B42318",  # Delete buttons; 6.6:1 on white
}

_C = COLORS

STYLE_SHEET = f"""
* {{
    font-family: "{UI_FAMILY}";
    font-size: 15px;
    color: {_C["text"]};
}}
QMainWindow, QWidget#ground {{ background: {_C["ground"]}; }}
QWidget#surface, QScrollArea#surface, QWidget#panel {{ background: {_C["surface"]}; }}
QWidget#panel {{ border-left: 1px solid {_C["border"]}; }}
QWidget#footer {{ background: {_C["surface"]}; border-top: 1px solid {_C["border"]}; }}
QFrame#previewWell {{ background: {_C["well"]}; border-radius: 10px; }}

QLabel#h1 {{ font-size: 24px; font-weight: 700; }}
QLabel#hero {{ font-size: 40px; font-weight: 700; color: {_C["ink"]}; }}
QLabel#savedTitle {{ font-size: 32px; font-weight: 700; color: {_C["ink"]}; }}
QLabel#lead {{ font-size: 17px; color: {_C["muted"]}; }}
QLabel#muted, QLabel#help {{ color: {_C["muted"]}; font-size: 14px; }}
QLabel#fieldLabel, QLabel#sectionLabel {{ font-weight: 700; }}
QLabel#fileName {{ font-size: 16px; font-weight: 700; }}
QLabel#msgOk {{ color: {_C["ink"]}; font-size: 14px; }}
QLabel#msgBad {{ color: {_C["amber_icon"]}; font-size: 14px; }}
QLabel#msgNeutral {{ color: {_C["muted"]}; font-size: 14px; }}
QLabel#mono {{ font-family: "{MONO_FAMILY}"; }}
QLabel#previewMessage {{
    color: {_C["amber_text"]}; background: {_C["amber_bg"]};
    border: 1px solid {_C["amber_border"]}; border-radius: 10px; padding: 14px;
}}

QFrame#dropTarget {{
    background: {_C["surface"]}; border: 3px solid {_C["ink"]}; border-radius: 22px;
}}
QFrame#dropTarget[dragging="true"] {{ background: {_C["ink_tint"]}; }}
QFrame#dropInner {{ border: 2px dashed {_C["ink"]}; border-radius: 14px; background: transparent; }}

QFrame#warning, QFrame#notice {{
    background: {_C["amber_bg"]}; border: 1px solid {_C["amber_border"]}; border-radius: 10px;
}}
QFrame#warning QLabel, QFrame#notice QLabel {{
    color: {_C["amber_text"]}; font-size: 14px; background: transparent;
}}
QFrame#info {{
    background: {_C["ground"]}; border: 1px solid {_C["border"]}; border-radius: 10px;
}}
QFrame#info QLabel {{ color: {_C["muted"]}; font-size: 14px; background: transparent; }}
QLabel#thumbnail {{ background: {_C["ground"]}; border: 1px solid {_C["border"]}; }}
QFrame#savedCard {{ background: {_C["surface"]}; border: 1px solid {_C["border"]}; border-radius: 14px; }}

QLineEdit, QComboBox {{
    min-height: 40px; padding: 0 10px; background: {_C["surface"]};
    border: 1.5px solid {_C["control_border"]}; border-radius: 8px; font-size: 16px;
}}
QLineEdit[invalid="true"] {{ border-color: {_C["amber_field"]}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {_C["ink"]}; }}
QLineEdit#identifier {{ font-family: "{MONO_FAMILY}"; font-size: 18px; font-weight: 600; }}
QComboBox::drop-down {{ border: 0; width: 28px; }}
QComboBox QAbstractItemView {{
    background: {_C["surface"]}; border: 1px solid {_C["border"]};
    selection-background-color: {_C["ink_tint"]}; selection-color: {_C["text"]};
}}

QPushButton {{
    min-height: 40px; padding: 0 18px; font-size: 16px; font-weight: 500;
    background: {_C["surface"]}; border: 1.5px solid {_C["control_border"]}; border-radius: 8px;
}}
QPushButton:hover {{ border-color: {_C["ink"]}; }}
QPushButton:focus {{ border: 3px solid {_C["ink"]}; }}
QPushButton:disabled {{ color: {_C["muted"]}; background: {_C["disabled_bg"]}; border-color: {_C["disabled_bg"]}; }}
QPushButton#primary {{
    color: #FFFFFF; background: {_C["ink"]}; border-color: {_C["ink"]}; font-weight: 700;
}}
QPushButton#primary:hover {{ background: {_C["ink_dark"]}; }}
QPushButton#primary:focus {{ border: 3px solid {_C["ink_dark"]}; }}
QPushButton#primary:disabled {{ color: {_C["muted"]}; background: {_C["disabled_bg"]}; border-color: {_C["disabled_bg"]}; }}
QPushButton#outline {{ color: {_C["ink"]}; border-color: {_C["ink"]}; }}
QPushButton#link {{
    border: 0; background: transparent; color: {_C["ink"]}; text-decoration: underline;
    padding: 0 4px; min-height: 28px; font-size: 15px;
}}
QPushButton#link:focus {{ border: 2px solid {_C["ink"]}; border-radius: 4px; }}
QPushButton#mutedLink {{
    border: 0; background: transparent; color: {_C["muted"]}; text-decoration: underline;
    padding: 0 4px; min-height: 28px; font-size: 14px;
}}
QPushButton#mutedLink:hover {{ color: {_C["ink"]}; }}
QPushButton#mutedLink:focus {{ border: 2px solid {_C["ink"]}; border-radius: 4px; }}
QPushButton#small {{ min-height: 30px; padding: 0 12px; font-size: 14px; }}
QPushButton#danger {{ min-height: 30px; padding: 0 12px; font-size: 14px; color: {_C["danger"]}; }}
QPushButton#choice {{
    min-height: 44px; padding: 4px 10px; font-size: 14px; font-weight: 500; border-radius: 10px;
}}
QPushButton#choice:checked {{ background: {_C["ink_tint"]}; border-color: {_C["ink"]}; }}
QToolButton#anchor {{
    min-width: 32px; min-height: 32px; background: {_C["surface"]};
    border: 1.5px solid {_C["control_border"]}; border-radius: 4px;
}}
QToolButton#anchor:checked {{ background: {_C["ink"]}; border-color: {_C["ink"]}; }}
QToolButton#anchor:focus {{ border: 2px solid {_C["ink_dark"]}; }}
QToolButton#pageNav {{
    min-width: 34px; min-height: 34px; background: {_C["surface"]};
    border: 1px solid {_C["border"]}; border-radius: 6px; font-size: 16px;
}}
QToolButton#pageNav:focus {{ border: 2px solid {_C["ink"]}; }}
QToolButton#pageNav:disabled {{ color: {_C["border_strong"]}; }}

QCheckBox {{ spacing: 8px; min-height: 26px; }}
QCheckBox::indicator {{ width: 17px; height: 17px; }}
/* No background rule on QCheckBox inside notices: it hid the tick box (D-113). */

QProgressBar {{
    min-height: 12px; max-height: 12px; border: 0; border-radius: 6px;
    background: {_C["well"]}; text-align: center; font-size: 1px; color: transparent;
}}
QProgressBar::chunk {{ background: {_C["ink"]}; border-radius: 6px; }}
QScrollArea {{ border: 0; }}

QLineEdit#code {{ font-family: "{MONO_FAMILY}"; font-size: 15px; }}
QDoubleSpinBox {{ min-height: 36px; font-size: 15px; }}
QListWidget {{
    background: {_C["surface"]}; border: 1.5px solid {_C["border"]}; border-radius: 8px;
    padding: 4px; font-size: 15px; outline: 0;
}}
QListWidget:focus {{ border-color: {_C["ink"]}; }}
QListWidget::item {{ min-height: 28px; padding: 0 6px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {_C["ink_tint"]}; color: {_C["text"]}; }}
QListWidget#nav {{ border: 0; background: transparent; font-size: 16px; font-weight: 500; }}
QListWidget#nav::item {{ min-height: 38px; padding: 0 10px; }}
QFrame#card {{ background: {_C["surface"]}; border: 1px solid {_C["border"]}; border-radius: 10px; }}
QToolButton#insert {{
    min-height: 40px; padding: 0 12px; font-size: 15px; background: {_C["surface"]};
    border: 1.5px solid {_C["border"]}; border-radius: 8px;
}}
QToolButton#insert:focus {{ border: 3px solid {_C["ink"]}; }}
QMenu {{ background: {_C["surface"]}; border: 1px solid {_C["border"]}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; }}
QMenu::item:selected {{ background: {_C["ink_tint"]}; }}
QFrame#colorPanel {{ background: {_C["surface"]}; }}
QToolButton#swatch {{ padding: 2px; border: 1.5px solid transparent; border-radius: 5px; }}
QToolButton#swatch:hover {{ border-color: {_C["border_strong"]}; }}
QToolButton#swatch:focus {{ border-color: {_C["ink"]}; }}
"""
