"""What machine the user has, so we can say honestly whether it will run a model.

No outside dependencies: dragging psutil into the build is not worth it, and the
amount of memory is available through each OS's own facilities anyway.
"""
import platform
import re
import subprocess
from functools import lru_cache


def _macos_ram_gb() -> float:
    out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
    return int(out.stdout.strip()) / 1024 ** 3


def _linux_ram_gb() -> float:
    with open("/proc/meminfo", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("MemTotal:"):
                return int(re.search(r"\d+", line).group()) / 1024 ** 2
    return 0.0


def _windows_ram_gb() -> float:
    import ctypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    stat = MemoryStatusEx()
    stat.dwLength = ctypes.sizeof(MemoryStatusEx)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    return stat.ullTotalPhys / 1024 ** 3


def _gpu_name() -> str:
    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.run(["system_profiler", "SPDisplaysDataType"],
                                 capture_output=True, text=True, timeout=15).stdout
            m = re.search(r"Chipset Model:\s*(.+)", out)
            return m.group(1).strip() if m else ""
        if system == "Linux":
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                                  "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=15).stdout.strip()
            return out.splitlines()[0] if out else ""
        if system == "Windows":
            out = subprocess.run(["wmic", "path", "win32_VideoController", "get", "name"],
                                 capture_output=True, text=True, timeout=15).stdout
            lines = [l.strip() for l in out.splitlines()[1:] if l.strip()]
            return lines[0] if lines else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""
    return ""


@lru_cache(maxsize=1)
def specs() -> dict:
    """{ram_gb, cpu, gpu, apple_silicon, usable_gb} — usable_gb is what can really be
    given to a model: some memory is always taken by the system and the app itself."""
    system = platform.system()
    try:
        ram = {"Darwin": _macos_ram_gb, "Linux": _linux_ram_gb,
               "Windows": _windows_ram_gb}.get(system, lambda: 0.0)()
    except Exception:  # noqa: BLE001 — detecting the hardware must not bring the app down
        ram = 0.0
    cpu = platform.processor() or platform.machine()
    if system == "Darwin":
        try:
            cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                 capture_output=True, text=True, timeout=10).stdout.strip() or cpu
        except (OSError, subprocess.SubprocessError):
            pass
    apple = system == "Darwin" and platform.machine() == "arm64"
    # On Apple Silicon the memory is shared with the GPU, so more of it is
    # available; on other systems a model mostly runs into separate video memory
    # or into CPU mode.
    usable = max(0.0, ram * (0.75 if apple else 0.6) - 1.5)
    return {"ram_gb": round(ram, 1), "cpu": cpu, "gpu": _gpu_name(),
            "apple_silicon": apple, "usable_gb": round(usable, 1), "os": system}


def fits(required_gb: float) -> str:
    """Will this machine run the model: 'yes' | 'tight' | 'no'."""
    usable = specs()["usable_gb"]
    if not usable:
        return "unknown"
    if required_gb <= usable * 0.8:
        return "yes"
    if required_gb <= usable:
        return "tight"
    return "no"
