# PDF 스토리보드 번역 배치 — 원문 아래 우선 (필드 박스 하단 상한) — 설계

- 날짜: 2026-07-31
- 대상 기능: 스토리보드 프로파일이 번역 주석을 놓는 위치
- 상태: 설계 승인 완료(2026-07-31) · 구현 완료
- 관련 커밋: `d3a86c5`(우측 배치 도입) · `fd7b1cd`(시프트업 제거)

## 1. 목적

번역 주석이 원문 **오른쪽 좁은 칸**에 들어가 4줄로 접히고 폰트가 10pt로 줄어든다.
사람 납품본은 같은 자리에서 **원문 바로 아래 전폭 12pt 한 줄**로 쓴다 — 그쪽이 읽기 편하다.
아래에 자리가 있으면 아래로 놓는다.

## 2. 현행 동작과 실측 (GABE01_A1, 373페이지)

`storyboard.py:179` — 원문 오른쪽 여유가 `_MIN_RIGHT_WIDTH`(180pt) 이상이면 무조건 우측.

| 항목 | 값 |
|---|---|
| 페이지 | 1008 × 612 |
| Action Notes 박스 | `(24.0, 525.7, 985.1, 588.0)` |
| 원문 영어 bbox | `(27.0, 546.7, 790.3, 557.8)` — 1줄 |
| 오른쪽 여유 | `1008 - 8 - (790.3 + 8) = 201.7pt` → 문턱 통과 → **우측 선택** |
| 우측 폭에서 12pt | 4줄 = 67.5pt → 페이지 하단 초과 → **10pt로 축소** |
| 결과 rect 하단 | 590.4 — **박스 하단 588.0을 넘어 흘러넘침** |
| 사람 납품본 | `(27.4, 557.1, 747.4, 569.3)` — 원문 아래 전폭 12pt 1줄 |
| 아래 여유 | `588.0 - 557.8 = 30.2pt` (12pt 2줄분) |

## 3. 근거 — 우측 배치의 원래 명분은 이미 소멸했다

우측 배치는 `d3a86c5` "주석 오른쪽 배치(**원문 가림 제거**)"로 들어왔다.
그러나 가림의 진범은 아래 배치가 아니라 **박스가 페이지 하단을 넘으면 위로 밀어 올리던 코드**였다
(`d3a86c5` 이전 `place()`의 `y0 = max(0.0, y1 - height)`).
그 시프트업은 이후 `fd7b1cd`에서 아래 경로 한정 `allow_shift=False`로 따로 제거됐다.
**지금의 아래 경로는 원문을 덮지 않는다** — 07-30 당시의 전제가 더 이상 성립하지 않는다.

## 4. 근거 — 사람의 실제 규칙 (전 1037페이지 줄 단위 실측)

- Action Notes가 1줄이라 아래 여유가 있으면 → **원문 아래 전폭**(373p, 401p)
- 영문이 여러 줄이라 박스가 꽉 차면 → 각 영문 줄 **오른쪽 공통 거터**(2p: 3줄 모두 x=324.9 정렬)
- 대사는 → `Dialog` 라벨 **바로 오른쪽 첫 줄**(21p, 101p, 401p)

즉 사람도 "아래가 되면 아래, 안 되면 오른쪽"이다. 이번 범위는 첫 번째 규칙만 구현한다.

## 5. 근거 — 효과 정량 (필드 1022건, 번역 길이는 사람 납품본 대용)

| 필드 | 아래 배치 가능(12pt) | 불가 |
|---|---|---|
| Action Notes | 233 (**87%**) | 36 |
| Dialog | 390 (52%) | 363 |
| 합계 | 631 (**61.7%**) | 391 |

현행 코드는 이 중 **1021건(99.9%)을 우측**으로 보낸다.
아래를 우선하면 62%가 수동본 레이아웃이 되고, 나머지 38%는 지금 그대로 우측에 남는다.

## 6. 핵심 결정 (확정)

1. **아래 우선, 박스 하단이 상한.** `room = limit_y - (원문 y1 + _GAP)` 안에 들어가면 아래 전폭.
   폭은 `(박스 우측 - 8) - 원문 x0`.
2. **축소 사다리는 10pt에서 끊는다.** 사람은 전부 12pt를 썼고, 실측상 10pt가 더해주는 몫은 0.8%뿐이다.
   9/8pt까지 내려 전폭에 우겨넣는 것보다 그 지점부터는 우측이 낫다.
3. **아래가 안 되면 현행 `_place_right_or_below` 그대로.** 우측 경로의 기하·시프트업·경고 로그는 무수정.
4. **박스 하단은 도형에서 읽는다(상수 하드코딩 금지).** 기하가 다른 6페이지와 다른 템플릿(FL102 등)에
   자동으로 맞는다. 못 읽으면 폴백 2단계를 거쳐 최종적으로 `None` → **현행 동작 그대로**.
5. **우측 경로가 박스 하단을 넘는 문제는 이번 범위 밖.** 지금도 590.4까지 흘러넘치지만, 같이 조이면
   남은 38%에서 클리핑이 늘어난다. 별건으로 남긴다.
6. **패널 콜아웃 라벨(`_place_panel_label`)은 무수정.** `limit_y`는 필드 블록에만 실린다.

## 7. 구현

### 7.1 `PdfBlock`에 `limit_y` 추가 (`profiles/base.py`)

```python
@dataclass(frozen=True)
class PdfBlock:
    page: int
    kind: str
    text: str
    bbox: tuple[float, float, float, float]
    limit_y: float | None = None   # 이 블록이 속한 필드 박스의 하단(pt)
```

기본값이 있으므로 패널 라벨 등 기존 생성부는 무수정 — 하위호환.

### 7.2 백엔드에 `page_rects` 추가 (`backend.py`, `backend_mupdf.py`)

```python
def page_rects(self, page: int) -> list[tuple[float, float, float, float]]: ...
```

MuPDF 구현은 `get_drawings()`의 `rect`를 그대로 돌려준다(필터링은 프로파일 몫).
`PdfDocument` Protocol이 한 칸 늘어난다 — 백엔드 교체(pypdfium2) 시 함께 구현해야 한다.

### 7.3 `extract()`가 `limit_y`를 계산 (`profiles/storyboard.py`)

필드 블록을 만들 때 3단 폴백으로 상한을 정한다:

1. `page_rects(page)` 중 그 원문 bbox를 감싸는 **가장 작은** 사각형의 `y1`
   (폭 300pt 초과 + 높이 15pt 초과만 후보 — 패널 테두리·표 셀 오탐 배제)
2. 없으면 다음 필드 라벨의 `y0 - _GAP` (`_field_content`가 이미 `upper_bound`로 계산 중인 값)
3. 그것도 없으면 `None`

### 7.4 `place()` 분기 (`profiles/storyboard.py`)

```
if block.kind == _PANEL_LABEL_KIND:  → 현행
if block.limit_y is None:            → 현행 _place_right_or_below
room  = block.limit_y - (bbox.y1 + _GAP)
width = (박스 우측 - 8) - bbox.x0
for fs in (12.0, 10.0):
    if _estimate_height(ko, width, fs) <= room:  → 아래 배치, fontsize=fs
→ 아니면 _place_right_or_below (현행)
```

박스 우측을 모르면(폴백 2·3 경로) `page_w - 8`을 쓴다 — 현행 아래 경로와 같은 값.

## 8. 변경 파일

| 파일 | 변경 |
|---|---|
| `apps/server/domain/pdf_translate/profiles/base.py` | `PdfBlock.limit_y` 필드(기본 None) |
| `apps/server/domain/pdf_translate/backend.py` | `PdfDocument.page_rects` Protocol 추가 |
| `apps/server/domain/pdf_translate/backend_mupdf.py` | `page_rects` = `get_drawings()` rect |
| `apps/server/domain/pdf_translate/profiles/storyboard.py` | `_field_box`(상한 산출) + `place()` 아래 우선 분기 + `_place_below` |
| `apps/server/tests/test_pdf_profiles.py` | 신규 7건 + 기존 테스트 docstring 정정 |

## 9. 검증

- **단위**
  - 박스 여유 충분 → rect가 원문 **아래**, 폭이 전폭, `fontsize == 12.0`, `rect.y1 <= limit_y`
  - 여유가 12pt엔 부족하고 10pt엔 충분 → 아래 배치 `fontsize == 10.0`
  - 여유 없음(`room < 10pt 1줄`) → **우측**(현행 경로) 선택
  - `limit_y=None` → 현행과 **완전 동일한** rect (하위호환 회귀 잠금)
  - 전 경로 공통: 원문 bbox 비교차 + `y1 > y0`(퇴화 아님) + 온페이지
  - 기존 `place()` 테스트는 모두 `PdfBlock`을 직접 만들어 `limit_y`가 `None`이므로 경로가
    바뀌지 않는다 — 조정이 필요한 것은 `extract()`를 거치는
    `test_place_returns_rect_within_page_and_not_intersecting_source`의 docstring뿐이다.
    실제 상한이 있는 우측 경로는 신규 `test_place_falls_back_to_right_when_box_has_no_room_below`가
    덮는다
- **주의**: `apps/server` 테스트는 `conftest.py`가 Postgres DSN을 하드코딩해 수집 시점에 접속한다.
  프로파일 테스트는 DB를 쓰지 않으므로 `pytest apps/server/tests/test_pdf_profiles.py`를 직접 경로로
  지정해 돌린다(root `pyproject`의 `testpaths` 함정도 같이 회피).
- **실물** (2026-07-31 검증 완료·재검증, `verify_below.py`로 373p·2p·21p 3페이지 재배치):
  373페이지 Action Notes가 원문 아래로 이동함을 확인 —
  `rect=(27.0, 561.8, 977.1, 584.3)`, `12.0pt`(rect 하단 584.3 ≤ 박스 하단 588.0).
  x0·y0는 사람 납품본 `(27.4, 557.1, 747.4, 569.3)`과 근사(±5pt, `_GAP` 4pt 포함)하며
  이번엔 폰트도 12pt로 사람과 일치한다. 2p(다중행 → 우측 유지,
  `rect=(331.0, 546.7, 1000.0, 584.2)`, 12.0pt)와 21p(Dialog 꽉 참 → 우측 유지,
  `rect=(192.0, 481.3, 1000.0, 503.8)`, 12.0pt) 회귀 없음 확인.

  **정정 이력**: 최초 검증(2026-07-31 오전)은 373p에서 `10.0pt`
  (`rect=(27.0, 561.8, 790.3, 580.6)`)를 관찰했었다 — 당시
  `_place_below_in_box`의 폭이 §6.1이 정한 "박스 우측까지"가 아니라
  `x1 = min(right, max(bx1, bx0 + _MIN_WIDTH))`로 **원문 자체의 x1(763.3pt)**에
  캡돼 있어, 검증용 한국어 문구가 12pt 1줄에 들어가지 못하고 10pt로 한 단
  내려갔다. 이는 §6.1·§7.4가 명시한 폭 규칙과 어긋난 구현 결함이었다
  (설계가 아니라 코드가 틀렸던 것) — 같은 날 리뷰로 발견해
  `x1 = right`(박스 우측 - 8)로 고쳤고, 위 재검증 수치가 그 결과다.

## 10. 남은 것 (이번 범위 밖)

- 우측 경로가 필드 박스 하단을 넘는 문제(373p에서 590.4 > 588.0)
- 사람의 **줄 단위 우측 거터 정렬**(2p: 각 영문 줄 옆 공통 x)
- 사람의 **Dialog 라벨줄 슬롯**(21p·101p·401p: `Dialog` 라벨 오른쪽 첫 줄) — 아래가 막힌 Dialog 363건이 대상
