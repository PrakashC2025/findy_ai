"""Hardware detection used to pick the biggest model that still feels instant.

Everything here is best-effort and read-only: the goal is an actionable
estimate ("a 7B Q4 model will run ~18 tok/s on this GPU"), not a benchmark.
The router replaces estimates with measurements as soon as it has any.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Rough HBM/GDDR bandwidth (GB/s) for the GPUs people actually run local models
# on.  Matched by longest substring, so "RTX 4090" wins over "RTX 40".
_GPU_BANDWIDTH: dict[str, float] = {
    "rtx 5090": 1792,
    "rtx 5080": 960,
    "rtx 5070 ti": 896,
    "rtx 5070": 672,
    "rtx 4090": 1008,
    "rtx 4080": 717,
    "rtx 4070 ti": 504,
    "rtx 4070": 504,
    "rtx 4060 ti": 288,
    "rtx 4060": 272,
    "rtx 3090 ti": 936,
    "rtx 3090": 936,
    "rtx 3080 ti": 912,
    "rtx 3080": 760,
    "rtx 3070 ti": 608,
    "rtx 3070": 448,
    "rtx 3060 ti": 448,
    "rtx 3060": 360,
    "rtx 2080 ti": 616,
    "rtx 2080": 448,
    "rtx 2070": 448,
    "rtx 2060": 336,
    "a100": 1555,
    "h100": 3350,
    "l40s": 864,
    "l4": 300,
    "t4": 320,
    "rx 7900 xtx": 960,
    "rx 7900 xt": 800,
    "rx 7800 xt": 624,
    "rx 7700 xt": 432,
    "rx 6900 xt": 512,
    "rx 6800 xt": 512,
    "rx 6800": 512,
    "arc a770": 560,
    "arc a750": 512,
}

_APPLE_BANDWIDTH = {
    "ultra": 800.0,
    "max": 400.0,
    "pro": 200.0,
    "m4": 120.0,
    "m3": 100.0,
    "m2": 100.0,
    "m1": 68.0,
}


def _run(cmd: list[str], timeout: float = 3.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _linux_ram_gb() -> tuple[float, float]:
    total = available = 0.0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            value_kb = float(rest.strip().split()[0])
            if key == "MemTotal":
                total = value_kb / 1024 / 1024
            elif key == "MemAvailable":
                available = value_kb / 1024 / 1024
    except (OSError, ValueError, IndexError):
        pass
    if not total:
        total = _total_ram_fallback()
        available = total * 0.7
    return total, available


def _total_ram_fallback() -> float:
    if platform.system() == "Darwin":
        value = _run(["sysctl", "-n", "hw.memsize"])
        if value.isdigit():
            return int(value) / 1024**3
    if platform.system() == "Windows":  # pragma: no cover - not exercised in CI
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / 1024**3
        except Exception:
            return 0.0
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        return page * pages / 1024**3
    except (ValueError, OSError, AttributeError):
        return 0.0


def _nvidia_gpus() -> list[tuple[str, float]]:
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    gpus: list[tuple[str, float]] = []
    for line in out.splitlines():
        name, _, mem = line.rpartition(",")
        if not name:
            continue
        try:
            gpus.append((name.strip(), float(mem.strip()) / 1024))
        except ValueError:
            continue
    return gpus


def _amd_vram_gb() -> tuple[str, float]:
    out = _run(["rocm-smi", "--showmeminfo", "vram", "--json"])
    if out:
        try:
            data = json.loads(out)
            for card, info in data.items():
                total = next(
                    (v for k, v in info.items() if "Total" in k and "VRAM" in k.upper()), 0
                )
                if total:
                    return str(card), float(total) / 1024**3
        except (json.JSONDecodeError, AttributeError, ValueError):
            pass
    return "", 0.0


def _bandwidth_for(gpu_name: str) -> float:
    lowered = gpu_name.lower()
    best = (0.0, "")
    for needle, bandwidth in _GPU_BANDWIDTH.items():
        if needle in lowered and len(needle) > len(best[1]):
            best = (bandwidth, needle)
    return best[0]


def _apple_bandwidth(chip: str) -> float:
    lowered = chip.lower()
    tiers = [tier for tier in _APPLE_BANDWIDTH if tier in lowered]
    if not tiers:
        return 100.0
    return _APPLE_BANDWIDTH[max(tiers, key=len)]


@dataclass
class Hardware:
    os_name: str = platform.system()
    arch: str = platform.machine()
    cpu: str = platform.processor() or "unknown"
    cores: int = os.cpu_count() or 1
    ram_gb: float = 0.0
    ram_available_gb: float = 0.0
    gpu: str = ""
    vram_gb: float = 0.0
    apple_silicon: bool = False
    bandwidth_gb_s: float = 0.0
    reserve_gb: float = 1.5
    disk_free_gb: float = 0.0
    sources: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ probe
    @classmethod
    def detect(cls, reserve_gb: float = 1.5, home: Path | None = None) -> Hardware:
        hw = cls(reserve_gb=reserve_gb)
        system = platform.system()
        machine = platform.machine().lower()

        if system == "Linux":
            hw.ram_gb, hw.ram_available_gb = _linux_ram_gb()
            hw.sources["ram"] = "proc_meminfo"
            cpuinfo = Path("/proc/cpuinfo")
            if cpuinfo.exists():
                for line in cpuinfo.read_text(errors="ignore").splitlines():
                    if line.lower().startswith("model name"):
                        hw.cpu = line.split(":", 1)[1].strip()
                        break
        elif system == "Darwin":
            hw.ram_gb = _total_ram_fallback()
            hw.ram_available_gb = hw.ram_gb * 0.7
            hw.cpu = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple Silicon"
            hw.sources["ram"] = "sysctl_hw.memsize"
            hw.apple_silicon = machine in {"arm64", "aarch64"}
        else:  # pragma: no cover - Windows
            hw.ram_gb = _total_ram_fallback()
            hw.ram_available_gb = hw.ram_gb * 0.7
            hw.cpu = platform.processor() or "unknown"
            hw.sources["ram"] = "GlobalMemoryStatusEx"

        if hw.apple_silicon:
            chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or hw.cpu
            hw.gpu = f"Apple {chip}".strip()
            hw.vram_gb = hw.ram_gb  # unified memory
            hw.bandwidth_gb_s = _apple_bandwidth(chip)
            hw.sources["gpu"] = "apple_unified_memory"
        else:
            gpus = _nvidia_gpus()
            if gpus:
                name, vram = max(gpus, key=lambda item: item[1])
                hw.gpu, hw.vram_gb = name, vram
                hw.bandwidth_gb_s = _bandwidth_for(name) or 400.0
                hw.sources["gpu"] = "nvidia-smi"
            else:
                name, vram = _amd_vram_gb()
                if vram:
                    hw.gpu, hw.vram_gb = name, vram
                    hw.bandwidth_gb_s = 400.0
                    hw.sources["gpu"] = "rocm-smi"

        if not hw.gpu:
            hw.gpu = "CPU only"
            # Dual-channel DDR4/DDR5 desktop: 40 GB/s is a fair middle estimate.
            hw.bandwidth_gb_s = 40.0
            hw.sources["gpu"] = "none"

        try:
            hw.disk_free_gb = shutil.disk_usage(home or Path.home()).free / 1024**3
        except OSError:
            hw.disk_free_gb = 0.0
        return hw

    # ------------------------------------------------------------- budgeting
    @property
    def backend(self) -> str:
        if self.apple_silicon:
            return "metal"
        return "gpu" if self.vram_gb >= 2 else "cpu"

    @property
    def usable_memory_gb(self) -> float:
        """Memory a model may use without pushing the machine into swap."""
        if self.backend in {"gpu", "metal"}:
            raw = self.vram_gb * 0.9
        else:
            raw = min(self.ram_available_gb or self.ram_gb * 0.6, self.ram_gb * 0.7)
        return max(0.0, raw - self.reserve_gb)

    def fits(self, model_gb: float) -> bool:
        return model_gb + 0.4 <= self.usable_memory_gb

    def estimate_tokens_per_second(self, model_gb: float, on_gpu: bool | None = None) -> float:
        """Bandwidth-bound estimate.  Real numbers replace this after first run."""
        if model_gb <= 0:
            return 0.0
        gpu = self.backend != "cpu" if on_gpu is None else on_gpu
        efficiency = 0.60 if gpu else 0.30
        return round(max(0.5, self.bandwidth_gb_s * efficiency / model_gb), 1)

    def suggested_ctx(self, model_gb: float) -> int:
        """Keep KV cache inside the memory budget; smaller models get more ctx."""
        headroom = self.usable_memory_gb - model_gb
        if headroom > 8:
            return 32768
        if headroom > 4:
            return 16384
        if headroom > 1.5:
            return 8192
        return 4096

    # ------------------------------------------------------------------ view
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(
            backend=self.backend,
            usable_memory_gb=round(self.usable_memory_gb, 1),
            ram_gb=round(self.ram_gb, 1),
            ram_available_gb=round(self.ram_available_gb, 1),
            vram_gb=round(self.vram_gb, 1),
            disk_free_gb=round(self.disk_free_gb, 1),
        )
        return data

    def summary(self) -> str:
        gpu = f"{self.gpu} ({self.vram_gb:.0f} GB VRAM)" if self.vram_gb else self.gpu
        return (
            f"{self.os_name}/{self.arch} · {self.cores} cores · "
            f"{self.ram_gb:.0f} GB RAM · {gpu} · backend={self.backend} · "
            f"model budget={self.usable_memory_gb:.1f} GB"
        )
