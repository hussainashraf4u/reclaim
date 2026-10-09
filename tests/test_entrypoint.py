import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def test_python_dash_m_reclaim_shows_help():
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    out = subprocess.run([sys.executable, "-m", "reclaim", "--help"], capture_output=True, text=True, env=env)
    assert out.returncode == 0 and "--scan-only" in out.stdout


def test_catalog_data_ships_inside_the_package():
    from reclaim.catalog import load_catalog
    assert len(load_catalog()) >= 30
