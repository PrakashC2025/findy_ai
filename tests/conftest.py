from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "devmind-home"
    monkeypatch.setenv("DEVMIND_HOME", str(home))
    return home


@pytest.fixture()
def fake_hw():
    """A 16 GB laptop with a 16 GB GPU — a very common 2024-25 developer box."""
    from devmind.hardware import Hardware

    return Hardware(
        os_name="Linux",
        arch="x86_64",
        cpu="Ryzen 7",
        cores=16,
        ram_gb=16.0,
        ram_available_gb=12.0,
        gpu="NVIDIA RTX 4060 Ti",
        vram_gb=16.0,
        bandwidth_gb_s=288.0,
        reserve_gb=1.5,
        disk_free_gb=200.0,
    )
