# PDF 납품문서 번역 기능 — 타당성 분석 (2026-07-29)

> 출처: EASA01_Shipping_Documents 실물 납품 PDF 포맷 분석 세션.
> 결론: **네 포맷 모두 자동화 가능.** 공통 패턴은 "원문 유지 + 한국어 오버레이".
> 구현 전 참조용 문서이며, 아직 코드는 없다.
> 샘플 경로: `/Users/usabatch/coding/EASA01_Shipping_Documents`
> 2026-07-29 검토 세션에서 코드베이스 실측 대조·아키텍처 확정·리드시트 포맷 추가
> ("확정 결정" 섹션 참조).

## 목표

영문 납품 문서(대본/스토리보드/컬러노트 PDF)를 받아, 기존 수작업 번역본과 동일한 형태
(원본 레이아웃 그대로 + 한국어 병기)의 PDF를 자동 생성한다.

## 핵심 발견: 기존 수작업 번역본의 실제 구조

수작업 번역본(`*_번역.pdf`)은 재조판 문서가 아니다. **원본 PDF 본문은 바이트 수준에서
그대로 두고, 한국어를 FreeText 주석(annotation)으로 오버레이**한 것이다.
따라서 자동화도 같은 방식이면 산출물이 기존 납품 형태와 일치한다.

## 분석한 4가지 포맷 프로파일

### 1. Final Draft 대본형 (Bob's Burgers)

- 샘플: `EASA01_Shipping_Documents/EASA01-Boogie_Days-ANIMATIC-03-28-24-clean.pdf` (원본)
  / 같은 이름 `_번역.pdf` (수작업 번역본)
- 66페이지, 세로 612×792pt, CourierFinalDraft 폰트
- 번역본: 64개 페이지에 **FreeText 주석 737개**, AdobeMyungjoStd-Medium 9pt
- 배치 규칙: 씬 헤딩·지문은 원문 근처 왼쪽(x≈110), 대사는 오른쪽 여백(x≈420~610)
- 캐릭터명도 번역해 대사 앞에 붙임: `GENE (O.S.)` → `진(화면밖): ...`
- 추출 난점: 블록 타입(씬헤딩/대사/지문/노트) 분류 필요.
  Final Draft는 들여쓰기가 고정이라 x좌표 규칙 기반 분류 가능
- 제외 대상: `(MORE)`, `(CONT'D)`, 페이지 헤더, "Yeson Entertainment" 워터마크

### 2. Storyboard Pro 스토리보드형 (King of the Hill)

- 샘플: `EASA01_Shipping_Documents/1601_콘티번역/GABE01_A1_FinalShipped.pdf`
  / `_번역.pdf` (A1~A3 쌍 존재)
- 1037페이지/181MB (A1 기준), 가로 1008×612pt, 페이지당 판넬 이미지 1장
- 번역 대상은 **Dialog, Action Notes 두 필드만**. 페이지당 주석 1~2개,
  AdobeMyungjoStd-Medium 12pt
- 배치 규칙: Dialog 번역은 대사 바로 아래(y≈132), Action Notes 번역은 하단(y≈26~56).
  템플릿 export라 위치가 전 페이지 일정
- 세 포맷 중 가장 쉬움: "Dialog"/"Action Notes" 라벨이 고정 위치에 있어 라벨 기준 추출 가능
- 주의: 단어 사이가 탭으로 추출됨(`If\tyou\twanna`) → 공백 정규화 전처리 필요
- 주의: 원본 PDF에 경미한 구조 오류(wrong pointing object) 있음 → pypdf는 경고,
  **PyMuPDF 기준으로 구현할 것**

### 3. 스프레드시트 표형 — Color Notes

- 샘플: `EASA01_Shipping_Documents/EASA04_ColorNotes_V04.pdf` (원본, 4p)
  / `EASA01ColorNotes.pdf` (부분 번역 예시, 2p)
- 가로 792×612pt. 컬럼: SQ / Scene Panels / Time of Day / CH & PR Elements /
  CH & PR Color Indication / FX Notes / Tone Notes
- 기존 예시는 주석이 아니라 **본문 텍스트로 직접 삽입**(Acrobat 편집 추정, AdobeMyungjoStd,
  Identity-H CID 인코딩). FX Notes 셀 옆 빈 공간에 한국어 병기, 원문 유지
- 추출 난점: 단순 텍스트 추출 시 셀이 붙어 나옴 → 헤더 행 x좌표로 컬럼 경계를 잡고
  단어 bbox를 컬럼에 매핑하는 **컬럼 인식 추출** 필요
- 미결정 사항 (구현 시작 시 결정):
  - 한국어 배치: 셀 옆 오버레이 vs FreeText 주석 vs 번역 표 페이지를 뒤에 추가
  - 번역 컬럼 범위: FX/Tone Notes 등 서술 셀만? "day"/"night" 같은 값도?

### 4. 웹 익스포트 리드시트형 — BW Lead Sheet (The Great North)

- 샘플: `EASA01_Shipping_Documents/5LBW03_SQ07_BW_LeadSheet_20240725_REVISED.pdf`
  — **이 파일 자체가 부분 번역 예시본**(깨끗한 원본 없음, 파란색 한국어가 이미 삽입됨)
- 1페이지, 가로 792×612pt. Bento Box 웹툴(kajabbox.herokuapp.com) 크롬 인쇄 익스포트
  (producer=Skia/PDF m117)
- 행 단위 구조: Call #(샷)당 썸네일 + Call # / Footage / BGs / Chars / Props /
  Time of Day / **Description** 컬럼. 컬럼 라벨이 행마다 반복되고 x좌표 고정
- **번역 대상은 Description 컬럼만**(x≈689~ 우측). BGs/Chars/Props의 애셋 ID는 번역 제외
- 기존 예시: 컬러노트와 동일하게 **본문 텍스트 직접 삽입**(AdobeMyungjoStd 6.3pt,
  파란색, Description 셀 아래 빈 공간에 좌측으로 확장 배치)
- 추출 난점: Skia 웹 익스포트라 **Type3 글리프 폰트 + 스팬이 글자 단위로 파편화**
  (`E|p|i|so|de`) → 스팬 병합 전처리 필수. 병합하면 텍스트는 온전히 추출됨(실측)
- 포맷 감지: producer=Skia/PDF + 헤더 "Lead Sheet" 텍스트 + 792×612 가로
- 주의: 부분 번역본 재입력 대비, **한글 포함 블록은 번역 대상에서 제외**하는 공통
  규칙 필요(다른 포맷의 `_번역` 재입력 사고 방지에도 유효)

## 공통 파이프라인 (3단계)

1. **추출**: PyMuPDF(fitz)로 텍스트 블록 + bbox 추출, 포맷 프로파일별 규칙으로
   블록 타입 분류·필터
2. **번역**: 블록 단위 LLM 배치 번역. 재사용 대상은 자막메이커 번역 엔진 스택 —
   `create_translator()`(`domain/video_captions/translate_cli.py`)와
   `TranslationProvider.translate_batch(list[str]) -> list[str]` 프로토콜
   (`translate.py:30`)은 SRT 비결합이라 전 엔진(gemini/claude/codex/qwen/apple)을
   그대로 쓸 수 있다. 단 기존 프롬프트·리질리언트 헬퍼는 자막 특화("화면에서 읽기
   좋게 짧게")이므로 **PDF 전용 프롬프트 빌더 + 개수불일치 복구 헬퍼는 신설**.
   캐릭터명 매핑(BOB→밥, HANK→행크 등)·용어집을 프롬프트에 포함
3. **오버레이**: 원본 복사본에 `page.add_freetext_annot()`으로 한국어 주석 삽입.
   좌표는 원문 bbox에서 포맷별 규칙으로 계산

### 아키텍처 (확정, 2026-07-29)

**"포맷별 추출 프로파일 + 공통 번역/주석 엔진 + 교체 가능 PDF 백엔드"** 구조.
포맷 자동 감지(페이지 크기·폰트·producer·라벨 텍스트로 판별) 후 해당 프로파일 적용.

```
apps/server/domain/pdf_translate/
  backend.py        # PDF 백엔드 인터페이스(열기/블록+bbox 추출/주석 삽입/페이지 래스터)
  backend_mupdf.py  # PyMuPDF 구현 — AGPL 이슈 시 이 파일만 교체(pypdfium2+pypdf 조합)
  profiles/         # 포맷 프로파일 플러그인 — 새 포맷 = 파일 하나 추가
    base.py         #   프로파일 계약: detect() / extract_blocks() / place_overlay()
    final_draft.py  #   1. Final Draft 대본형
    storyboard.py   #   2. Storyboard Pro형
    color_notes.py  #   3. 컬러노트 표형
    lead_sheet.py   #   4. 웹 익스포트 리드시트형
  translate_blocks.py  # PDF 전용 프롬프트 빌더 + 리질리언트 배치(엔진은 create_translator 재사용)
  overlay.py        # 공통 오버레이(백엔드 경유), 한국어 폰트 임베드
  job_run.py        # 잡 러너 — video_captions job_tasks 패턴 미러링
api/v1/pdf_jobs.py  # 업로드/상태/프리뷰(페이지 PNG)/다운로드 라우트
```

- 번역·용어집·잡 상태 관리는 프로파일과 무관한 공통부. 프로파일은 "어느 블록을
  번역하고 어디에 놓는가"만 담당 → 신규 포맷 추가 시 공통부 무수정
- 프리뷰: 백엔드의 페이지 래스터(get_pixmap→PNG)를 `GET /pdf-jobs/{id}/page/{n}`으로
  서빙. 원본·번역본(주석 포함) 동일 경로, 씬 썸네일 라우트 선례. 1000p급은 lazy 로드
- UI: 최상위 네비 신규 탭(A안) — `ConsoleView` 유니언 + `ConsoleNav.tsx` `navItems`
  + `DesktopConsole.tsx` 섹션 추가, 신규 `PdfTranslatePanel` 컴포넌트로 완전 격리

## 기술 스택 메모

- 신규 의존성: **PyMuPDF** (현재 yeson_meet에 PDF 라이브러리 없음). pypdf는 대상 파일의
  CID 폰트 텍스트 추출 실패·구조 오류 경고 이력이 있어 부적합
- 폰트: 기존 번역본은 AdobeMyungjoStd(Acrobat 내장) 사용 — 뷰어에 따라 대체될 수 있어
  임베드 가능한 한국어 폰트 지정 권장
  - **구현은 이 권장을 따르지 않았다**(2026-07-30 정정): `backend_mupdf.py`는 의도적으로
    폰트를 지정하지 않고 MuPDF의 CJK 폴백에 맡긴다. ROADMAP이 남긴 수동 확인 항목
    ("외부 뷰어 한글 렌더 확인 — 어피어런스 폰트 이식성")은 **이 선택이 실제 뷰어에서
    통하는지**를 보는 것이지, 위 권장의 이행 여부를 보는 것이 아니다
- 통합 위치: Python 스택(server / client_sidecar / sdk-python)에 배치 작업 형태
  (PDF 업로드 → 번역 PDF 다운로드)
- 비용: 대본 66p ≈ 737블록, 스토리보드 1037p ≈ 페이지당 0~2블록, 컬러노트 4p —
  LLM 비용 미미. 스토리보드는 페이지 단위 병렬 처리 용이

## 리스크 / 튜닝 포인트

- 주석 배치 계산이 실질 난이도의 대부분 (긴 대사의 박스 높이, 빽빽한 페이지의 겹침 회피)
- 표 포맷의 셀 공간 제약 (배치 방식 결정 필요 — 위 미결정 사항)
- 번역 품질·톤 일관성: 기존 수작업 번역본을 few-shot 예시로 활용 가능
  (원문-번역 쌍을 주석 rect 매칭으로 대량 추출 가능 → 용어집/스타일 학습 데이터로 사용)

## 확정 결정 (2026-07-29 검토 세션)

1. **PDF 라이브러리 = PyMuPDF 단일 스택**, 단 추출·주석·래스터는 `backend.py`
   인터페이스 뒤로 격리. PyMuPDF는 **AGPL**(사내 LAN 전용이라 실질 리스크 낮음) —
   외부 배포가 생기면 `backend_mupdf.py`만 pypdfium2(Apache/BSD)+pypdf(BSD) 조합으로
   교체. 성능상 PyMuPDF 상회 대안은 없음(pypdfium2가 유일한 대등 후보)
2. **UI = A안**: 최상위 네비에 "스토리보드 번역" 탭 추가(자막메이커 내부 서브탭 아님 —
   989줄 VideoCaptionPanel 개조 회피)
3. **대상 포맷 4종**(리드시트형 포함). 프로파일 플러그인 구조로 신규 포맷 확장
4. **PDF 프리뷰 화면 포함**(요구사항): 서버 래스터 방식(위 아키텍처 참조)
5. 미결정 잔여: 컬러노트·리드시트의 한국어 배치 방식(예시본은 본문 삽입이지만
   FreeText 주석으로 통일할지), 컬러노트 번역 컬럼 범위
6. **슬라이스 1(스토리보드형) 구현 완료 — `feat/pdf-translate-slice1`**(2026-07-30,
   Task 1~11). 동결 번들(`--collect-all pymupdf --hidden-import fitz`) + PDF
   셀프테스트(`YESON_PDF_SELFTEST`) 포함. 범위 밖(후속): 대본형(Final
   Draft)·컬러노트·리드시트 프로파일, Windows 실기 검증

## 동결 번들 반영 체크리스트 (구현 시 필수)

- [x] `apps/server/pyproject.toml`에 pymupdf 추가 + **`uv lock` 재생성**(Task 1)
- [x] `build-server.sh`·`build-server.ps1` **두 곳 모두** `--collect-all pymupdf`
  (+ `--hidden-import fitz`, Windows add-data 구분자 `;`는 기존 구조 유지 — Task 11)
- [x] cv2 전례의 uv 캐시 미실체화 함정 대비: 빌드 venv에서 `import pymupdf, fitz`
  실패 시에만 `--reinstall --no-cache` 후 재검증(정상 케이스 빌드 시간 미증가 — Task 11)
- [x] 번들 스모크 셀프테스트(`YESON_REPORT_SELFTEST` 선례): `YESON_PDF_SELFTEST`로
  fitz 임포트 + 1페이지 한글 주석 삽입/래스터 왕복 검증(Task 11)
- [ ] ~~임베드용 한국어 폰트 `--add-data` 스테이징~~ — **불필요로 판명**: PyMuPDF
  `add_freetext_annot`은 fontname 미지정 시에도 어피어런스 생성기가 CJK 폴백
  폰트를 써서 한글을 정상 렌더(스파이크 실증 + Task 11 셀프테스트로 재확인).
  단, macOS 미리보기/Acrobat 등 MuPDF 외 뷰어에서의 어피어런스 폰트 이식성은
  아직 실물 수동 검증 전(11-4, 사용자 확인 예정)
- [x] dev에서 새 라우트 추가 후 재동결 안 하면 404(기존 함정) — Task 11에서 재동결로
  `/pdf-jobs` 라우트 반영 완료, 동결 없이 데스크톱 앱에서 쓰면 404였을 것

## Windows 체크리스트

- PyMuPDF는 win_amd64 휠 제공(컴파일 불요) — uv.lock은 멀티플랫폼 해석이라 lock
  재생성만 하면 Windows CI(`uv sync --frozen`)가 자동 수급
- 폰트: 위 "동결 번들 반영 체크리스트"에서 확인했듯 **번들 폰트 임베드는 불필요로
  판명** — `add_freetext_annot`은 fontname 미지정 시 어피어런스 생성기가 CJK
  폴백 폰트를 자동으로 써서 한글을 렌더한다(AdobeMyungjoStd 같은 OS/Acrobat
  내장 폰트에 의존하지 않음). MuPDF 자체 렌더(서버 프리뷰 PNG·셀프테스트)는
  Mac에서 검증 완료. **남은 검증은 Windows에서의 동일 확인 + macOS 미리보기/
  Acrobat 등 MuPDF 외 뷰어에서의 어피어런스 폰트 이식성**(실물 수동 검증
  전, 위 체크리스트 11-4와 동일 항목) — 아래 실기 검증 항목에 포함
- 한글 파일명(`*_번역.pdf`) 자체는 NTFS 유니코드라 안전. cp949 함정은 subprocess
  한정인데 이 파이프라인은 전부 in-process(PyMuPDF)라 해당 없음 — **단, 향후
  외부 도구 호출을 추가하면 `encoding="utf-8"` 필수**(기존 교훈)
- 프리뷰는 서버 래스터 PNG라 WebView2 특이사항 없음(pdf.js 미사용 이유 중 하나)
- PDF 선택 다이얼로그: 폴더 아닌 **파일 선택**이므로 PR#73의 폴더 피커 이슈와 무관.
  `open({ filters: [{ extensions: ["pdf"] }] })` 파일 모드 사용
- 실기 검증 항목(릴리스 전): Win에서 업로드→번역→프리뷰→다운로드 왕복 1회,
  173MB급 스토리보드 포함 + 다운로드한 번역본을 Windows 기본 PDF 뷰어(또는
  Acrobat)로 열어 CJK 폴백 폰트 어피어런스가 정상 렌더되는지 확인(위 폰트
  항목의 남은 검증)
