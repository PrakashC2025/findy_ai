from __future__ import annotations

from devmind.hardware import Hardware


def test_cpu_only_budget_uses_available_ram() -> None:
    hw = Hardware(ram_gb=32, ram_available_gb=20, vram_gb=0, bandwidth_gb_s=40, reserve_gb=2)
    assert hw.backend == "cpu"
    # min(20, 32*0.7) - 2
    assert hw.usable_memory_gb == 20 - 2


def test_gpu_budget_is_ninety_percent_minus_reserve() -> None:
    hw = Hardware(ram_gb=32, ram_available_gb=24, vram_gb=12, bandwidth_gb_s=360, reserve_gb=1.5)
    assert hw.backend == "gpu"
    assert hw.usable_memory_gb == 12 * 0.9 - 1.5


def test_fit_respects_overhead(fake_hw) -> None:
    # budget = 16 * 0.9 - 1.5 = 12.9 GB
    assert fake_hw.fits(4.7) is True
    assert fake_hw.fits(9.0) is True
    assert fake_hw.fits(12.6) is False  # 12.6 + 0.4 > 12.9
    assert fake_hw.fits(20.0) is False


def test_token_estimate_is_bandwidth_bound(fake_hw) -> None:
    small = fake_hw.estimate_tokens_per_second(1.0)
    large = fake_hw.estimate_tokens_per_second(9.0)
    assert small > large > 0
    # 288 GB/s * 0.6 / 4.7 GB ≈ 37 tok/s
    assert 30 < fake_hw.estimate_tokens_per_second(4.7) < 45


def test_apple_unified_memory_is_reported_as_metal() -> None:
    hw = Hardware(
        os_name="Darwin",
        arch="arm64",
        cpu="Apple M2 Pro",
        ram_gb=16,
        ram_available_gb=12,
        vram_gb=16,
        apple_silicon=True,
        bandwidth_gb_s=200,
        reserve_gb=1.5,
    )
    assert hw.backend == "metal"
    assert hw.usable_memory_gb > 10


def test_detect_is_safe_on_this_machine() -> None:
    hw = Hardware.detect()
    assert hw.ram_gb > 0
    assert hw.cores >= 1
    assert hw.backend in {"cpu", "gpu", "metal"}
    assert "backend=" in hw.summary()
