from pathlib import Path


def load_dark_theme() -> str:
    style_path = Path(__file__).with_name("dark_theme.qss")
    assets_dir = style_path.parent.parent / "assets"
    return style_path.read_text(encoding="utf-8").replace(
        "{{ASSETS_DIR}}", assets_dir.as_posix()
    )
