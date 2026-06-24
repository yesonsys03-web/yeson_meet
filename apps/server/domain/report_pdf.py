# === ANCHOR: REPORT_PDF_START ===
"""PDF report generation via LibreOffice soffice (S4).

Strategy: convert a .docx bytes payload → PDF bytes using
``soffice --headless --convert-to pdf``.  LibreOffice is an external
binary — not bundled — so absence is handled gracefully (returns None).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Well-known soffice install locations outside of PATH.
_FALLBACK_PATHS: list[str] = [
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",        # macOS
    r"C:\Program Files\LibreOffice\program\soffice.exe",           # Windows (64-bit)
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",     # Windows (32-bit)
    "/usr/bin/soffice",                                            # Linux
    "/snap/bin/libreoffice",                                       # Linux (snap)
]


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


# === ANCHOR: REPORT_PDF_CONVERT_START ===
def convert_docx_to_pdf(docx_bytes: bytes) -> bytes | None:
    """Convert *docx_bytes* to PDF bytes using soffice.

    Returns:
        PDF bytes on success, or ``None`` if soffice is unavailable or
        conversion fails.  Never raises — callers should treat ``None`` as
        "PDF unavailable" and return an appropriate HTTP error.
    """
    soffice = find_soffice()
    if soffice is None:
        logger.warning(
            "report_pdf: soffice not found — PDF conversion unavailable. "
            "Install LibreOffice to enable PDF export."
        )
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
        logger.warning("report_pdf: unexpected error during conversion: %s", exc)
        return None
# === ANCHOR: REPORT_PDF_CONVERT_END ===
# === ANCHOR: REPORT_PDF_END ===
