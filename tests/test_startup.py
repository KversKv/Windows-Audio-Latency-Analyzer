import os
from pathlib import Path
import subprocess
import sys


def test_main_entry_starts_and_exits_cleanly(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["LATENCY_ANALYZER_CONFIG_DIR"] = str(tmp_path)
    environment["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        [sys.executable, "main.py", "--simulate", "--smoke-test"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert (tmp_path / "config.json").exists()
