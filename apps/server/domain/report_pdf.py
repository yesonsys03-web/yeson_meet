# === ANCHOR: REPORT_PDF_START ===
"""PDF report generation: MS Word first, LibreOffice fallback (cross-platform).

Convert a ``.docx`` bytes payload → PDF bytes using the highest-fidelity engine
available on the host:

  * **MS Word** (best fidelity) — macOS via AppleScript (``osascript``),
    Windows via COM automation (``pywin32``).  Never available on Linux.
  * **LibreOffice** ``soffice --headless --convert-to pdf`` — cross-platform
    fallback.

Neither engine is bundled, so absence of *both* is handled gracefully (returns
``None``).  Engine selection can be forced with the ``YESON_PDF_ENGINE`` env var
(``auto`` | ``word`` | ``soffice`` | ``none``); ``auto`` (default) tries Word
then soffice.  Conversion never raises — callers treat ``None`` as
"PDF unavailable" and return an appropriate HTTP error.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_ENV_ENGINE = "YESON_PDF_ENGINE"
_VALID_ENGINES = ("auto", "word", "soffice", "none")

# Well-known soffice install locations outside of PATH.
_FALLBACK_PATHS: list[str] = [
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",        # macOS
    r"C:\Program Files\LibreOffice\program\soffice.exe",           # Windows (64-bit)
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",     # Windows (32-bit)
    "/usr/bin/soffice",                                            # Linux
    "/snap/bin/libreoffice",                                       # Linux (snap)
]

# macOS MS Word application bundle.
_WORD_MAC_APP = "/Applications/Microsoft Word.app"


# === ANCHOR: REPORT_PDF_FIND_SOFFICE_START ===
def find_soffice() -> str | None:
    """Return the path to the soffice executable, or None if not found."""
    # 1. Check PATH first.
    found = shutil.which("soffice")
    if found:
        return found
    # 2. Try well-known fallback locations.
    for candidate in _FALLBACK_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None
# === ANCHOR: REPORT_PDF_FIND_SOFFICE_END ===


# === ANCHOR: REPORT_PDF_FIND_WORD_START ===
def find_word() -> str | None:
    """Return a handle for MS Word if installed, else None.

    macOS   → path to the ``Microsoft Word.app`` bundle.
    Windows → ``"Word.Application"`` when the COM ProgID is registered.
    Linux   → always ``None`` (Word does not run there).
    """
    if sys.platform == "darwin":
        return _WORD_MAC_APP if Path(_WORD_MAC_APP).is_dir() else None
    if sys.platform == "win32":
        try:
            import winreg

            winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Word.Application").Close()
            return "Word.Application"
        except OSError:
            return None
    return None
# === ANCHOR: REPORT_PDF_FIND_WORD_END ===


# === ANCHOR: REPORT_PDF_FIND_ENGINE_START ===
def find_pdf_engine() -> list[str]:
    """Return the ordered list of available PDF engines for this host.

    Honors the ``YESON_PDF_ENGINE`` override:
      * ``auto`` (default) — Word first, then soffice.
      * ``word`` / ``soffice`` — restrict to that engine only.
      * ``none`` — disable PDF generation entirely.

    Returns an empty list when no engine is available/selected — callers treat
    that as "PDF unavailable".
    """
    pref = (os.environ.get(_ENV_ENGINE) or "auto").strip().lower() or "auto"
    if pref not in _VALID_ENGINES:
        logger.warning(
            "report_pdf: unknown %s=%r — falling back to 'auto'.", _ENV_ENGINE, pref
        )
        pref = "auto"
    if pref == "none":
        return []

    engines: list[str] = []
    if pref in ("auto", "word") and find_word() is not None:
        engines.append("word")
    if pref in ("auto", "soffice") and find_soffice() is not None:
        engines.append("soffice")
    return engines
# === ANCHOR: REPORT_PDF_FIND_ENGINE_END ===


# === ANCHOR: REPORT_PDF_CONVERT_START ===
def convert_docx_to_pdf(docx_bytes: bytes) -> bytes | None:
    """Convert *docx_bytes* to PDF bytes, trying each available engine in order.

    Returns:
        PDF bytes on success, or ``None`` if no engine is available or every
        engine fails.  Never raises — callers should treat ``None`` as
        "PDF unavailable" and return an appropriate HTTP error.
    """
    engines = find_pdf_engine()
    if not engines:
        logger.warning(
            "report_pdf: no PDF engine available (need MS Word or LibreOffice). "
            "Install one, or check %s.",
            _ENV_ENGINE,
        )
        return None

    for engine in engines:
        if engine == "word":
            pdf = _convert_via_word(docx_bytes)
        elif engine == "soffice":
            pdf = _convert_via_soffice(docx_bytes)
        else:  # pragma: no cover - guarded by find_pdf_engine
            pdf = None
        if pdf is not None:
            return pdf
        logger.warning("report_pdf: engine %r produced no PDF — trying next.", engine)
    return None
# === ANCHOR: REPORT_PDF_CONVERT_END ===


# === ANCHOR: REPORT_PDF_SOFFICE_START ===
def _convert_via_soffice(docx_bytes: bytes) -> bytes | None:
    """Convert via LibreOffice ``soffice --headless --convert-to pdf``."""
    soffice = find_soffice()
    if soffice is None:
        return None

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            docx_path = tmp_path / "report.docx"
            docx_path.write_bytes(docx_bytes)

            # Isolate the LibreOffice user-profile so concurrent requests
            # do not share a lock file.
            profile_dir = tmp_path / "lo_profile"
            profile_dir.mkdir()
            profile_url = profile_dir.as_uri()  # file:///...

            result = subprocess.run(  # noqa: S603
                [
                    soffice,
                    f"-env:UserInstallation={profile_url}",
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(tmp_path),
                    str(docx_path),
                ],
                capture_output=True,
                timeout=60,
            )

            if result.returncode != 0:
                logger.warning(
                    "report_pdf: soffice exited with code %d. stderr=%s",
                    result.returncode,
                    result.stderr.decode(errors="replace"),
                )
                return None

            pdf_path = tmp_path / "report.pdf"
            if not pdf_path.exists():
                logger.warning(
                    "report_pdf: soffice succeeded (rc=0) but output PDF not found at %s",
                    pdf_path,
                )
                return None

            return pdf_path.read_bytes()

    except subprocess.TimeoutExpired:
        logger.warning("report_pdf: soffice timed out after 60 s")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("report_pdf: soffice unexpected error during conversion: %s", exc)
        return None
# === ANCHOR: REPORT_PDF_SOFFICE_END ===


# === ANCHOR: REPORT_PDF_WORD_START ===
_WORD_TIMEOUT = 120  # seconds — Word cold-start can be slow on first launch.

# AppleScript that opens the .docx in Word, exports it as PDF, then closes the
# document without saving (leaving any pre-existing Word windows untouched).
_WORD_MAC_APPLESCRIPT = (
    'tell application "Microsoft Word"\n'
    '    open "{docx}"\n'
    "    set theDoc to active document\n"
    '    save as theDoc file name "{pdf}" file format format PDF\n'
    "    close theDoc saving no\n"
    "end tell"
)

# MS Word on macOS is sandboxed (com.apple.security.app-sandbox), so it can only
# read/write inside its own container without a (blocking) user file-access
# prompt.  Stage the docx/pdf there so AppleScript-driven open/save-as succeed
# unattended.  When the container is absent (non-sandboxed install), fall back
# to the default temp dir.
_WORD_MAC_CONTAINER = Path.home() / "Library/Containers/com.microsoft.Word/Data"


def _word_mac_staging_dir() -> str | None:
    """Return Word's sandbox container Data dir if present, else None."""
    return str(_WORD_MAC_CONTAINER) if _WORD_MAC_CONTAINER.is_dir() else None


def _applescript_escape(value: str) -> str:
    """Escape backslashes and double quotes for embedding in an AppleScript string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _convert_via_word(docx_bytes: bytes) -> bytes | None:
    """Dispatch to the platform-specific MS Word converter."""
    if sys.platform == "darwin":
        return _convert_via_word_mac(docx_bytes)
    if sys.platform == "win32":
        return _convert_via_word_win(docx_bytes)
    return None


def _convert_via_word_mac(docx_bytes: bytes) -> bytes | None:
    """Convert via MS Word on macOS using an AppleScript (``osascript``)."""
    try:
        with tempfile.TemporaryDirectory(dir=_word_mac_staging_dir()) as tmp:
            tmp_path = Path(tmp)
            docx_path = tmp_path / "report.docx"
            pdf_path = tmp_path / "report.pdf"
            docx_path.write_bytes(docx_bytes)

            script = _WORD_MAC_APPLESCRIPT.format(
                docx=_applescript_escape(str(docx_path)),
                pdf=_applescript_escape(str(pdf_path)),
            )
            result = subprocess.run(  # noqa: S603
                ["osascript", "-e", script],
                capture_output=True,
                timeout=_WORD_TIMEOUT,
            )

            if result.returncode != 0:
                logger.warning(
                    "report_pdf: Word(mac) osascript exited with code %d. stderr=%s",
                    result.returncode,
                    result.stderr.decode(errors="replace"),
                )
                return None

            if not pdf_path.exists():
                logger.warning(
                    "report_pdf: Word(mac) reported success but no PDF found at %s",
                    pdf_path,
                )
                return None

            return pdf_path.read_bytes()

    except subprocess.TimeoutExpired:
        logger.warning("report_pdf: Word(mac) timed out after %d s", _WORD_TIMEOUT)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("report_pdf: Word(mac) unexpected error during conversion: %s", exc)
        return None


def _convert_via_word_win(docx_bytes: bytes) -> bytes | None:
    """Convert via MS Word on Windows using COM automation (``pywin32``)."""
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        logger.warning("report_pdf: Word(win) requires pywin32 — %s", exc)
        return None

    tmp = tempfile.mkdtemp()
    docx_path = os.path.join(tmp, "report.docx")
    pdf_path = os.path.join(tmp, "report.pdf")
    Path(docx_path).write_bytes(docx_bytes)

    # convert_docx_to_pdf is dispatched via asyncio.to_thread, so COM must be
    # initialized on this worker thread.
    pythoncom.CoInitialize()
    word = None
    doc = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0  # wdAlertsNone
        doc = word.Documents.Open(docx_path, ReadOnly=True)
        doc.SaveAs(pdf_path, FileFormat=17)  # wdFormatPDF
        doc.Close(False)
        doc = None
        return Path(pdf_path).read_bytes()
    except Exception as exc:  # noqa: BLE001
        logger.warning("report_pdf: Word(win) unexpected error during conversion: %s", exc)
        return None
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:  # noqa: BLE001, S110
            pass
        pythoncom.CoUninitialize()
        shutil.rmtree(tmp, ignore_errors=True)
# === ANCHOR: REPORT_PDF_WORD_END ===
# === ANCHOR: REPORT_PDF_END ===
