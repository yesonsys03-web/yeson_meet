# 서드파티 구성요소 고지 (Third-Party Notices)

yeson-meet 클라이언트·서버 콘솔 설치본에 **동봉되어 함께 배포되는** 서드파티
구성요소와 각 라이선스를 밝힌다. 배포하는 쪽에 고지·소스 제공 의무가 따라오는
항목(카피레프트)을 먼저 적고, 허용적 라이선스는 목록으로 정리한다.

- 작성: 2026-07-31 (v1.8.0 기준)
- 갱신 시점: 동봉 구성요소를 추가·제거하거나 버전을 올릴 때
- 이 문서는 법률 자문이 아니다. 판단이 필요한 항목은 §5에 따로 모았다.

---

## 1. 배포 규모 (판단의 전제)

| 항목 | 실측 |
|---|---|
| 배포 경로 | GitHub Releases (공개 리포 `yesonsys03-web/yeson_meet`) |
| 설치본 누적 다운로드 | **108건** (전 34개 릴리스 합계, 2026-07-31 기준) |
| 릴리스당 다운로드 | **2~3건** |
| 용도 | 사내 회의 자막·제작 문서 번역 도구 |

공개 리포이므로 이론상 누구나 받을 수 있으나, 실제 배포량은 사내 인원 규모다.
아래 §5의 판단은 이 수치를 전제로 한다 — **전제가 바뀌면(외부 판매, 배포 확대,
유상 공급) 재검토해야 한다.**

---

## 2. 카피레프트 — FFmpeg (GPL)

| | |
|---|---|
| 구성요소 | FFmpeg (BtbN 빌드 `win64-gpl-8.1` / macOS는 osxexperts.net·evermeet.cx) |
| 버전 | `n8.1.2-32-gcfa62de001` (Windows·Linux) / `8.1`·`8.1.2` (macOS) |
| 라이선스 | **GPL** (빌드 플래그에 `--enable-gpl`, `--enable-nonfree` 없음 = 재배포 가능) |
| 결합 방식 | **별도 실행 파일을 자식 프로세스로 호출**(`subprocess.run([ffmpeg, …])`, `apps/server/domain/video_captions/ffmpeg.py`) — 라이브러리 링크 아님 |
| 용도 | 영상 자막 굽기, 오디오 추출, 씬 분할용 크롭 |
| 핀 관리 | `apps/server_desktop/ffmpeg.lock.json` (URL + sha256 고정) |

**동봉 빌드의 구성** (실제 바이너리에서 확인, 2026-07-31):
`--enable-gpl` `--enable-libass` `--enable-libfreetype` `--enable-fontconfig`
— `--enable-nonfree`는 **없음**.

### 대응 소스

GPL 바이너리를 배포하므로 대응 소스를 제공해야 한다. 아래를 통해 제공한다:

- FFmpeg 원본 소스: <https://ffmpeg.org/download.html> — 동봉 버전은 상류 태그
  `n8.1.2` 계열이다
- 빌드 스크립트(Windows·Linux 빌드를 만든 구성): <https://github.com/BtbN/FFmpeg-Builds>

> ⚠ **BtbN의 개별 autobuild 릴리스 URL을 소스 근거로 걸면 안 된다.** BtbN은
> autobuild를 약 11일치만 보관하고 오래된 태그를 통째로 삭제한다 — 실제로
> 2026-07-31 v1.8.0 빌드가 이 만료로 실패했다. 위처럼 **버전(태그)과 빌드
> 스크립트 리포지토리**를 가리켜야 링크가 썩지 않는다.

---

## 3. 카피레프트 — PyMuPDF (AGPL) ✅ AGPL-3.0 채택으로 해소

| | |
|---|---|
| 구성요소 | PyMuPDF / `fitz` (Artifex, MuPDF 바인딩) |
| 버전 | 1.28.0 |
| 라이선스 | **AGPL-3.0 또는 Artifex 상용 라이선스 (이중)** |
| 결합 방식 | **파이썬 라이브러리로 임포트**(`import fitz`) — 별도 프로세스 아님 |
| 용도 | 스토리보드 PDF 번역(텍스트 추출·주석 오버레이·페이지 래스터) |
| 격리 지점 | `apps/server/domain/pdf_translate/backend.py` 인터페이스 — 구현체는 `backend_mupdf.py` **한 파일** |

**FFmpeg와 성격이 다르다.** FFmpeg는 자식 프로세스로 호출해 "별도 프로그램"
논리가 서지만, PyMuPDF는 **임포트해서 같은 프로세스에서 링크**되므로 결합
저작물 논리가 훨씬 강하다. 즉 §2의 방어가 여기엔 적용되지 않는다.

전제도 바뀌었다. 이 의존성을 도입할 때의 기록은 "사내 LAN 전용이라 실질 리스크
낮음, 외부 배포 시 재검토"였는데(`docs/pdf-translation-feasibility-2026-07-29.md`),
**v1.8.0으로 이 기능이 공개 다운로드 가능한 설치본에 들어갔다.**

→ **2026-08-03 결정: 앱 전체를 AGPL-3.0으로 배포**한다(리포 루트 `LICENSE`).
Artifex가 제시한 이중 라이선스 중 AGPL 쪽을 그대로 따르는 것이다. 근거와
남는 의무는 §5.1 참조.

---

## 4. 허용적 라이선스 구성요소

동봉되지만 고지 외 별도 의무가 사실상 없는 것들. (전이 의존성 전체를 열거한
목록은 아니며, 동결 번들에 명시적으로 수집되는 주요 항목이다.)

| 구성요소 | 버전 | 라이선스 | 용도 |
|---|---|---|---|
| cloudflared | 2026.6.1 | Apache-2.0 | 퀵터널(외부 뷰어 공개) |
| onnxruntime | 1.23.2 | MIT | OCR 추론 런타임 |
| RapidOCR (onnxruntime) | 1.4.4 | Apache-2.0 | 슬레이트·패널 라벨 OCR |
| OpenCV (`cv2`) | 5.0.0.93 | Apache-2.0 | 이미지 전처리 |
| CTranslate2 | 4.8.1 | MIT | 자막 전사 추론 |
| faster-whisper | 1.2.1 | MIT | 음성 전사 |
| PyAV (`av`) | 18.0.0 | BSD-3-Clause | 미디어 디먹싱 |
| yt-dlp | 2026.7.4 | Unlicense | 유튜브 입력 |
| python-docx | 1.2.0 | MIT | 보고서 Word 출력 |
| Shapely | 2.1.2 | BSD-3-Clause | OCR 기하 연산 |
| pyclipper | 1.4.0 | MIT | 동상 |
| google-genai | 2.10.0 | Apache-2.0 | Gemini 라이브 자막 |
| google-cloud-speech | 2.39.0 | Apache-2.0 | STT(옵션 경로) |
| FastAPI | 0.136.1 | MIT | 서버 프레임워크 |
| Uvicorn | 0.47.0 | BSD-3-Clause | ASGI 서버 |
| SQLAlchemy | 2.0.49 | MIT | ORM |
| aiosqlite | 0.22.1 | MIT | SQLite 비동기 드라이버 |
| lxml | 6.1.1 | BSD-3-Clause | XML 처리 |
| NumPy | 2.4.4 | BSD-3-Clause 외 | 수치 연산 |
| Pillow | 12.3.0 | MIT-CMU | 이미지 |
| requests | 2.34.2 | Apache-2.0 | HTTP |

Apple Silicon 전용 경로에는 MLX(`mlx`, `mlx-lm`, MIT)가 추가로 들어간다.
Tauri(Rust)·프런트엔드(npm) 의존성 트리는 각 매니페스트(`Cargo.toml`,
`package.json`)와 락파일을 참조한다.

---

## 5. 판단이 필요했던 항목 (결정 기록)

### 5.1 PyMuPDF AGPL — **AGPL-3.0 채택으로 해소 (2026-08-03 결정)**

검토했던 선택지와 판단 근거:

| 방안 | 비고 | 판정 |
|---|---|---|
| **앱 전체를 AGPL-3.0으로 공개** | 코드 변경 0·비용 0. 저작권자(Artifex)가 제시한 두 선택지 중 하나를 그대로 따르는 것 | **채택** |
| Artifex 상용 라이선스 취득 | 독점 유지 시 가장 명확. 코드 무변경·품질 위험 0. 비용 발생 | 보류(독점 유지 필요 없음) |
| PDF 백엔드 교체 (pypdfium2 + pypdf) | **가능함이 실증됐으나 드롭인이 아니다** — §5.1.1 | 불필요 |
| 리포 비공개 전환 | **효과 없음** — 의무는 소스 공개 여부가 아니라 **배포**에서 발생한다. 게다가 기술적으로 불가에 가깝다(아래) | 기각 |
| 현 상태 유지 + 근거 문서화 | §1의 저볼륨을 근거로. 단 AGPL은 GPL보다 방어 논리가 약하다 | 기각 |

**채택 근거**: 이 리포는 이미 공개이고 소스 노출이 문제되지 않는다(사내 도구,
판매하지 않음). AGPL의 대가(파생물도 AGPL로 공개해야 함)는 **팔 물건일 때** 비용이지
사내 도구에는 실질 비용이 아니다. 나머지 구성요소는 §4대로 전부 허용적 라이선스라
AGPL 저작물에 포함하는 데 충돌이 없고, FFmpeg(GPL)는 별도 프로세스라 애초에 결합되지
않는다(§2).

**남는 의무 — 지키면 그것이 합법 사용이다**

1. `LICENSE` = AGPL-3.0 전문 (리포 루트, 2026-08-03 추가)
2. 대응 소스 제공 — 공개 리포가 그 자체로 충족. 릴리스 노트에 소스 위치 명시
3. **§13 네트워크 조항** — 서버 콘솔 사이드바에 `AGPL-3.0 · 소스` 링크
   (`apps/server_desktop/src/ServerConsole.tsx`)

**⚠되돌릴 수 없는 성질**: 이미 AGPL로 배포된 버전은 회수할 수 없다. 다음 버전의
라이선스는 저작권자(우리)가 바꿀 수 있지만, **PyMuPDF를 계속 쓰는 한 AGPL을 벗어날 수
없다** — 벗어나려면 백엔드 교체(§5.1.1) 또는 상용 라이선스 취득이 선행돼야 한다.

**⚠비공개 전환이 왜 기각인가(기술)**: ①양 앱 `tauri.conf.json`의 업데이터
엔드포인트가 **공개 URL로 설치본에 구워져** 있어 비공개 즉시 전 설치본의 자동
업데이트가 404 ②`ffmpeg.lock.json`의 Windows·Linux 핀이 **우리 릴리스 자산**을
가리키는데 페처(`fetch-ffmpeg.sh:90`, `build-server.ps1:83`)에 인증이 없어 빌드가
ffmpeg 벤더링에서 죽는다 ③공개 리포는 Actions 무료, 비공개는 유료(macOS ×10).

#### 5.1.1 백엔드 교체 타당성 조사 (2026-07-31 실측)

교체 후보 `pypdfium2`(BSD-3-Clause / Apache-2.0) + `pypdf`(BSD-3-Clause)로
`backend.py` Protocol 10개 메서드를 실제로 시험했다. **결론: 라이선스 문제는
풀리지만, 지금 품질을 유지하려면 추출 계층 재튜닝이 필요하다.**

**(1) 한글 주석 쓰기 — 가능하되 주석 종류가 바뀐다**

빈 A4에 같은 문구를 넣고 렌더해 잉크 픽셀을 센 결과:

| 경로 | 한글 렌더 | 주석 여부 | AP 생성 |
|---|---|---|---|
| **PyMuPDF FreeText (현행)** | **1962** ✅ | FreeText | ✅ |
| pypdf `FreeText` | **0** ❌ | — | — |
| pypdfium2 → 페이지 본문 | 1954 ✅ | ❌ 본문에 박힘(제거 불가) | — |
| pypdfium2 → **FreeText 주석** | ❌ `FPDFAnnot_AppendObject` 실패 | FreeText | **없음** |
| pypdfium2 → **Stamp 주석** | **1891** ✅ | Stamp | ✅ |

- `pypdf`의 `FreeText`는 표준 14폰트만 써서 **한글이 0픽셀**이다(영문은 렌더됨).
- `pypdfium2`는 `FPDFText_LoadFont(..., FPDF_FONT_TRUETYPE, cid=True)`로 한글
  TTF를 임베드하면 PyMuPDF와 동등하게 렌더한다.
- 단 PDFium의 `FPDFAnnot_AppendObject`는 **FreeText를 거부하고 Stamp만 받는다.**
  → 사람 납품본 관례(FreeText)와 주석 종류가 달라진다.
- Stamp는 텍스트가 그림 객체라 **복사·검색이 안 된다** —
  `FPDFAnnot_SetStringValue(annot, "Contents", …)`로 문자열을 함께 넣어 보완해야 한다.

**(2) 텍스트 추출 충실도 — 여기가 진짜 비용** (FL102_FNL_A 79페이지 전수)

| 비교 | 결과 |
|---|---|
| 페이지 텍스트 원본 일치 | **0 / 79** |
| `_` 주변 공백만 정규화 후 일치 | **1 / 79 (1%)** |
| 남는 불일치 | **78 / 79** |
| 도형 개수(`page_rects` 원재료) | **1006 대 1006 — 정확 일치** ✅ |

차이는 세 종류가 겹쳐 있다:

1. **`_` 주변 공백 삽입** — `FL102_FNL_A` → `FL102 _ FNL _ A`
2. **단어 내부 공백 난입**(커닝 해석 차이) —
   MuPDF `'Proper ty of Netflix'` vs PDFium `'Prope r t y o f Ne tfl ix'`
3. **읽기 순서 차이** — 페이지 푸터가 다른 위치로 옴

밑줄 공백을 정규화해도 **1%만 일치**한다 — 즉 이 차이는 단순 정규화로 흡수되지
않는다.

**(3) 왜 그것이 품질에 직결되는가**

스토리보드 프로파일의 튜닝이 전부 MuPDF 추출 문자열 **위에** 세워져 있다:

- **자산 코드 보존** — 프롬프트가 `TGNO_PizzaBox_CL_V01`을 "번역하지 말고 그대로
  복사"로 지시한다. PDFium이 `_`를 쪼개면 이 규칙이 무너진다.
- **Task 20 문자 깨짐 복구** — `block_index`/`offset`/`bad_indices` 기반
  **오프셋 치환**이다. 추출 문자열이 바뀌면 좌표계가 어긋나 엉뚱한 자리를 덮는다.
- 화자줄 파싱·씬 번호 게이트·발화 병합·dedupe 키가 모두 문자열 형태 의존.

**(4) 유지되는 것**

- `page_rects` 원재료(도형) — 개수 정확 일치. 좌표 일치는 미검증
- `render_png` — pypdfium2의 주특기
- `corrupt_words`의 신호원 — **`FPDFText_HasUnicodeMapError`가 존재**해
  MuPDF `get_texttrace()`의 U+FFFD 신호와 직접 대응한다
- `page_count`/`page_size`/`producer`/`save`/`close` — 자명

**(5) 교체 시 실제로 해야 할 일**

1. 블록 조립을 MuPDF 의미(스팬 병합·bbox union)에 맞게 재구현 — PDFium은
   문자·rect만 주고 블록 API가 없다
2. `_` 공백·단어 내 공백 정규화 계층 추가
3. 읽기 순서 정렬
4. 그 위에서 프로파일 상수 재검증(열 허용폭 60pt, 필드 박스 300pt, 발화 병합,
   화자줄 규칙)
5. GABE01 1037p + FL102 79p 전수 재비교로 회귀 0 확인

**판단**: §1의 배포 규모(108건)에서는 **Artifex 상용 라이선스 쪽이 비용 대비
합리적**이다. 교체는 "가능하다"가 확인됐을 뿐 싸지 않다 — 사실상 추출 계층을
다시 만드는 규모이고, 현재 품질(사람 주석 88건 중 86건 도달·결함 0)은 위 5단계를
모두 거쳐야 재현된다.

⚠ 측정 범위: 위 (2)는 **페이지 단위 텍스트** 비교다. 블록 조립을 잘 만들면 차이
일부는 흡수될 수 있다 — 다만 99%가 다른 상태에서 출발하므로 "재튜닝 없이 된다"는
결론은 나오지 않는다. 재현 스크립트는 세션 스크래치에 있었고 영구 보관하지
않았다(`pypdfium2`·`pypdf`·`pymupdf`를 한 venv에 넣고 같은 문서로 대조하면 재현된다).

### 5.2 H.264 특허 (현 시점 조치 없음, 근거 기록)

동봉 FFmpeg에는 H.264 인코더(x264)가 포함되고, 자막 굽기가 실제로 H.264로
인코딩한다(`libx264`, `h264_nvenc`, `h264_qsv`, `h264_amf`).

- 저작권(GPL)과 **다른 축**이다. 고지·표기로 해소되지 않는다.
- AVC 특허 풀(Via LA, 옛 MPEG LA)은 역사적으로 **저볼륨 무로열티 구간**을
  두어 왔다(연간 십만 단위 규모). §1의 실측(누적 108건)은 그 문턱에 크게
  못 미친다.
- AVC 핵심 특허는 2000년대 초 출원분이 상당수 만료됐다. 다만 "전부
  만료됐다"고 단정할 근거는 확인하지 않았다.
- 현 시점 판단: **조치하지 않는다.** 사내 도구·저볼륨이라는 전제에 근거한다.

**재검토 트리거** — 아래 중 하나라도 발생하면 이 판단을 다시 본다:

1. 외부 판매·유상 공급 시작
2. 배포량이 유의미해질 때(예: 누적 1,000건 초과)
3. 제품에 포함해 제3자에게 재배포
4. 인코딩 결과물을 상업 방송·유료 서비스로 송출

> 이 절의 목적은 "몰라서 안 한 것"과 "판단해서 안 한 것"을 구분해 두는 것이다.
> 정확한 조건은 Via LA에 확인해야 한다.

---

## 6. 갱신 방법

1. 동결 스크립트(`apps/server_desktop/scripts/build-server.{sh,ps1}`)의
   `--collect-all` 목록과 `binaries/` 디렉터리가 실제 동봉 목록이다.
2. 파이썬 의존성 라이선스는 설치된 배포판 메타데이터로 확인한다:
   `python -c "import importlib.metadata as m; md=m.metadata('<pkg>'); print(md.get('License-Expression') or md.get('License'))"`
3. FFmpeg 빌드 구성은 바이너리에서 직접 확인한다:
   `strings ffmpeg | grep -oE '\-\-enable-[a-z0-9]+'`
