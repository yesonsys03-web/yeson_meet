# 자막메이커 "로컬 번역 모델" 관리 탭 — 설계

- 날짜: 2026-07-14
- 목표: 전사 모델처럼 **번역 모델도 사용자가 앱에서 다운로드/삭제**. "전사 모델 관리"
  옆에 탭으로 "로컬 번역 모델" 추가.

## 배경

[[2026-07-14-ollama-qwen-translate-provider-design]]로 자막메이커 번역이 전 플랫폼
로컬 Qwen을 지원(실리콘=MLX / 그 외=Ollama). 하지만 모델 다운로드 UI가 없다.
전사 모델은 `VideoCaptionPanel.tsx` "전사 모델 관리" 섹션 + `video_models.py`/
`whisper_models.py`(데몬 스레드 다운로드 + 인메모리 진행률 폴링)로 관리된다. 같은
패턴을 번역 모델에 적용한다.

## 설계 — 플랫폼 적응형 통합 탭

"전사 모델 관리" 섹션 헤더를 **2탭**(`전사 모델` | `로컬 번역 모델`)으로.

**로컬 번역 모델 탭** = 드롭다운 3티어와 1:1 3행:

| 티어 값 | 라벨 | 실리콘(MLX) | 그 외(Ollama) |
|---|---|---|---|
| `qwen`      | Qwen 9B (로컬)           | Qwen3.5-9B-4bit (~5GB) | qwen3.5:9b (~6.6GB) |
| `qwen_lite` | Qwen 4B (로컬·빠름)      | Qwen3.5-4B-4bit (~2.3GB) | qwen3.5:4b (~3.4GB) |
| `qwen_hifi` | Qwen 9B (로컬·고품질 8bit)| Qwen3.5-9B-8bit (~10GB) | qwen3.5:9b-q8_0 (~10GB) |

- 각 행: 설치됨/미설치 칩 + 크기 + 진행률% + 다운로드/삭제 (전사 모델 행 UI 미러).
- **런타임 자동 선택**(create_translator와 동일 기준): 실리콘맥→MLX, 그 외→Ollama.
  "실리콘 통합"은 이 방식 — 같은 3행이 실리콘에선 MLX 모델을 받는다.
- Ollama 런타임인데 **미설치/미실행 → 안내 배너 + ollama.com 링크**, 다운로드 비활성.
  실리콘(MLX)은 배너 불필요.

## 백엔드 (`whisper_models.py`/`video_models.py` 패턴 미러)

### 신규 `domain/video_captions/translate_models.py`
- `_TIERS`(위 표). 런타임 = `_is_apple_silicon_mac()` ? mlx : ollama.
- 모델 참조: MLX=`QWEN_MLX_MODELS[tier]`, Ollama=`qwen_ollama_model(tier)`(둘 다 기존).
- `list_models() -> dict`: 티어별 {name,label,runtime,approx_bytes,downloaded,downloading,
  progress,downloadable} + 최상위 {runtime, ollama_installed, ollama_running}.
  - 설치 판정: MLX=`mlx_model_installed(repo)`, Ollama=`qwen_ollama_available(tag)`.
  - 진행률: MLX=디스크크기/approx(whisper와 동일), Ollama=`/api/pull` 스트림의 completed/total.
- `download_model(name)`: 데몬 스레드에서 블로킹. MLX=`snapshot_download(repo, mlx_model_dir)`,
  Ollama=`/api/pull`(stream) 진행률 갱신. `_downloading`/`_progress` 인메모리 + 락.
- `delete_model(name)`: MLX=`rmtree(mlx_model_dir)`, Ollama=`/api/delete`. 다운로드 중이면 거부.

### `translate_ollama.py` 헬퍼 추가
- `ollama_running()`(`/api/tags` 200?), `ollama_installed()`(which("ollama") or running),
  `pull_model(tag, on_progress)`(스트리밍), `delete_model(tag)`.

### 신규 라우터 `api/v1/translate_models.py` (prefix `/translate-models`, main.py 마운트)
- `GET ""` → list_models(). `POST "/{name}/download"`(202, 스레드 스폰; ollama 미실행이면 409).
  `DELETE "/{name}"`(204). **인증 없음**(video-models와 동일 LAN 신뢰 정책).

## 클라 (`videoApi.ts` + `VideoCaptionPanel.tsx`)
- `TranslateModelInfo` 타입 + `listTranslateModels`/`downloadTranslateModel`/`deleteTranslateModel`.
  목록 응답에 ollama_installed/running 포함.
- 모델 관리 섹션에 탭 상태(`transcribe`|`translate`) + 번역 모델 목록 렌더 + Ollama 배너.

## 테스트
- pytest: translate_models list(런타임별)/download 스폰(중복/이미설치 가드)/delete(다운로드중 거부),
  ollama pull/delete/running/installed(httpx monkeypatch), 라우터 202/204/409/404.
- vitest: videoApi 새 3함수 URL/메서드.
- Ollama 경로 = 이 인텔맥 실기 검증(설치/다운로드/삭제). MLX 다운로드 = 유닛만(실리콘 실기 필요).

## 비목표(YAGNI)
- 앱 내 Ollama 자동 설치(불가 — 링크 안내만).
- 실리콘에서 Ollama 대체 선택(런타임=MLX 고정, create_translator와 일치).
- 임의 태그/모델 추가 UI(카탈로그 3티어 + 기존 env 오버라이드로 충분).
