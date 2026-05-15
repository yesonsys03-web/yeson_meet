#!/usr/bin/env python3
"""Generate card-style summary images from project Markdown docs.

Usage:
  OPENAI_API_KEY=... python3 generate_md_cards.py
  python3 generate_md_cards.py --dry-run

The script intentionally uses only the Python standard library so it can run
without installing the OpenAI package. It calls the OpenAI Images API directly.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_TARGETS = [
    PROJECT_ROOT / "docs" / "PRD.md",
    PROJECT_ROOT / "docs" / "ROADMAP.md",
    PROJECT_ROOT / "docs" / "ARCHITECTURE.md",
    PROJECT_ROOT / "docs" / "DEPLOY.md",
    PROJECT_ROOT / "docs" / "WORKFLOW_SOLO_AI.md",
    PROJECT_ROOT / "docs" / "WORKFLOW_COLLABORATION.md",
    PROJECT_ROOT / "docs" / "UI_DESIGN_SYSTEM.md",
    PROJECT_ROOT / "AI_Meeting_Dashboard_Mac_Windows_Workflow.md",
]


def slugify(path: Path) -> str:
    stem = path.with_suffix("").as_posix().replace("/", "__")
    stem = re.sub(r"[^A-Za-z0-9가-힣_.-]+", "-", stem)
    return stem.strip("-").lower()


def extract_summary(markdown: str, max_items: int = 10) -> dict[str, object]:
    title = "Document"
    headings: list[str] = []
    bullets: list[str] = []
    highlights: list[str] = []

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            text = re.sub(r"^#+\s*", "", line).strip()
            if text and title == "Document":
                title = text
            elif text:
                headings.append(text)
            continue
        if line.startswith(("- ", "* ")):
            bullets.append(line[2:].strip())
        elif line.startswith("|") and "---" not in line:
            compact = " ".join(cell.strip() for cell in line.strip("|").split("|") if cell.strip())
            if compact:
                highlights.append(compact)
        elif any(token in line for token in ("MVP", "Windows", "Gemini", "자막", "viewer", "Slice", "β")):
            highlights.append(line)

    def clean(items: list[str], limit: int) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            item = re.sub(r"[`*_>#]", "", item)
            item = re.sub(r"\s+", " ", item).strip()
            if len(item) > 110:
                item = item[:107].rstrip() + "..."
            if item and item not in seen:
                seen.add(item)
                result.append(item)
            if len(result) >= limit:
                break
        return result

    return {
        "title": title,
        "headings": clean(headings, 6),
        "bullets": clean(bullets, max_items),
        "highlights": clean(highlights, 6),
    }


def build_prompt(path: Path, summary: dict[str, object]) -> str:
    title = summary["title"]
    headings = "\n".join(f"- {item}" for item in summary["headings"])
    bullets = "\n".join(f"- {item}" for item in summary["bullets"])
    highlights = "\n".join(f"- {item}" for item in summary["highlights"])

    return f"""Create a polished Korean card-style infographic image summarizing this Markdown document.

Document file: {path.relative_to(PROJECT_ROOT)}
Document title: {title}

Important section headings:
{headings or "- 없음"}

Key points to visualize:
{bullets or "- 없음"}

Context highlights:
{highlights or "- 없음"}

Visual style:
- Single standalone card, 16:9 landscape, dark navy background (#0f172a), cyan accent (#38bdf8), white Korean typography.
- Make it easy for a non-engineer to understand in 10 seconds.
- Use 3-5 grouped panels with simple icons, arrows, badges, and timeline chips where useful.
- Preserve Korean labels where possible, but keep text short and large.
- Avoid dense paragraphs. No tiny text. No fake UI screenshots.
- Branding: small footer text "yeson-meet".
"""


def request_image(prompt: str, *, model: str, size: str) -> bytes:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": 1,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI Images API failed: HTTP {exc.code}: {detail}") from exc

    item = body.get("data", [{}])[0]
    if "b64_json" in item:
        return base64.b64decode(item["b64_json"])
    if "url" in item:
        with urllib.request.urlopen(item["url"], timeout=180) as response:
            return response.read()
    raise RuntimeError(f"Unexpected image response: {body}")


def resolve_targets(include_all_md: bool) -> list[Path]:
    if include_all_md:
        excluded_parts = {".git", ".omc", ".vibelign", "vibelign_exports"}
        return sorted(
            path
            for path in PROJECT_ROOT.rglob("*.md")
            if not any(part in excluded_parts for part in path.relative_to(PROJECT_ROOT).parts)
        )
    return [path for path in DEFAULT_TARGETS if path.exists()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--size", default="1536x1024")
    parser.add_argument("--out-dir", default="generated/md-cards")
    parser.add_argument("--all-md", action="store_true", help="include every non-hidden project Markdown file")
    parser.add_argument("--dry-run", action="store_true", help="write prompt files only; do not call the API")
    args = parser.parse_args()

    output_dir = PROJECT_ROOT / args.out_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, str]] = []
    targets = resolve_targets(args.all_md)
    if not targets:
        print("No Markdown targets found", file=sys.stderr)
        return 1

    for path in targets:
        markdown = path.read_text(encoding="utf-8")
        summary = extract_summary(markdown)
        prompt = build_prompt(path, summary)
        slug = slugify(path.relative_to(PROJECT_ROOT))
        prompt_path = output_dir / f"{slug}.prompt.txt"
        image_path = output_dir / f"{slug}.png"
        prompt_path.write_text(prompt, encoding="utf-8")

        status = "prompt-only"
        if not args.dry_run:
            image_bytes = request_image(prompt, model=args.model, size=args.size)
            image_path.write_bytes(image_bytes)
            status = "generated"
            print(f"generated {image_path.relative_to(PROJECT_ROOT)}")
        else:
            print(f"wrote prompt {prompt_path.relative_to(PROJECT_ROOT)}")

        manifest.append(
            {
                "source": str(path.relative_to(PROJECT_ROOT)),
                "prompt": str(prompt_path.relative_to(PROJECT_ROOT)),
                "image": str(image_path.relative_to(PROJECT_ROOT)),
                "status": status,
            }
        )

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
