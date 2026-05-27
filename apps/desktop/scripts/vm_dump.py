"""vm_dump.py — dump Voicemeeter state for yeson-meet auto-routing design.

Run on the Windows machine that has Voicemeeter Banana installed and
manually pre-configured (mic on Strip 1 with A1/B1/B2 lit, VAIO with
A1/B2 lit, Windows default playback = "Voicemeeter Input").

    python vm_dump.py > vm_dump.json

Send the resulting JSON back. No external deps required — uses only
the Python stdlib (ctypes, winreg). If `sounddevice` happens to be
installed, the script also adds the Windows audio device list, which
helps us pick the exact capture name for the sidecar.
"""

from __future__ import annotations

import ctypes
import json
import sys
import time
from ctypes import byref, c_char_p, c_float, c_long, create_string_buffer, POINTER

try:
    import winreg  # type: ignore[import-not-found]
except ImportError:
    sys.stderr.write("vm_dump.py must run on Windows (winreg unavailable).\n")
    sys.exit(1)

REGISTRY_KEY = r"SOFTWARE\WOW6432Node\VB-Audio\Voicemeeter"
INSTALL_DIR_VALUE = "VoicemeeterRemoteDir"
DLL_NAME = "VoicemeeterRemote64.dll"
EDITIONS = {1: "Standard", 2: "Banana", 3: "Potato", 6: "Potato (x64)"}


def locate_dll() -> str:
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRY_KEY) as key:
        install_dir, _ = winreg.QueryValueEx(key, INSTALL_DIR_VALUE)
    return f"{install_dir.rstrip(chr(92))}\\{DLL_NAME}"


def main() -> None:
    dll_path = locate_dll()
    sys.stderr.write(f"[vm_dump] loading {dll_path}\n")
    dll = ctypes.WinDLL(dll_path)

    dll.VBVMR_Login.restype = c_long
    dll.VBVMR_Logout.restype = c_long
    dll.VBVMR_GetVoicemeeterType.restype = c_long
    dll.VBVMR_GetVoicemeeterType.argtypes = [POINTER(c_long)]
    dll.VBVMR_GetVoicemeeterVersion.restype = c_long
    dll.VBVMR_GetVoicemeeterVersion.argtypes = [POINTER(c_long)]
    dll.VBVMR_GetParameterFloat.restype = c_long
    dll.VBVMR_GetParameterFloat.argtypes = [c_char_p, POINTER(c_float)]
    dll.VBVMR_GetParameterStringA.restype = c_long
    dll.VBVMR_GetParameterStringA.argtypes = [c_char_p, c_char_p]

    code = dll.VBVMR_Login()
    if code not in (0, 1):
        sys.stderr.write(f"[vm_dump] VBVMR_Login returned {code}\n")
        sys.exit(2)
    time.sleep(0.4)

    vm_type = c_long(0)
    dll.VBVMR_GetVoicemeeterType(byref(vm_type))
    version = c_long(0)
    dll.VBVMR_GetVoicemeeterVersion(byref(version))

    def get_string(param: str) -> str:
        buf = create_string_buffer(512)
        rc = dll.VBVMR_GetParameterStringA(param.encode("ascii"), buf)
        return buf.value.decode("utf-8", errors="replace") if rc == 0 else ""

    def get_float(param: str) -> float | None:
        value = c_float(0.0)
        rc = dll.VBVMR_GetParameterFloat(param.encode("ascii"), byref(value))
        return float(value.value) if rc == 0 else None

    lane_count = 5 if vm_type.value == 2 else (8 if vm_type.value in (3, 6) else 3)
    string_keys = [
        "label",
        "device.name",
        "device.sr",
        "device.wdm",
        "device.mme",
        "device.ks",
        "device.asio",
    ]
    strip_floats = ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "mute", "gain"]
    bus_floats = ["mute", "gain", "mono", "sel"]

    def dump_lane(prefix: str, count: int, floats: list[str]) -> list[dict]:
        out: list[dict] = []
        for i in range(count):
            entry: dict = {"index": i}
            for key in string_keys:
                entry[key] = get_string(f"{prefix}[{i}].{key}")
            for key in floats:
                value = get_float(f"{prefix}[{i}].{key}")
                if value is not None:
                    entry[key] = value
            out.append(entry)
        return out

    report: dict = {
        "edition": EDITIONS.get(vm_type.value, "Unknown"),
        "edition_code": vm_type.value,
        "version_raw": version.value,
        "version": ".".join(
            str((version.value >> shift) & 0xFF) for shift in (24, 16, 8, 0)
        ),
        "lane_count": lane_count,
        "strips": dump_lane("Strip", lane_count, strip_floats),
        "buses": dump_lane("Bus", lane_count, bus_floats),
    }

    try:
        import sounddevice as sd  # type: ignore[import-untyped]

        report["windows_audio_devices"] = [
            {
                "index": idx,
                "name": entry["name"],
                "host_api": sd.query_hostapis(entry["hostapi"])["name"],
                "max_input_channels": entry["max_input_channels"],
                "max_output_channels": entry["max_output_channels"],
                "default_samplerate": entry["default_samplerate"],
            }
            for idx, entry in enumerate(sd.query_devices())
        ]
    except ImportError:
        report["windows_audio_devices"] = (
            "sounddevice not installed; skip or run `pip install sounddevice`"
        )

    dll.VBVMR_Logout()
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
