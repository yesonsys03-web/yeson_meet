# 슬레이트 OCR 기반 씬별 분할 익스포트 — 설계

- 날짜: 2026-07-16
- 대상 기능: 자막메이커(Video Caption Studio) 결과보기에 "씬별 분할 익스포트" 추가
- 상태: 설계 승인 대기

## 1. 목적

편집실에서 내려온 영상에는 하단에 **슬레이트 오버레이**(샷 이름·씬 번호·타임코드)가 원본에 구워져 있다.
사용자는 자막메이커로 한글 자막까지 입힌 최종본(`burned.mp4`)을 **슬레이트 텍스트 기준으로 씬별/시퀀스별로 잘라** 개별 파일로 내보내고 싶다.

슬레이트 포맷은 작품마다 다르다. 두 실제 예시:

| 예시 | 시퀀스 | 씬/샷 |
|---|---|---|
| `HH0307_020_0150_AC_v01` | `020` | `0150` |
| `Seq 07_S08 - Panel 3` | `Seq 07` | `S08` |

→ 파서를 하드코딩하지 않는다. OCR이 텍스트를 읽고, 사용자가 **한 번** "어느 토큰이 시퀀스/씬인지" 규칙만 지정한다.

## 2. 핵심 결정 (확정)

1. **대상 파일**: `burned.mp4` — 슬레이트 + 한글 자막이 모두 있는 최종본. 잡이 `done` 상태여야 진입 가능.
2. **입력 방식**: OCR 자동 판독 + 사용자는 규칙(토큰 선택)만 지정. 경계는 자동 계산.
3. **검증 UX**: 다빈치 리졸브식 **필름스트립** — 썸네일 트랙에 컷 라인·구간 라벨을 얹어 잘리는 구간을 시각적으로 보여주고, 사용자가 확인·미세조정 후 익스포트.
4. **자르기**: **재인코딩(정확)**. 컷을 키프레임으로 반올림하는 무손실 복사(`-c copy`)는 쓰지 않는다. 슬레이트 편집본은 컷 경계가 명확해야 하므로 프레임 정확도를 우선한다.
5. **OCR 엔진**: **RapidOCR (onnxruntime)**. 서버 번들은 이미 `--collect-all onnxruntime`을 하고 있어(build-server.sh) 새 시스템 바이너리가 필요 없다. 오프라인 동작.
6. **두 가지 익스포트 모드**: 시퀀스별 / 씬별. 같은 경계 데이터에서 그룹 키만 달리한다.

## 3. 전체 흐름

```
[결과보기 · done 잡]
   │  "씬별 분할" 열기
   ▼
① 프레임 스캔:  ffmpeg로 1초 간격 썸네일 + OCR용 프레임 추출
② OCR:          RapidOCR로 각 프레임의 슬레이트 텍스트 판독
③ 규칙 지정:    대표 프레임 OCR 텍스트를 토큰화 → 사용자가 [시퀀스][씬] 클릭 (1회)
④ 경계 계산:    프레임별 (시퀀스,씬) 값이 바뀌는 지점 = 컷
⑤ 필름스트립:   썸네일 트랙 + 컷 라인 + 구간 라벨 → 사용자 확인·미세조정
⑥ 익스포트:     "시퀀스별" / "씬별" 모드로 ffmpeg 재인코딩 세그먼트 → 폴더 저장
```

## 4. 컴포넌트 설계

### 4.1 백엔드 — `apps/server/domain/video_captions/scene_split.py` (신규)

책임: 프레임 추출, OCR, 토큰화, 경계 계산, 세그먼트 자르기. 순수 함수 위주로 두고 API 레이어에서 조립한다.

주요 함수(초안):

- `scan_frames(job_dir, interval_s=1.0) -> list[FrameSample]`
  ffmpeg `-vf fps=1/interval` 로 프레임 PNG를 임시 디렉토리에 추출. 각 샘플 = `{index, t_ms, image_path}`.
  썸네일은 축소본(예: 높이 90px)을 별도로 뽑아 필름스트립 전송용으로 쓴다.
- `ocr_frames(samples) -> list[FrameOCR]`
  RapidOCR로 각 프레임 판독 → `{t_ms, text, boxes}`. 슬레이트 라인은 "구분자로 토큰화 가능한 가장 긴/신뢰도 높은 라인"을 후보로 선택.
  RapidOCR 인스턴스는 프로세스당 1회 초기화(모델 로드 비용 큼).
- `tokenize(text) -> list[str]`
  구분자 `_`, 공백, `-` 로 분해. 예: `Seq 07_S08 - Panel 3` → `["Seq 07", "S08", "Panel 3"]`,
  `HH0307_020_0150_AC_v01` → `["HH0307","020","0150","AC","v01"]`.
- `apply_rule(frame_ocrs, rule) -> list[FrameKey]`
  규칙 = `{seq_tokens: [idx...], scene_tokens: [idx...]}`. 각 프레임에서 해당 토큰을 뽑아 시퀀스 키·씬 키 문자열을 만든다.
  판독 실패/빈 프레임은 직전 유효 키로 채운다(홀드). 1프레임짜리 튐은 무시(최소 지속시간 필터).
- `compute_boundaries(frame_keys, mode) -> list[Segment]`
  `mode ∈ {"sequence","scene"}`. 키가 바뀌는 지점을 컷으로. 각 `Segment = {label, start_ms, end_ms}`.
- `cut_segments(src_mp4, segments, out_dir, name_template) -> list[Path]`
  각 세그먼트를 ffmpeg 재인코딩(`-ss start -to end -c:v libx264 -c:a aac`)으로 저장. 파일명 = 슬레이트 라벨.

경계 정밀화(선택, 후속): 1초 샘플로 대략 잡은 뒤 경계 ±1초 구간만 프레임 단위 재OCR해 컷을 좁힐 수 있다. MVP에서는 1초 정밀 + 필름스트립 수동 조정으로 충분.

### 4.2 저장 — 잡 디렉토리 `scenes.json` (신규 DB 없음)

`$STORAGE_ROOT/video_jobs/<external_id>/scenes.json`:

```json
{
  "rule": { "delimiters": ["_"," ","-"], "seq_tokens": [1], "scene_tokens": [1,2] },
  "interval_ms": 1000,
  "frames": [ { "t_ms": 0, "text": "HH0307_020_0150_AC_v01" }, ... ],
  "segments_scene":    [ { "label": "HH0307_020_0150_AC_v01", "start_ms": 0, "end_ms": 23000 }, ... ],
  "segments_sequence": [ { "label": "HH0307_020", "start_ms": 0, "end_ms": 83000 }, ... ]
}
```

DB 테이블/컬럼을 추가하지 않는다(번들 additive 마이그레이션 부담 회피 — 프로젝트 메모리 참고). 규칙과 경계는 잡 디렉토리 파일로만 산다.

### 4.3 API — `apps/server/api/v1/video_jobs.py` 확장

- `POST /{id}/scenes/scan` — 프레임 스캔 + OCR 실행, `frames`(썸네일 URL·OCR 텍스트) 반환. 오래 걸리면 기존 진행률 패턴(`progress`)을 재사용해 폴링.
- `POST /{id}/scenes/rule` — 사용자가 정한 규칙 저장 + 경계 재계산, `segments_scene`/`segments_sequence` 반환.
- `PATCH /{id}/scenes/segments` — 필름스트립에서 사용자가 손본 경계/라벨 반영.
- `POST /{id}/scenes/export` — `mode`(scene|sequence)로 재인코딩 세그먼트 생성. 완료 후 파일 목록/저장 경로 반환. 데스크톱은 기존 Tauri 저장 다이얼로그로 대상 폴더 선택.

취소·진행률은 기존 굽기 파이프라인의 프로세스 레지스트리(`ffmpeg.py`의 `_ACTIVE`/`_KILLED`)와 세대 카운터 패턴을 재사용한다.

### 4.4 프론트엔드 — 데스크톱

- `apps/desktop/src/console/VideoReviewView.tsx` — `done` 잡에 "씬별 분할" 진입 버튼 추가.
- `apps/desktop/src/console/SceneSplitView.tsx` (신규) — 규칙 지정 + 필름스트립 + 익스포트 화면.
- `apps/desktop/src/console/SceneFilmstrip.tsx` (신규) — 썸네일 트랙 + 드래그 가능한 컷 라인 + 구간 라벨. 모드 토글(씬/시퀀스).
- API 호출은 기존 `videoApi.ts` 패턴에 함수 추가.

필름스트립 레이아웃(개념):

```
┌────────────────────────────────────────────────────────┐
│ [썸][썸][썸]│[썸][썸]│[썸][썸][썸][썸]│[썸][썸]  ← 썸네일 트랙
│            ⋮        ⋮                ⋮       ← 드래그 가능한 컷
│  020_0150  │020_0160│   020_0170     │ 021_0010  ← 구간 라벨
│  0:00-0:23 │  -0:41 │    -1:52       │           ← 시간
└────────────────────────────────────────────────────────┘
   모드: (◉ 씬별  ○ 시퀀스별)      [16개 클립 익스포트]
```

## 5. 규칙 지정 UX 상세

대표 프레임(첫 유효 OCR 프레임)의 텍스트를 토큰 칩으로 보여준다:

```
OCR이 읽음:   Seq 07_S08 - Panel 3
토큰:        [Seq 07] [S08] [Panel 3]
             (클릭→시퀀스) (클릭→씬)

예시1:       [HH0307] [020] [0150] [AC] [v01]
              (고정)   (시퀀스) (씬)
```

- 시퀀스 키 = 선택된 시퀀스 토큰들의 결합. 씬 키 = 시퀀스 토큰 + 씬 토큰 결합(씬은 시퀀스 안에서 유일해야 하므로).
- 파일명은 슬레이트 원문 라벨을 그대로 쓴다:
  - 씬별: `HH0307_020_0150_AC_v01.mp4`
  - 시퀀스별: `HH0307_020.mp4` (시퀀스 키만; 필요 시 고정 접두 포함)
- 규칙은 `scenes.json`에 저장되어 재실행 시 자동 적용.

## 6. 번들 / 의존성 변경

- `apps/server/pyproject.toml`: `rapidocr-onnxruntime` 의존성 추가(onnxruntime은 이미 faster-whisper 전이의존).
- `apps/server_desktop/scripts/build-server.sh` + `build-server.ps1`: pyinstaller 호출에 `--collect-all rapidocr_onnxruntime` (모델 파일 포함) 한 줄 추가. onnxruntime `--collect-all`은 이미 있음.
- 모델 파일(~10MB, det+rec+cls)만 번들 증가. 새 시스템 바이너리·ffprobe 불필요.

## 7. 에러 처리 · 엣지 케이스

- **OCR 판독 실패 프레임**: 직전 유효 키로 홀드. 연속 실패가 임계 초과면 필름스트립에 "판독 불가 구간"으로 표시해 사용자가 병합/삭제.
- **1프레임 튐(오독)**: 최소 지속시간(예: 2초) 미만 구간은 인접 구간에 흡수.
- **슬레이트가 없는 영상**: 스캔 후 토큰화 가능한 라인이 없으면 "슬레이트를 찾지 못함"으로 안내하고 수동 경계 입력 폴백(후속).
- **잡이 아직 굽기 전(`review`)**: 이 기능은 `done`(=`burned.mp4` 존재)에서만 진입. 그 전엔 버튼 비활성.
- **취소**: 스캔/익스포트 중 취소 시 기존 프로세스 레지스트리로 ffmpeg·OCR 워커 종료.

## 8. 범위 밖 (YAGNI / 후속)

- 프레임 단위 경계 자동 정밀화(1초 → 프레임). MVP는 1초 + 수동 조정.
- 슬레이트 없는 영상의 자동 씬 감지(장면 전환 검출).
- 클라우드 OCR(Gemini) 폴백.
- 시퀀스별 파일에 자막 재타이밍(현재는 그대로 잘라 넣으므로 자막 타이밍 보존됨 — 추가 작업 불필요).

## 9. 테스트 전략

- `tokenize`/`apply_rule`/`compute_boundaries`는 순수 함수 → 두 실제 슬레이트 예시로 pytest 단위 테스트(회귀 잠금).
  - `HH0307_020_0150_AC_v01` 규칙 → 시퀀스 `020`, 씬 `020_0150` 검증.
  - `Seq 07_S08 - Panel 3` 규칙 → 시퀀스 `Seq 07`, 씬 `Seq 07_S08` 검증.
- OCR은 고정 프레임 이미지 픽스처로 판독 결과 스냅샷 테스트.
- `cut_segments`는 짧은 합성 mp4로 세그먼트 개수·경계 근사 검증.
- 프론트 필름스트립은 경계 배열 → 렌더 구간 매핑 로직 위주.
```
