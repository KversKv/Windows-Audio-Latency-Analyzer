from pathlib import Path


def load_dark_theme() -> str:
    style_path = Path(__file__).with_name("dark_theme.qss")
    return style_path.read_text(encoding="utf-8")
