# 자막 메이커 GPU 가속 기획안 (2026-07-08)

## 결론 요약

자막 메이커는 현재 **전사(faster-whisper CPU int8)** 와 **굽기(ffmpeg libx264 소프트웨어 인코딩)** 두 구간이 CPU를 소모한다. GPU 활용 가능성은 구간·플랫폼별로 크게 갈린다:

| 구간 | NVIDIA (Win) | AMD (Win) | Intel iGPU (Win) | Metal (mac) | 이 개발 맥 (i9-9900K + RX 5700 XT) |
|---|---|---|---|---|---|
| 굽기(인코딩) | ◎ NVENC — ffmpeg에 이미 포함 | ○ AMF — 이미 포함 | ○ QSV — 포함 여부 실기기 확인 필요 | ○ VideoToolbox (Apple Silicon) | ✕ **실측 불가** (VT HW 인코더 안 열림) |
| 전사(whisper) | ○ CUDA (faster-whisper 지원, 단 패키징 부담 큼) | ✕ ROCm 미지원 | ✕ | ✕ CTranslate2 Metal 미지원 | ✕ |
| 번역 | — GPU 무관 (Gemini API / 구독 CLI) | — | — | — | — |

**권장 로드맵**: P0(GPU 아님·즉효: 인코딩 preset 명시, 실측 35% 단축) → P1(굽기 GPU 인코더 자동감지+폴백, ffmpeg 교체 불필요) → P2(옵트인: Windows NVIDIA 한정 전사 CUDA, "GPU 팩" 별도 다운로드) → P3(보류: whisper.cpp Vulkan/Metal 엔진 이원화).

**이 개발 맥에서는 GPU 가속이 사실상 불가능하다**(아래 실측). GPU의 실익은 Windows 실기기(특히 NVIDIA 장착 시)에서 나온다.

---

## 1. 현재 상태 (조사 결과)

파이프라인: `ingesting`(YouTube만) → `extracting`(preview+16kHz wav) → `transcribing` → `translating` → `review`(사용자 검수) → `burning` → `done`

CPU 소모 지점과 코드 위치:

1. **전사** — `apps/server/domain/video_captions/transcribe.py:30` `WhisperModel(model_dir, device="cpu", compute_type="int8")`. 로컬 faster-whisper(CTranslate2), 모델은 사용자가 클라 탭에서 HF 다운로드(`{STORAGE_ROOT}/whisper_models/`). CPU 경합 때문에 `pipeline.py:64` `_JOB_SEMAPHORE = asyncio.Semaphore(1)`로 **잡 전체가 1개씩 직렬 처리**된다 — 배치 처리량의 병목 근원.
2. **굽기** — `apps/server/domain/video_captions/ffmpeg.py:49-62`. `subtitles` 필터 + **`-c:v` 미지정** → ffmpeg 기본값(libx264, preset **medium**, crf 23)으로 풀 재인코딩. preset/스레드 튜닝 없음.
3. 번역 — Gemini 2.5 Flash 또는 구독 CLI(claude/codex/agy/opencode). GPU 무관.

ffmpeg 바이너리(서버 사이드에서 실행, `YESON_FFMPEG_BIN` 주입):
- mac: evermeet.cx 정적 빌드 — **videotoolbox 인코더 포함 확인**
- Windows: **BtbN win64 GPL 빌드 — NVENC/AMF 포함**(업스트림 표준 구성; QSV는 실기기에서 `-encoders` 확인 필요)
- Linux: johnvansickle 정적 빌드 — HW 가속 인코더 **없음**

하드웨어 가속 관련 코드는 현재 저장소에 전무(grep 0건).

## 2. 실측 (이 개발 맥, 2026-07-08)

환경: Intel i9-9900K(16스레드) + AMD Radeon RX 5700 XT 8GB(Metal 3), macOS 26.2, 번들 ffmpeg 8.1.2(evermeet). 소재: 30초 1080p30 합성 클립.

| 시나리오 | 시간 | 비고 |
|---|---|---|
| 굽기, 현행과 동일(libx264 medium + subtitles 필터) | 6.6초 | 현재 코드 경로 재현 |
| 굽기, libx264 **veryfast** + subtitles 필터 | 4.3초 | **-35%**. VMAF 실측: medium 96.6 vs veryfast 95.3(100점 만점, 1.3점 차 — 시각적 구분 불가 수준). 파일 크기도 이 소재에선 오히려 8% 작았음 |
| h264_videotoolbox (HW) | **실패** | 인코더 오픈 자체가 안 됨 — Intel맥+RX 5700 XT 조합에서 VT HW 인코딩 미노출 |
| h264_videotoolbox `-allow_sw 1` | 12.5초 | Apple SW 인코더 폴백 — libx264보다 3배 느림, 쓸모없음 |

**교훈 두 가지**:
- 이 맥에서는 GPU 인코딩 경로가 OS 레벨에서 막혀 있다. 전사 GPU도 불가(CTranslate2는 CUDA 전용 — Metal/ROCm 미지원). → **이 맥은 CPU 유지가 정답.**
- `-encoders` 목록에 인코더가 있어도 **실제로 열리는지는 별개**다. 감지는 목록 조회가 아니라 **실제 프로브 인코딩**으로 해야 한다(P1 설계의 근거).

## 3. 제안 로드맵

### P0 — 인코딩 파라미터 명시 (GPU 아님, 1줄 수정, 즉효)

`ffmpeg.py` 굽기 커맨드에 `-c:v libx264 -preset veryfast -crf 23` 명시. 실측 35% 단축, 리스크 거의 0. preview 트랜스코드(`ffmpeg.py:98`)는 이미 veryfast라 일관성도 맞는다.

### P1 — 굽기 GPU 인코더 자동감지 + 폴백

ffmpeg 바이너리 교체 없이 가능(Windows BtbN 빌드에 NVENC/AMF 기포함, mac evermeet에 VT 포함).

- **감지**: 서버 기동 시(또는 첫 굽기 시) 후보 인코더별 1초 프로브 인코딩 실행, 성공한 것만 사용 가능으로 캐시.
  ```
  ffmpeg -f lavfi -i testsrc2=size=1280x720:rate=30:duration=1 -c:v <candidate> -f null -
  ```
- **우선순위**: `h264_nvenc` → `h264_amf` → `h264_qsv` → `h264_videotoolbox` → `libx264`(폴백)
- **품질 매핑**: crf 23 상당 → NVENC `-cq 23 -preset p5`, AMF `-quality balanced -rc cqp`, VT `-b:v` 기반. GPU 인코더는 동일 비트레이트에서 x264보다 화질이 다소 낮으므로 비트레이트/품질 파라미터를 한 단계 여유 있게.
- **오버라이드**: `YESON_BURN_ENCODER` 환경변수(강제 지정/강제 libx264), 실패 시 런타임 폴백(굽기 시작 직후 인코더 오픈 실패 → libx264로 1회 재시도).
- **부수효과**: 굽기가 GPU로 빠지면 CPU가 비므로, 세마포어 구조를 "전사 1개 + 굽기 N개" 분리로 확장할 여지가 생긴다(현재도 `run_burn_job`은 세마포어 밖).

기대 효과: NVIDIA/AMD Windows에서 굽기 구간 3~10배 단축(해상도·GPU 세대에 따라 다름) + CPU 점유 해방. 검증은 Windows 실기기에서 프로브+실굽기로.

### P2 — 전사 CUDA 옵트인 (Windows NVIDIA 한정, 조건부)

전사가 배치 처리량의 지배 구간이므로 GPU 실익은 가장 크지만, 제약이 명확하다:

- faster-whisper(CTranslate2)는 **CUDA 전용** — AMD(ROCm)/Metal 미지원. 즉 NVIDIA Windows에서만 성립.
- 프로즌 번들에 CUDA/cuDNN DLL 동봉 시 **수백 MB~1GB 증가** → 기본 번들에 넣지 말고, whisper 모델 다운로드와 같은 방식의 **"GPU 팩" 옵트인 다운로드**로 제공.
- 감지: `nvidia-smi` 존재 + `ctranslate2.get_cuda_device_count() > 0` 이면 설정 탭에 "GPU 전사 사용" 토글 노출.
- 효과: whisper small 기준 CPU 대비 대략 5~10배(일반적 보고치; 실기기 실측 필요). `device="cuda", compute_type="float16"`.

### P3 — 보류: whisper.cpp 엔진 이원화 (AMD/Metal 전사)

AMD(Vulkan)·Apple Silicon(Metal)까지 전사 GPU를 넓히려면 whisper.cpp로의 엔진 교체/이원화가 필요하다. 세그먼트·word timestamp 호환 작업과 유지보수 이원화 비용이 커서 **P2 실측에서 전사 GPU 효과가 입증된 뒤에만** 재검토. (참고: local_whisper 폐기 결정은 *라이브 자막* 트랙 얘기고, 자막 메이커의 오프라인 배치 전사는 이미 로컬 whisper를 잘 쓰고 있으므로 그 결정과 충돌하지 않는다.)

## 4. 미검증/리스크 목록

- [ ] Windows 실기기: 장착 GPU 확인(`nvidia-smi` 또는 PowerShell `Get-CimInstance Win32_VideoController | Select Name`) — P1/P2의 실익이 이 결과에 달려 있음
- [ ] Windows 번들 ffmpeg에서 `-encoders` 실제 목록 + 프로브 인코딩 성공 여부(NVENC/AMF/QSV)
- [ ] GPU 인코딩 화질 검수(자막 가장자리 선명도) — cq/비트레이트 파라미터 튜닝
- [ ] 10bit/HDR 소스 입력 시 GPU 인코더 pix_fmt 협상(실패 시 libx264 폴백으로 흡수)
- [ ] Linux 배포를 살릴 거면 ffmpeg 빌드 교체 필요(존 밴시클 정적 빌드는 HW 인코더 없음) — 현재 우선순위 낮음
- [ ] P2 GPU 팩의 CUDA/cuDNN 버전 매트릭스(ctranslate2 휠과 호환되는 cuDNN 9.x)
