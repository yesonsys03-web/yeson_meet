from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from apps.server.domain.video_captions import gpu_pack as gp


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.delenv("YESON_WHISPER_DEVICE", raising=False)
    # 모듈 전역 상태 리셋 — 테스트 간 캐시 누수 방지
    monkeypatch.setattr(gp, "_downloading", False)
    monkeypatch.setattr(gp, "_progress", 0)
    monkeypatch.setattr(gp, "_cuda_checked", None)
    monkeypatch.setattr(gp, "_cuda_error", None)
    monkeypatch.setattr(gp, "_activated", False)
    monkeypatch.setattr(gp, "_last_error", None)
    yield


def _install_fake_dlls() -> None:
    d = gp.bin_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "cublas64_12.dll").write_bytes(b"x")
    (d / "cudnn_ops64_9.dll").write_bytes(b"x")


def test_wheel_url_picks_win_amd64(monkeypatch):
    fake = {"urls": [
        {"filename": "pkg-1.0-py3-none-manylinux2014_x86_64.whl",
         "url": "https://x/linux.whl", "size": 1},
        {"filename": "pkg-1.0-py3-none-win_amd64.whl",
         "url": "https://x/win.whl", "size": 42},
    ]}
    monkeypatch.setattr(gp, "_http_get_json", lambda url: fake)
    assert gp._wheel_url("pkg") == ("https://x/win.whl", 42)


def test_wheel_url_missing_win_wheel_raises(monkeypatch):
    monkeypatch.setattr(gp, "_http_get_json", lambda url: {"urls": []})
    with pytest.raises(RuntimeError):
        gp._wheel_url("pkg")


def test_download_pack_extracts_dlls_flat(monkeypatch):
    # 휠 이름별로 서로 다른 DLL을 담은 가짜 zip을 만들어 평탄 추출을 검증
    monkeypatch.setattr(gp, "_wheel_url", lambda pkg: (f"https://x/{pkg}.whl", 10))

    def fake_download(url, dest, progress_cb):
        dll = "nvidia/cublas/bin/cublas64_12.dll" if "cublas" in url \
            else "nvidia/cudnn/bin/cudnn_ops64_9.dll"
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr(dll, b"dll-bytes")
            zf.writestr("nvidia/cublas/include/header.h", b"not-a-dll")
        progress_cb(10)

    monkeypatch.setattr(gp, "_download_file", fake_download)
    gp.download_pack()
    names = sorted(p.name for p in gp.bin_dir().glob("*"))
    assert names == ["cublas64_12.dll", "cudnn_ops64_9.dll"]
    assert gp.is_installed()
    assert gp._downloading is False


def test_is_installed_requires_both_dll_families(tmp_path: Path):
    assert not gp.is_installed()
    d = gp.bin_dir()
    d.mkdir(parents=True)
    (d / "cublas64_12.dll").write_bytes(b"x")
    assert not gp.is_installed()  # cudnn 없으면 미설치
    (d / "cudnn_ops64_9.dll").write_bytes(b"x")
    assert gp.is_installed()


def test_enabled_flag_roundtrip():
    assert not gp.is_enabled()
    gp.set_enabled(True)
    assert gp.is_enabled()
    gp.set_enabled(False)
    assert not gp.is_enabled()


def test_resolve_device_env_forces_cpu(monkeypatch):
    monkeypatch.setenv("YESON_WHISPER_DEVICE", "cpu")
    gp.set_enabled(True)
    _install_fake_dlls()
    monkeypatch.setattr(gp, "cuda_available", lambda: True)
    assert gp.resolve_device() == ("cpu", "int8")


def test_resolve_device_env_forces_cuda(monkeypatch):
    monkeypatch.setenv("YESON_WHISPER_DEVICE", "cuda")
    assert gp.resolve_device() == ("cuda", "float16")


def test_resolve_device_optin_path(monkeypatch):
    gp.set_enabled(True)
    _install_fake_dlls()
    monkeypatch.setattr(gp, "cuda_available", lambda: True)
    assert gp.resolve_device() == ("cuda", "float16")


def test_resolve_device_defaults_to_cpu(monkeypatch):
    # 플래그 없음 → CPU. 플래그 있어도 CUDA 미인식이면 CPU.
    assert gp.resolve_device() == ("cpu", "int8")
    gp.set_enabled(True)
    _install_fake_dlls()
    monkeypatch.setattr(gp, "cuda_available", lambda: False)
    assert gp.resolve_device() == ("cpu", "int8")


def test_download_pack_failure_recorded_not_swallowed(monkeypatch):
    # 워커 스레드 예외는 UI에 안 보인다 — last_error로 표면화해야
    # "버튼 눌러도 반응 없음"이 재발하지 않는다(2026-07-08 Windows 회귀 가드).
    def boom(pkg):
        raise RuntimeError("네트워크 실패")

    monkeypatch.setattr(gp, "_wheel_url", boom)
    gp.download_pack()
    assert gp._downloading is False
    assert gp._last_error is not None and "네트워크 실패" in gp._last_error
    assert gp.status()["last_error"] == gp._last_error
    # 재시도 시작 시 이전 오류는 리셋
    monkeypatch.setattr(gp, "_wheel_url", lambda pkg: ("u", 1))
    monkeypatch.setattr(gp, "_download_file", lambda u, d, cb: None)
    monkeypatch.setattr(gp, "_extract_dlls", lambda w, d: 0)
    gp.download_pack()
    assert gp._last_error is None


def test_cuda_available_caches_failure(monkeypatch):
    calls: list[int] = []

    def fake_count():
        calls.append(1)
        raise RuntimeError("no cuda")

    monkeypatch.setattr(gp, "_cuda_device_count", fake_count)
    assert gp.cuda_available() is False
    assert gp.cuda_available() is False
    assert len(calls) == 1


def test_cuda_available_records_exception_reason(monkeypatch):
    def fake_count():
        raise RuntimeError("cuDNN 미설치")

    monkeypatch.setattr(gp, "_cuda_device_count", fake_count)
    assert gp.cuda_available() is False
    status = gp.cuda_status()
    assert status["ok"] is False
    assert "cuDNN 미설치" in status["reason"]


def test_cuda_available_records_zero_device_reason(monkeypatch):
    monkeypatch.setattr(gp, "_cuda_device_count", lambda: 0)
    assert gp.cuda_available() is False
    status = gp.cuda_status()
    assert status["ok"] is False
    assert status["reason"]  # "device count 0" 류 사유가 채워짐


def test_cuda_status_before_any_check_is_unknown():
    status = gp.cuda_status()
    assert status == {"ok": False, "reason": None}


def test_set_enabled_true_resets_cuda_cache(monkeypatch):
    """재활성화 시 이전 실패가 프로세스 수명 캐시로 영구 고정되지 않고 재검사되어야
    한다 — 예: GPU 팩을 다시 다운로드/드라이버를 고친 뒤 토글을 다시 켤 때."""
    calls: list[int] = []

    def fake_count():
        calls.append(1)
        return 0 if len(calls) == 1 else 1

    monkeypatch.setattr(gp, "_cuda_device_count", fake_count)
    assert gp.cuda_available() is False
    assert gp.cuda_status()["reason"] is not None

    gp.set_enabled(True)
    assert gp._cuda_checked is None
    assert gp._cuda_error is None
    assert gp.cuda_available() is True  # 재검사돼 두 번째 호출 결과를 반영
    assert len(calls) == 2


def test_download_pack_resets_cuda_cache(monkeypatch):
    monkeypatch.setattr(gp, "_cuda_checked", False)
    monkeypatch.setattr(gp, "_cuda_error", "이전 실패 사유")
    monkeypatch.setattr(gp, "_wheel_url", lambda pkg: ("u", 1))
    monkeypatch.setattr(gp, "_download_file", lambda u, d, cb: None)
    monkeypatch.setattr(gp, "_extract_dlls", lambda w, d: 0)
    gp.download_pack()
    assert gp._cuda_checked is None
    assert gp._cuda_error is None


def test_status_shape(monkeypatch):
    monkeypatch.setattr(gp, "gpu_name", lambda: None)
    out = gp.status()
    assert set(out) == {"supported", "gpu_name", "installed", "downloading",
                        "progress", "cuda_available", "cuda_ok", "cuda_reason",
                        "enabled", "approx_bytes", "last_error"}
    assert out["installed"] is False
    assert out["cuda_available"] is False  # 미설치면 ctranslate2 검사 자체를 안 함
    assert out["cuda_ok"] is False
    assert out["cuda_reason"] is None
