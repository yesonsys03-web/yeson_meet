# 자막메이커 — 영문 잔존 구간 일괄 재번역 (설계)

작성일: 2026-07-16

## 문제

결과보기(`VideoReviewView`)에서 번역이 안 되고 **영문 원문 그대로 표시되는 구간**이 나온다. qwen(MLX/Ollama)과 Apple 온디바이스 양쪽에서 발생한다. 사용자가 그 구간을 일일이 찾아 수동 수정해야 하는데, 긴 영상에서는 현실적으로 불가능하다.

## 원인 — 버그가 아니라 "원문 유지" 폴백 3경로

영문이 그대로 남는 경로는 셋이고, **셋 다 원문(`text_en`)을 그대로 복사**한다.

| # | 위치 | 조건 | 대상 |
|---|---|---|---|
| 1 | `translate_mlx.py:85` | 환각 가드 `guard_mlx_ko` 불합격 (`english_leak`/`length_ratio`/`repetition`/`invented_number`/`foreign_script`/`empty`) | qwen |
| 2 | `translate.py:110` | `_translate_resilient`가 1줄까지 쪼개도 `TranslationError` | 전 provider |
| 3 | Apple EN→KO 언어팩 미설치 | Apple이 원문을 그대로 반환(오류 없음) | apple/apple_hifi |

경로 1은 **의도된 설계**다 — `translate_mlx.py:79` 주석이 명시한다: *"환각 가드: 불합격 줄은 원문(EN) 유지(검수 단계에서 눈에 띄게)"*. 이 기능은 그 설계가 전제한 검수 단계를 실제로 완성하는 것이다.

**셋 다 흔적을 저장하지 않는다** — 로그로만 남는다. 따라서 판별은 저장된 텍스트에서 해야 한다.

## 판별 규칙

`VideoSegment`에 `text_en`과 `text_ko`가 나란히 있다는 점을 이용한다.

1. **1차(정확)**: `text_ko.strip() == text_en.strip()` — 위 3경로가 전부 원문을 복사하므로 **오탐 없이** 전부 잡는다.
2. **2차(보조)**: ascii 알파벳 비율 > `_ASCII_LEAK_MAX`(=0.6) — 원문과는 다르지만 여전히 영어인 경우(가드가 없는 Claude/Apple 경로). **서버가 이미 쓰는 기준을 재사용**해 "english_leak" 정의를 서버와 일치시킨다. 새 매직넘버를 만들지 않는다.
   - 단 `_ASCII_LEAK_MAX`·`_ASCII_ALPHA_RE`는 `ai/mlx_live_translate.py`의 **private 상수**다. 그대로 import하면 캡슐화를 깨므로, 같은 모듈에 공개 헬퍼(예: `is_english_leak(text) -> bool`)를 노출하고 `guard_mlx_ko`가 그것을 쓰도록 정리한 뒤 재사용한다. 상수 복제는 금지 — 두 정의가 갈라진다.

**스키마 변경 없음.** 저장된 텍스트만으로 판별하므로 **이미 만들어진 기존 작업에 소급 적용**된다. (플래그를 새로 저장하는 방식은 신규 작업에만 적용돼 지금 문제를 못 푼다.)

알려진 오탐: 고유명사만 있는 줄(`"Margarita"`) 등 원문=번역이 정당한 경우. 재번역해도 대개 같은 결과가 나오므로 무해하다.

## 왜 기존 `rebuild`로는 안 되는가

`POST /{id}/rebuild`는 같은 소스·**같은 옵션**으로 파이프라인을 재실행한다. 따라서 (a) 같은 엔진이라 같은 실패가 재현되고, (b) **기존 검수 편집과 굽기 결과를 폐기**한다. 목적이 다르다.

## API

`POST /api/v1/video-jobs/{external_id}/retranslate`

요청: `{"provider": "claude", "cli_model": "..." | null}`

- `provider` 검증은 **`list_translate_engines()`에서 자동 도출한 기존 `_TRANSLATE_PROVIDER_PATTERN` 재사용**. v1.3.6의 qwen 422 사고(작업생성 검증 패턴에 새 엔진 추가를 빠뜨림)를 반복하지 않기 위한 필수 조건 — 패턴을 새로 하드코딩하지 않는다.
- `cli_model`은 기존 관례를 따라 opencode에서만 의미를 갖는다.

가드:
- 진행 중 작업이면 **409** (`rebuild`와 동일: status가 `review`/`done`/`error`/`cancelled`가 아니면 거부)
- 선택 provider가 `available`이 아니면 **409**. 무인증 API라 UI 비활성만으로는 못 막는다(기존 정책).

동작: 대상 세그먼트만 선택 → 기존 `create_translator` + `_translate_resilient`로 번역 → `text_ko` 갱신 → 커밋.

응답: `{"total": N, "retranslated": M, "remaining": K}`
- `total` = 판별된 영문 구간 수, `retranslated` = 실제로 한글로 바뀐 수, `remaining` = 재번역 후에도 여전히 영문인 수.

**동기 호출**(202/폴링 아님). 가드 불합격은 보통 전체의 일부라 수십 줄 규모다. 실제 잔존 규모가 커서 느리면 그때 백그라운드+진행률로 승격한다.

## 안전 속성

대상이 **판별식을 만족하는 줄로 한정**되므로 사용자가 직접 수정한 줄은 `text_ko != text_en`이 되어 **절대 덮어쓰지 않는다.** `rebuild`가 편집을 통째로 폐기하는 것과 대비된다.

굽기 결과(`burned_path`)는 기존 `patch_segments`와 **동일하게 유지**한다 — 자막 수정 후 다시 굽는 것은 수동 편집과 같은 흐름이다.

## UI (`VideoReviewView`)

상단 바에 추가:
- `영문 잔존 N구간` 배지 (클라가 판별식으로 계산)
- 엔진 `select` — `list_translate_engines()`의 `available`만 활성, 기본값 = 작업의 `translate_provider`가 아니라 **Claude 구독**(로컬 엔진 재시도는 같은 실패가 반복될 수 있으므로). opencode 선택 시 `cliModel` 입력 노출(기존 `VideoCaptionPanel` 관례).
- `[일괄 재번역]` 버튼 — N=0이면 비활성 + "영문 잔존 없음"
- 실행 중 스피너·버튼 비활성, 완료 후 `N개 중 M개 해결, K개 남음` + 세그먼트 재조회

각 영문 세그먼트에 **시각 표시**(배지/테두리)를 넣는다. "구간을 일일이 찾는 게 너무 어렵다"는 문제 자체를 해소하는 부분이라 범위에 포함한다.

## 컴포넌트 경계

- `translate.py::is_untranslated(text_en, text_ko) -> bool` — 판별 단일 진실. 서버 엔드포인트가 사용.
- 클라에도 같은 규칙이 필요(배지 카운트). TS로 재구현하되 **서버가 최종 판정자**다 — 클라 카운트는 표시용, 실제 대상 선정은 서버가 다시 한다. 두 구현이 어긋나도 데이터는 서버 규칙을 따른다.
- 신규 엔드포인트는 `video_jobs.py`에 기존 라우터 관례대로 추가.

## 테스트

**서버**
- `is_untranslated` 단위: 동일 텍스트 / ascii 과다 / 정상 한글 / 고유명사 / 빈 문자열 / 공백 차이
- 엔드포인트: 대상만 갱신 · **수동 편집 줄 미변경**(핵심 안전 속성) · 미가용 provider 409 · 진행 중 409 · **전 provider 값 수용**(패턴 드리프트 회귀 — v1.3.6 교훈) · 응답 카운트 정확성 · 재번역 후에도 영문이면 `remaining`에 반영

**클라**
- 배지 카운트, N=0 비활성, 재번역 후 재조회, vitest

## 범위 밖

- Apple 언어팩 자동 설치 유도 (별건)
- 가드 불합격 사유를 DB에 저장 (스키마 변경 필요, 소급 불가 — 지금 문제를 못 품)
- 로컬 엔진으로 조건 바꿔 재시도(샘플링/티어 변경) — 사용자가 Claude 구독 사용을 승인해 불필요
