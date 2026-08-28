"""오버레이 계획과 사람의 편집 — **두 파일, 두 저자**.

왜 파일을 나누는가. 한 파일에 기계가 만든 베이스라인과 사람이 친 편집을 함께
두면 저자가 둘이 된다 — 파이프라인은 워커 스레드에서(`pdf_run.py`의
`asyncio.to_thread`), 편집 API는 이벤트 루프 코루틴에서 쓴다. 그 둘은 서로를
`asyncio.Lock`으로 막을 수 없고, 재번역이 `load → 병합 → save` 하는 사이에
들어온 편집은 `os.replace`가 조용히 덮어쓴다. 사람이 두 시간 걸려 넣은 라벨이
그렇게 사라진다.

그래서:

- **`overlay_plan.json` — 파이프라인 단독 저자.** 번역 실행이 만든 파생
  캐시다. 사람은 절대 쓰지 않는다. 재번역하면 통째로 새로 쓰인다.
- **`label_edits.json` — 편집 API 단독 저자.** 사람의 저작물이다. 파이프라인은
  **읽기만** 한다. 그래서 재번역이 이 파일을 건드릴 일이 없고, 수동 라벨은
  `(페이지, 판넬, 상대좌표)` 주소로 **구조적으로** 재부착된다 — 신원 키
  휴리스틱이 0개다.
- **`plan_status.json` — 파이프라인 단독 저자, 수백 바이트.** 목록 라우트가
  1.5초마다 폴링하는 경로에서 MB급 계획 파일을 파싱하지 않게 하려고, 목록에
  필요한 값만 따로 뽑아 둔다(이 리포가 명시적으로 금지한 패턴이다 —
  `pdf_run.py:104-106`).

`translated.pdf`는 이 둘을 합성해 다시 굽는 **산출물**이다. 이미 구운 PDF의
주석을 고치는 경로는 만들지 않는다 — `PdfDocument`(`backend.py:42`)가 주석에
대해 `add_freetext` 하나만 노출하는 것은 PyMuPDF(AGPL) 격리를 위한 의도된
설계이고, 여기에 읽기/수정/삭제를 더하면 그 격리가 깨진다.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .backend import PdfDocument
from .profiles.storyboard import PANEL_ADDRESS_REV

logger = logging.getLogger("yeson.pdf.overlay_plan")

SCHEMA_VERSION = 1

PLAN_FILENAME = "overlay_plan.json"
EDITS_FILENAME = "label_edits.json"
STATUS_FILENAME = "plan_status.json"

# 아직 굽지 않았거나 굽는 도중이라는 표식. §4.4의 `stale` 식이
# `baked_edits_version != edits_version`이므로, 이 값이면 어떤 편집 버전과도
# 다르다 = 항상 stale. 계획을 **지우지 않고** 이 값만 넣는 것이 핵심이다 —
# 지우면 취소가 오버레이 직전에 도착했을 때 멀쩡한 옛 PDF와 멀쩡한 편집 파일만
# 남고 계획이 사라져, 목록에서 사용자의 수동 라벨이 통째로 보이지 않게 된다.
UNBAKED = -1


def _r2(value: float) -> float:
    """좌표 반올림 — **레코드를 만드는 시점에** 단 한 곳에서 한다.

    직렬화 시점에 반올림하면 최초 굽기가 쓰는 메모리 값과 재굽기가 파일에서
    읽은 값이 최대 0.005pt 어긋나, "같은 계획인데 주석 위치가 미세하게 다른"
    상태가 된다. 만들 때 접어 두면 메모리와 파일이 언제나 같은 값이다.
    """
    return round(value, 2)


def _rect2(rect: Sequence[float]) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = rect
    return (_r2(x0), _r2(y0), _r2(x1), _r2(y1))


def is_usable_rect(rect: tuple[float, float, float, float],
                   page_size: tuple[float, float]) -> bool:
    """add_freetext에 넘기기 전 마지막 방어선(2026-07-30 리뷰 Finding 1b).

    profile.place()가 아무리 견고해도(스토리보드 프로파일은 이미
    비퇴화·온페이지를 보장하지만, 다른/미래 프로파일까지 같은 보장을
    한다는 계약은 없다) rect의 폭·높이가 0 이하이거나 완전히 페이지
    밖이면 PyMuPDF add_freetext_annot이 'rect is infinite or empty'로
    터진다 — 그 예외가 배치 루프 중간에서 나면 이미 끝낸 번역(최대
    수백~천 블록)까지 통째로 날아간다.

    사람이 드래그로 만든 rect도 같은 방어선을 통과해야 하므로, 이제
    파이프라인뿐 아니라 편집 API도 이 함수를 쓴다(`pdf_run`에서 이리로
    옮긴 이유 — 참조처 2곳·테스트 참조 0건을 확인하고 이동했다).
    """
    x0, y0, x1, y1 = rect
    page_w, page_h = page_size
    if not (x1 > x0 and y1 > y0):
        return False
    return x1 > 0.0 and y1 > 0.0 and x0 < page_w and y0 < page_h


# ── 모델 ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlanItem:
    """기계가 만든 주석 하나. `id`는 **계획 생성마다 새로 발급하는 난수**다.

    내용 해시가 아니다 — 두 규칙이 사용자에게 보이는 동작을 가르기 때문에
    여기서 확정한다: 난수면 재번역 시 자동 라벨에 한 사람의 수정(override)이
    전량 무효가 되어 목록에 표시되고, 결정적 해시면 변하지 않은 페이지의
    수정이 재번역을 넘어 살아남는다. v1은 난수를 쓰고, 재번역을 넘는 자동 라벨
    수정 보존은 의도적으로 범위 밖이다(수동 라벨은 주소로 재부착되므로 영향
    없다). 같은 페이지에 동일 텍스트·동일 bbox 블록이 둘일 때 해시가 충돌하는
    문제도 함께 피한다.
    """
    id: str
    kind: str
    page: int
    panel_index: int | None
    rect: tuple[float, float, float, float]
    fontsize: float
    source_text: str
    text: str


@dataclass(frozen=True)
class OverlayPlan:
    job_id: str
    profile: str
    page_count: int
    page_sizes: list[tuple[float, float]]
    items: list[PlanItem]
    plan_version: int = 1
    baked_edits_version: int = UNBAKED
    address_rev: int = PANEL_ADDRESS_REV
    built_at: str = ""


@dataclass(frozen=True)
class ManualLabel:
    """사람이 넣은 라벨. **절대 rect를 저장하지 않는다.**

    저장하면 재번역 후에도 옛 좌표에 그대로 찍혀 AC11(주소 재부착)이 그 순간
    무너진다. 드래그 PATCH도 그 페이지의 판넬 rect를 얻어 `rel`로 역변환해
    저장한다.

    `panel_rect`·`address_rev`는 **드리프트 감지와 orphan 재지정 UI 전용**이며
    배치에는 쓰지 않는다 — 배치는 언제나 현재 `panels()` 결과를 쓴다.
    """
    id: str
    page: int
    panel_index: int
    rel: tuple[float, float]
    size: tuple[float, float]
    fontsize: float
    source_text: str
    text: str
    panel_rect: tuple[float, float, float, float] | None = None
    address_rev: int = PANEL_ADDRESS_REV


@dataclass(frozen=True)
class Override:
    """자동 라벨 하나에 대한 사람의 수정/삭제. `target` = 계획 item id."""
    target: str
    page: int
    text: str | None = None
    rect: tuple[float, float, float, float] | None = None
    deleted: bool = False


@dataclass(frozen=True)
class LabelEdits:
    job_id: str
    edits_version: int = 0
    manual: list[ManualLabel] = field(default_factory=list)
    overrides: list[Override] = field(default_factory=list)
    updated_at: str = ""

    def item_count(self) -> int:
        """프루닝 판정용 — 파일 존재가 아니라 **내용**으로 본다."""
        return len(self.manual) + len(self.overrides)


@dataclass(frozen=True)
class PlacedItem:
    """합성이 끝나 '어디에 무엇을 찍을지'가 확정된 항목."""
    page: int
    rect: tuple[float, float, float, float]
    text: str
    fontsize: float
    origin: str          # "auto" | "manual"
    item_id: str
    kind: str = "panel_label"
    source_text: str = ""
    edited: bool = False        # 자동 항목에 사람의 수정이 적용됐는가
    panel_index: int | None = None   # 수동 라벨의 주소(자동은 None)


@dataclass(frozen=True)
class ComposeResult:
    placed: list[PlacedItem]
    # 주소를 잃은 수동 라벨 — 다른 판넬로 **추정하지 않는다**. 레코드는 그대로
    # 보존되고, 판넬이 되돌아오면 다음 굽기에서 자동 복귀한다.
    unresolved: list[ManualLabel]
    # 계획에 없는 target을 겨냥한 override(재번역으로 id가 바뀐 경우).
    dangling: list[Override]
    deleted: int = 0


@dataclass(frozen=True)
class ApplyStats:
    annots: int
    out_of_range: int
    unusable_rect: int


# ── 파일 IO (원자 교체) ───────────────────────────────────────────────────────

def plan_path(job_dir: Path) -> Path:
    return job_dir / PLAN_FILENAME


def edits_path(job_dir: Path) -> Path:
    return job_dir / EDITS_FILENAME


def status_path(job_dir: Path) -> Path:
    return job_dir / STATUS_FILENAME


def _write_json(dest: Path, payload: dict) -> None:
    """tmp → `os.replace` 원자 교체. 쓰다 만 JSON이 관측되지 않게 한다."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, dest)


def _read_json(src: Path) -> dict | None:
    """어떤 파손에도 예외를 파이프라인으로 올리지 않는다.

    계획·편집 파일이 깨졌다고 번역 자체가 실패하면 안 된다 — 읽기 실패는
    `None`으로 수렴시키고 경고만 남긴다(호출부가 "없는 것"과 같이 다룬다).
    """
    if not src.exists():
        return None
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("overlay_plan: %s 를 읽을 수 없어 무시한다", src.name,
                       exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("schema_version")
    if isinstance(version, int) and version > SCHEMA_VERSION:
        # 더 새 버전이 쓴 파일은 **덮어쓰지 않는다** — 구버전 앱이 신버전
        # 편집을 지우는 사고를 막는다. 호출부는 이 None을 보고 409로 끝내거나
        # (API) 재부착을 건너뛴다(파이프라인).
        logger.warning("overlay_plan: %s 의 schema_version=%s 가 이 버전(%s)보다 "
                       "새롭다 — 건드리지 않는다", src.name, version, SCHEMA_VERSION)
        return None
    return data


def save_plan(job_dir: Path, plan: OverlayPlan) -> None:
    _write_json(plan_path(job_dir), {
        "schema_version": SCHEMA_VERSION,
        "plan_version": plan.plan_version,
        "baked_edits_version": plan.baked_edits_version,
        "address_rev": plan.address_rev,
        "job_id": plan.job_id,
        "profile": plan.profile,
        "built_at": plan.built_at,
        "page_count": plan.page_count,
        "page_sizes": [list(s) for s in plan.page_sizes],
        "items": [{
            "id": it.id, "kind": it.kind, "page": it.page,
            "panel_index": it.panel_index, "rect": list(it.rect),
            "fontsize": it.fontsize,
            "source_text": it.source_text, "text": it.text,
        } for it in plan.items],
    })
    save_plan_status(job_dir, plan)


def load_plan(job_dir: Path) -> OverlayPlan | None:
    data = _read_json(plan_path(job_dir))
    if data is None:
        return None
    return OverlayPlan(
        job_id=str(data.get("job_id", "")),
        profile=str(data.get("profile", "")),
        page_count=int(data.get("page_count", 0)),
        page_sizes=[tuple(s) for s in data.get("page_sizes", [])],
        items=[PlanItem(
            id=str(d["id"]), kind=str(d["kind"]), page=int(d["page"]),
            panel_index=d.get("panel_index"),
            rect=tuple(d["rect"]), fontsize=float(d["fontsize"]),
            source_text=str(d.get("source_text", "")), text=str(d["text"]),
        ) for d in data.get("items", [])],
        plan_version=int(data.get("plan_version", 1)),
        baked_edits_version=int(data.get("baked_edits_version", UNBAKED)),
        address_rev=int(data.get("address_rev", PANEL_ADDRESS_REV)),
        built_at=str(data.get("built_at", "")),
    )


def save_plan_status(job_dir: Path, plan: OverlayPlan) -> None:
    """목록 라우트가 읽을 소형 파일 — 계획과 **같은 저자**가 함께 쓴다."""
    _write_json(status_path(job_dir), {
        "schema_version": SCHEMA_VERSION,
        "plan_version": plan.plan_version,
        "baked_edits_version": plan.baked_edits_version,
        "item_count": len(plan.items),
        "page_count": plan.page_count,
    })


def load_plan_status(job_dir: Path) -> dict | None:
    return _read_json(status_path(job_dir))


def save_edits(job_dir: Path, edits: LabelEdits) -> None:
    _write_json(edits_path(job_dir), {
        "schema_version": SCHEMA_VERSION,
        "edits_version": edits.edits_version,
        "job_id": edits.job_id,
        "updated_at": edits.updated_at or datetime.now(UTC).isoformat(),
        "manual": [{
            "id": m.id, "page": m.page, "panel_index": m.panel_index,
            "rel": list(m.rel), "size": list(m.size), "fontsize": m.fontsize,
            "source_text": m.source_text, "text": m.text,
            "panel_rect": None if m.panel_rect is None else list(m.panel_rect),
            "address_rev": m.address_rev,
        } for m in edits.manual],
        "overrides": [{
            "target": o.target, "page": o.page, "text": o.text,
            "rect": None if o.rect is None else list(o.rect),
            "deleted": o.deleted,
        } for o in edits.overrides],
    })


def load_edits(job_dir: Path, *, job_id: str = "") -> LabelEdits:
    """편집 파일이 없으면 **빈 편집**을 돌려준다 — 없는 것과 비어 있는 것을
    호출부가 구분할 이유가 없다(둘 다 "사람이 아직 손대지 않았다")."""
    data = _read_json(edits_path(job_dir))
    if data is None:
        return LabelEdits(job_id=job_id)
    return LabelEdits(
        job_id=str(data.get("job_id", job_id)),
        edits_version=int(data.get("edits_version", 0)),
        manual=[ManualLabel(
            id=str(d["id"]), page=int(d["page"]),
            panel_index=int(d["panel_index"]),
            rel=tuple(d["rel"]), size=tuple(d["size"]),
            fontsize=float(d.get("fontsize", 10.0)),
            source_text=str(d.get("source_text", "")), text=str(d["text"]),
            panel_rect=(None if d.get("panel_rect") is None
                        else tuple(d["panel_rect"])),
            address_rev=int(d.get("address_rev", PANEL_ADDRESS_REV)),
        ) for d in data.get("manual", [])],
        overrides=[Override(
            target=str(d["target"]), page=int(d.get("page", 0)),
            text=d.get("text"),
            rect=None if d.get("rect") is None else tuple(d["rect"]),
            deleted=bool(d.get("deleted", False)),
        ) for d in data.get("overrides", [])],
        updated_at=str(data.get("updated_at", "")),
    )


def mark_baked(plan: OverlayPlan, edits_version: int) -> OverlayPlan:
    """이 계획으로 구운 PDF에 **실제로 합성한** 편집 스냅샷의 버전을 새긴다.

    반드시 `compose`에 넘긴 바로 그 스냅샷의 값이어야 한다 — 저장 시점에 편집
    파일을 다시 읽으면, 굽는 사이에 들어온 편집까지 "구웠다"고 기록해 `stale`이
    거짓으로 정합을 보고한다. 틀리려면 **낮은 쪽으로** 틀려야 안전하다.
    """
    return OverlayPlan(
        job_id=plan.job_id, profile=plan.profile, page_count=plan.page_count,
        page_sizes=plan.page_sizes, items=plan.items,
        plan_version=plan.plan_version, baked_edits_version=edits_version,
        address_rev=plan.address_rev, built_at=plan.built_at)


def invalidate_baked_version(job_dir: Path) -> int:
    """굽기 진입 시 계획을 **무효화**한다(삭제가 아니다). 이전 `plan_version`을 돌려준다.

    지우지 않는 이유는 모듈 docstring과 `UNBAKED` 주석에 있다 — 요약하면,
    지우면 취소·중단 시 "멀쩡한 PDF + 멀쩡한 편집 + 계획 없음" 상태가 되어
    목록에서 사용자의 수동 라벨이 통째로 사라지고 잡이 되살릴 길 없이 막힌다.
    """
    plan = load_plan(job_dir)
    if plan is None:
        return 0
    save_plan(job_dir, OverlayPlan(
        job_id=plan.job_id, profile=plan.profile, page_count=plan.page_count,
        page_sizes=plan.page_sizes, items=plan.items,
        plan_version=plan.plan_version, baked_edits_version=UNBAKED,
        address_rev=plan.address_rev, built_at=plan.built_at))
    return plan.plan_version


# ── 계획 산출 ────────────────────────────────────────────────────────────────

def build_plan(doc: PdfDocument, profile, blocks: list, ko_by_block: list,
               *, job_id: str, plan_version: int = 1) -> OverlayPlan:
    """현행 오버레이 루프의 **결정 부분만** 옮긴 것 — 무엇을 어디에 찍을지 정한다.

    찍는 일은 `apply_composed`가 한다. 둘을 나눠야 사람의 편집을 사이에 끼워
    넣을 수 있고, 재굽기가 번역을 다시 하지 않아도 된다.

    `refine_ko`는 **여기(자동 경로)에만** 적용한다 — 판정식이 `[A-Za-z]{2,}`라
    사람이 일부러 친 라틴 라벨(`CAM`·`BG`)을 통째로 지운다.
    """
    refine_ko = getattr(profile, "refine_ko", None)
    place_with_doc = getattr(profile, "place_with_doc", None)

    # ⚠ 자동 항목의 `panel_index`는 **여기서 계산하지 않는다**(항상 None).
    #
    # 처음엔 판넬 라벨이 있는 페이지에서만 `panels()`를 부르는 것으로 예산을
    # 잡았는데, S0-b 실측이 그 예산을 깼다(GABE01 1037p):
    #   overlaying 5.185s → 23.217s (+348%), 그중 panels 18.931s /138 호출,
    #   깨진 페이지 복구 재지불만 9.868s.
    # AC13의 "기준 overlaying 대비 +20%"를 크게 넘는다.
    #
    # 계획이 준비해 둔 폴백 둘 중 ⓐ(`extract`가 계산한 판넬 재사용)는 중복
    # 9.868s를 없애도 나머지 9.06s(138 × 약 66ms)가 남아 **여전히 초과**한다.
    # 그래서 ⓑ를 택했다: 자동 항목의 `panel_index`는 §4.3대로 **표시·필터
    # 전용이고 배치에 쓰이지 않으므로**, 굽는 동안 계산할 이유가 없다.
    # 편집 화면은 현재 페이지의 `/panels`를 이미 받으므로 그 페이지에 한해
    # 클라이언트가 히트 테스트로 파생한다 — 서버 비용 0.
    #
    # 수동 라벨의 `panel_index`는 영향이 없다. 그건 계산값이 아니라 사람이
    # 고른 **주소**이고 편집 파일에 저장된다.
    items: list[PlanItem] = []
    placed_by_page: dict[int, list] = {}
    # ★배치는 **위에서 아래로** 훑는다(2026-08-26). 엑스시트 배치가 좌우를
    # 번갈아 쓰는 지그재그라, 블록이 세로 순서로 오지 않으면 그 번갈음이
    # 무작위가 된다(실측: 세로 오름차순 페이지가 187개 중 40개(21%)뿐).
    # ⚠추출이 아니라 **여기서** 정렬하는 이유: 추출 결과는 캐시되므로
    # 추출 쪽에 두면 캐시가 적중한 런에서 조용히 우회된다 — 실제로 그렇게
    # 우회됐다(추출 캐시 지문이 람다 안의 변경을 못 봐서 적중). 배치 순서는
    # 배치 단계가 책임진다.
    order = sorted(range(len(blocks)),
                   key=lambda i: (blocks[i].page, blocks[i].bbox[1],
                                  blocks[i].bbox[0]))
    for i in order:
        block = blocks[i]
        ko = ko_by_block[i]
        # 번역 실패 폴백(원문 복사)·빈 결과는 주석을 달지 않는다
        if ko is None:
            continue
        if refine_ko is not None:
            ko = refine_ko(block, ko)
            if not ko:
                # 다듬고 나니 붙일 게 없다 — 주석을 만들지 않는다.
                continue
        page_size = doc.page_size(block.page)
        # `place_with_doc`는 `refine_ko`와 같은 **선택** 훅이다(Protocol에
        # 없다). 페이지 그림이 있어야 후보 자리 중 빈 곳을 고를 수 있는
        # 프로파일(엑스시트: 손글씨를 피해 앉아야 한다)만 구현한다.
        # 배치 이력(occupied)도 넘긴다 — 잉크만 피하면 이웃 블록끼리 같은
        # 빈자리를 골라 주석이 포개진다(A2 실측 심한 겹침 91쌍, 사람 0쌍).
        ov = (place_with_doc(block, ko, page_size, doc,
                             occupied=placed_by_page.get(block.page, ()))
              if place_with_doc is not None
              else profile.place(block, ko, page_size))
        if not is_usable_rect(ov.rect, page_size):
            # 방어선(2026-07-30 리뷰 Finding 1b) — 그 한 블록 때문에 이미 끝낸
            # 번역까지 잃을 수는 없으니 이 블록만 건너뛰고 경고를 남긴다.
            logger.warning(
                "pdf-translate: page %d %s block의 rect가 유효하지 않아 "
                "주석을 건너뜀 %r", block.page, block.kind, ov.rect)
            continue
        items.append(PlanItem(
            id=uuid4().hex[:12], kind=block.kind, page=ov.page,
            panel_index=None, rect=_rect2(ov.rect),
            fontsize=ov.fontsize, source_text=block.text, text=ov.text))
        placed_by_page.setdefault(ov.page, []).append(ov.rect)

    return OverlayPlan(
        job_id=job_id, profile=getattr(profile, "name", ""),
        page_count=doc.page_count,
        page_sizes=[tuple(doc.page_size(p)) for p in range(doc.page_count)],
        items=items, plan_version=plan_version,
        baked_edits_version=UNBAKED,
        built_at=datetime.now(UTC).isoformat())


# ── 합성과 배치 ──────────────────────────────────────────────────────────────

def compose(plan: OverlayPlan, edits: LabelEdits,
            panels_for_page: Callable[[int], Sequence | None]) -> ComposeResult:
    """계획 + 편집 → 찍을 것들.

    판넬 해석자를 **주입**받는다 — 수동 라벨의 절대 rect는 그 페이지의 판넬
    rect가 있어야 계산되는데, 그 계산은 문서를 열어야 한다. 함수 자체는
    순수하게 두고 문서 접근은 호출부가 책임진다(목록 라우트는 요청당 1회만
    열고, 수동 라벨이 있는 페이지에 한해 부른다).
    """
    by_target: dict[str, Override] = {o.target: o for o in edits.overrides}
    known = {it.id for it in plan.items}
    dangling = [o for o in edits.overrides if o.target not in known]

    placed: list[PlacedItem] = []
    deleted = 0
    for it in plan.items:
        ov = by_target.get(it.id)
        if ov is not None and ov.deleted:
            deleted += 1
            continue
        text = ov.text if (ov is not None and ov.text) else it.text
        rect = (_rect2(ov.rect) if (ov is not None and ov.rect is not None)
                else it.rect)
        placed.append(PlacedItem(page=it.page, rect=rect, text=text,
                                 fontsize=it.fontsize, origin="auto",
                                 item_id=it.id, kind=it.kind,
                                 source_text=it.source_text,
                                 edited=ov is not None))

    unresolved: list[ManualLabel] = []
    for m in edits.manual:
        panels = panels_for_page(m.page)
        if panels is None or not (0 <= m.panel_index < len(panels)):
            # 주소를 잃었다 — **다른 판넬로 추정하지 않는다.** 조용한 오배치가
            # 조용한 소실보다 낫지 않다. 레코드는 보존되고 목록에 표시된다.
            unresolved.append(m)
            continue
        px0, py0, px1, py1 = panels[m.panel_index]
        x0 = px0 + m.rel[0] * (px1 - px0)
        y0 = py0 + m.rel[1] * (py1 - py0)
        placed.append(PlacedItem(
            page=m.page, rect=_rect2((x0, y0, x0 + m.size[0], y0 + m.size[1])),
            text=m.text, fontsize=m.fontsize, origin="manual", item_id=m.id,
            kind="panel_label", source_text=m.source_text,
            panel_index=m.panel_index))

    return ComposeResult(placed=placed, unresolved=unresolved,
                         dangling=dangling, deleted=deleted)


def apply_composed(doc: PdfDocument, placed: Sequence[PlacedItem], *,
                   should_continue: Callable[[], bool] | None = None,
                   on_progress: Callable[[int], None] | None = None,
                   check_every: int = 200) -> ApplyStats | None:
    """찍는다 — **`add_freetext` 외의 주석 API는 쓰지 않는다**(AC12).

    `should_continue`가 False를 돌려주면 즉시 중단하고 `None`을 반환한다.
    `to_thread` 바디는 바깥 await이 취소돼도 스스로 멈추지 않으므로, 취소를
    실제로 관측 가능하게 만들려면 루프가 직접 확인해야 한다.

    배치 제외는 **정확히 세 가지**다: 삭제(합성 단계에서 이미 빠졌다) /
    페이지 범위 밖 / `is_usable_rect` 실패. 해석 불가 항목은 애초에 `placed`에
    들어오지 않으므로 이 셋의 예외가 아니다.
    """
    annots = out_of_range = unusable = 0
    for i, item in enumerate(placed):
        if (should_continue is not None and i % check_every == 0
                and not should_continue()):
            return None
        if on_progress is not None and i % check_every == 0:
            on_progress(i)
        if not (0 <= item.page < doc.page_count):
            out_of_range += 1
            continue
        page_size = doc.page_size(item.page)
        if not is_usable_rect(item.rect, page_size):
            logger.warning(
                "pdf-translate: page %d %s 항목의 rect가 유효하지 않아 "
                "주석을 건너뜀 %r", item.page, item.origin, item.rect)
            unusable += 1
            continue
        doc.add_freetext(item.page, item.rect, item.text,
                         fontsize=item.fontsize)
        annots += 1
    if on_progress is not None:
        on_progress(len(placed))
    return ApplyStats(annots=annots, out_of_range=out_of_range,
                      unusable_rect=unusable)


# ── 편집 변형 (전부 순수 함수: 편집 in → 편집 out) ───────────────────────────
#
# 파일 IO도 락도 여기 없다 — 라우트가 잡별 락 안에서 read-modify-write 한다.
# 순수하게 두면 동시성 없이 규칙만 테스트할 수 있다.

def _next(edits: LabelEdits, *, manual=None, overrides=None) -> LabelEdits:
    """버전을 올린 새 편집 — 낙관적 동시성 토큰이 여기서만 증가한다."""
    return LabelEdits(
        job_id=edits.job_id, edits_version=edits.edits_version + 1,
        manual=edits.manual if manual is None else manual,
        overrides=edits.overrides if overrides is None else overrides,
        updated_at=datetime.now(UTC).isoformat())


def add_manual(edits: LabelEdits, label: ManualLabel) -> LabelEdits:
    return _next(edits, manual=[*edits.manual, label])


def patch_manual(edits: LabelEdits, item_id: str, *, text: str | None = None,
                 rel: tuple[float, float] | None = None,
                 size: tuple[float, float] | None = None,
                 panel_rect: tuple[float, float, float, float] | None = None,
                 ) -> LabelEdits:
    """수동 라벨 수정. **rect가 아니라 `rel`을 저장한다** — 절대 좌표를 저장하면
    재번역 후에도 옛 자리에 찍혀 AC11(주소 재부착)이 그 순간 무너진다."""
    out = []
    for m in edits.manual:
        if m.id != item_id:
            out.append(m)
            continue
        out.append(ManualLabel(
            id=m.id, page=m.page, panel_index=m.panel_index,
            rel=m.rel if rel is None else rel,
            size=m.size if size is None else size,
            fontsize=m.fontsize, source_text=m.source_text,
            text=m.text if text is None else text,
            panel_rect=m.panel_rect if panel_rect is None else panel_rect,
            address_rev=m.address_rev))
    return _next(edits, manual=out)


def repoint_manual(edits: LabelEdits, item_id: str, *, page: int,
                   panel_index: int, rel: tuple[float, float] | None = None,
                   panel_rect: tuple[float, float, float, float] | None = None,
                   ) -> LabelEdits:
    """주소를 잃은 수동 라벨을 **사람이 직접** 다른 판넬로 다시 붙인다.

    `page`도 받는다 — 페이지 자체가 사라진 orphan은 판넬 번호만으로는 복구할
    수 없다. 시스템이 추정하지 않고 사람이 고르는 것이 이 경로의 요점이다.
    """
    out = []
    for m in edits.manual:
        if m.id != item_id:
            out.append(m)
            continue
        out.append(ManualLabel(
            id=m.id, page=page, panel_index=panel_index,
            rel=m.rel if rel is None else rel, size=m.size,
            fontsize=m.fontsize, source_text=m.source_text, text=m.text,
            panel_rect=panel_rect, address_rev=PANEL_ADDRESS_REV))
    return _next(edits, manual=out)


def delete_manual(edits: LabelEdits, item_id: str) -> LabelEdits:
    return _next(edits, manual=[m for m in edits.manual if m.id != item_id])


def upsert_override(edits: LabelEdits, target: str, *, page: int,
                    text: str | None = None,
                    rect: tuple[float, float, float, float] | None = None,
                    deleted: bool | None = None) -> LabelEdits:
    """자동 라벨에 대한 사람의 수정/삭제를 덮어쓴다(없으면 만든다)."""
    found = False
    out: list[Override] = []
    for o in edits.overrides:
        if o.target != target:
            out.append(o)
            continue
        found = True
        out.append(Override(
            target=target, page=page,
            text=o.text if text is None else text,
            rect=o.rect if rect is None else rect,
            deleted=o.deleted if deleted is None else deleted))
    if not found:
        out.append(Override(target=target, page=page, text=text, rect=rect,
                            deleted=bool(deleted)))
    return _next(edits, overrides=out)


def purge_dangling(edits: LabelEdits, known_ids: set[str]) -> LabelEdits:
    """계획에 없는 target을 겨냥한 override만 정리한다.

    ⚠ **수동 라벨은 어떤 상태에서도 이 경로로 삭제되지 않는다.** 주소를 잃은
    수동 라벨(unresolved)은 판넬이 되돌아오면 다음 굽기에서 자동 복귀하는데,
    "무효 항목 정리" 버튼이 그것까지 지우면 되돌아올 예정이던 사람의 라벨이
    영구 소멸한다(버전 파일 누적은 Non-Goal이라 복구 수단도 없다).
    """
    return _next(edits, overrides=[o for o in edits.overrides
                                   if o.target in known_ids])


def panels_resolver(doc: PdfDocument, profile) -> Callable[[int], Sequence | None]:
    """`compose`에 넘길 판넬 해석자 — 페이지당 한 번만 계산하고 캐시한다.

    수동 라벨이 같은 페이지에 여러 개면 `panels()`가 그만큼 반복 호출되는데,
    그 함수는 깨진 페이지에서 300dpi 렌더 + 단어별 OCR을 돈다.
    """
    hook = getattr(profile, "panels", None)
    cache: dict[int, Sequence | None] = {}

    def resolve(page: int) -> Sequence | None:
        if hook is None:
            return None
        if page not in cache:
            cache[page] = (hook(doc, page)
                           if 0 <= page < doc.page_count else None)
        return cache[page]

    return resolve
