# 서버 콘솔 보고서 관리 · 리뷰 설계

- 날짜: 2026-07-07
- 대상 앱: `apps/server_desktop` (서버 콘솔) + `apps/server` (FastAPI 백엔드)
- 상태: 승인됨(설계). 다음 단계 = 구현 계획(writing-plans)

## 1. 배경 · 문제

회의 보고서(보고서 본문 + LLM 한글 요약)는 서버 파일시스템
(`{STORAGE_ROOT}/{세션ID}/report.*`)에 저장되지만, 이를 다루는 UI는 **클라이언트 앱**에만
존재한다. 그마저도 통합 목록이 아니라 회의 종료 직후 그 세션 하나만 다루는 패널
(`apps/desktop/src/console/SessionResultPanel.tsx`)이다.

운영자는 **모든 회의의 보고서를 한 곳에서** 조회·리뷰·익스포트·삭제할 수 없다. 이는
"서버 콘솔 = control plane" 원칙(디바이스 키 발급을 서버 콘솔로 이관한 결정과 동일한
방향)에 어긋난다. 보고서는 이미 서버 자산이므로 서버 콘솔이 관리 주체가 되는 것이 맞다.

## 2. 목표

서버 콘솔 사이드바에 **"보고서 관리"** 탭을 추가하여 운영자가 다음을 할 수 있게 한다.

1. **목록**: 전체 세션의 보고서를 한 목록에서 조회(제목·날짜·상태·파일 크기, 검색/정렬)
2. **리뷰**: 콘솔 안에서 보고서 본문과 LLM 요약을 바로 읽기(다운로드 없이). 두 탭 전환.
3. **익스포트**: MD·HTML·DOCX·PDF로 서버 콘솔에서 직접 파일 저장
4. **삭제**: (a) 보고서 파일만 삭제 (b) 세션 전체 삭제

### 비목표 (YAGNI)

- 보고서 내용 편집/재작성 기능 없음
- 보고서 재생성 트리거 버튼 없음(기존 GET 라우트가 disk-miss 시 자동 재생성)
- 클라이언트 앱의 기존 세션별 패널은 유지(제거하지 않음)

## 3. 접근 방식 결정

**채택: 새 무인증 loopback `/reports` 라우터.**

서버 콘솔 패널들은 이미 `http://127.0.0.1:{port}` 무인증 loopback REST
(video-jobs / backup / device-admin)로 통일돼 있다. 기존 보고서 엔드포인트
(`sessions.py`)는 `require_operator` 인증이 걸려 있어 콘솔에서 직접 호출하면 401이 난다.
콘솔에 operator 토큰을 심는 대안(B안)은 무인증 컨벤션을 깨고 로그인 UI를 요구하므로 기각.

`video_jobs.py` / `VideoJobsPanel.tsx` / `videoJobsAdmin.ts` 3종 세트가 참고 템플릿이다.

## 4. 백엔드 설계 — `apps/server/api/v1/reports.py` (신규)

- 라우터 prefix `/reports`, `main.py`의 `/api/v1`에 마운트.
- 인증 없음(`Depends(get_session)`만). 로직은 기존 `domain/report_*.py` 빌더를 재사용하여
  중복 구현하지 않는다.
- **라우트 순서 주의**: 정적 경로(`/storage`)를 동적 `/{external_id}` 앞에 선언
  (video_jobs.py 교훈).

| Method | Path | 동작 |
|---|---|---|
| GET | `/reports` | 전체 세션 목록. 항목마다 `report_ready`, `summary_ready`, (옵션 `?with_sizes`) 파일 크기. `Session` 조회 + `{STORAGE_ROOT}/{id}/` 스캔. |
| GET | `/reports/storage` | 보고서 총 용량·세션 수 (video `/storage`와 동형). |
| GET | `/reports/{id}/view` | **리뷰용 보고서 HTML**. 기존 `build_session_report_html` 재사용(클라이언트와 동일 결과). |
| GET | `/reports/{id}/summary/view` | **리뷰용 요약 HTML**. 기존 요약 HTML 빌더 재사용. |
| GET | `/reports/{id}/download?fmt=` | 보고서 익스포트 바이트 (md·html·docx·pdf). 기존 바이트 빌더 재사용. |
| GET | `/reports/{id}/summary/download?fmt=` | 요약 익스포트 바이트. |
| DELETE | `/reports/{id}/files` | **보고서 파일만 삭제**. `{STORAGE_ROOT}/{id}/report.*` + `report.summary.*` unlink. DB·자막 원본 보존. 재생성 가능. |
| DELETE | `/reports/{id}/session` | **세션 전체 삭제**. DB `Session`+`Utterance` 행 삭제 + **FTS 인덱스 정리** + 스토리지 디렉토리 `shutil.rmtree`. 복구 불가. |

### 삭제 동작 상세

- **파일만 삭제**: 되돌리기 쉬움(GET 시 자동 재생성). 낮은 위험.
- **세션 전체 삭제**: `video_jobs.py`의 DELETE 흐름을 참고. 순서 = FTS 인덱스에서 세션 제거
  → DB 행 삭제 → 디렉토리 삭제. 지식저장고 검색에서도 사라져 일관성 유지.

## 5. 프론트엔드 설계 — 3종 세트

### `apps/server_desktop/src/reportsAdmin.ts` (신규)
loopback API 클라이언트. `videoJobsAdmin.ts` 복제:
`http://127.0.0.1:{port}/api/v1/reports...`. `listReports(port)`, `getStorage(port)`,
`fetchReportHtml/fetchSummaryHtml`, `fetchReportBytes(fmt)/fetchSummaryBytes(fmt)`,
`deleteReportFiles(port,id)`, `deleteSession(port,id)`.

### `apps/server_desktop/src/ReportsPanel.tsx` (신규)
`{ serverPort, running }` props. `VideoJobsPanel.tsx` 구조 차용:

- **목록 테이블**: 제목·날짜·상태(준비/미준비)·크기. `refresh()`가 목록+storage를
  `Promise.all`로 로드. 검색/정렬.
- **리뷰 뷰어**: 행 선택 시 열림. 상단에 **[보고서] / [요약] 탭 전환**. 선택된 탭의 HTML을
  가져와 렌더(예: sandboxed iframe 또는 안전한 컨테이너에 삽입).
- **익스포트**: 뷰어 내 MD·HTML·DOCX·PDF 버튼 → 바이트 fetch → Tauri 저장 다이얼로그로
  파일 저장(`reportExport.ts` 패턴 참고).
- **삭제**: 행별 **인라인 확인**(`window.confirm` 금지 — WebView2 이슈, VideoJobsPanel 헤더
  주석 참고). "보고서 파일만"은 1단계 인라인 확인. "세션 전체 삭제"는 별도 위험 버튼 +
  2단계 확인(자막 원본까지 소멸함을 명시).

### `apps/server_desktop/src/ServerConsole.tsx` (수정)
video 탭과 동일 패턴:
1. `View` union에 `"reports"` 추가 (line ~123)
2. `navItems`에 `{ view: "reports", label: "보고서 관리" }` push (line ~379)
3. `<section hidden={activeView !== "reports"}>`에 `<ReportsPanel serverPort={status?.port ?? null} running={running} />` 추가
4. 상단 import 추가

## 6. 데이터 흐름

```
ReportsPanel (콘솔, Tauri WebView)
  → http://127.0.0.1:{port}/api/v1/reports* (무인증 loopback)
    → reports.py 라우터
      → 목록: DB Session 조회 + STORAGE_ROOT 디렉토리 스캔
      → 리뷰/익스포트: domain/report_*.py 빌더 재사용
      → 삭제: 파일 unlink 또는 (DB행+FTS+디렉토리) 제거
```

## 7. 에러 처리

- 세션 존재하나 보고서 파일 없음 → 리뷰/다운로드는 기존 disk-miss 자동 재생성 경로 사용,
  또는 명확한 "보고서 없음" 상태 표시.
- 삭제 대상 없음 → 204/404 적절히. 프론트는 실패 시 인라인 에러 문구.
- LibreOffice 미설치로 PDF 실패 → 기존 빌더의 에러를 그대로 전달, 프론트에서 안내.

## 8. 테스트

- 백엔드: `apps/server/tests`에 `test_reports_admin.py` 신규. 목록/뷰/다운로드/파일삭제/세션삭제
  각 라우트. 세션 전체 삭제 후 FTS 인덱스·디렉토리 소멸 검증.
- 프론트: `reportsAdmin.test.ts`(API 클라이언트 URL/파라미터), 삭제 2단계 확인 로직.
- 회귀: 기존 operator 인증 보고서 엔드포인트(`sessions.py`)는 변경 없음 → 기존 테스트 유지.

## 9. 배포 · 함정

- **frozen-bundle 재동결 필수**: `apps/server`에 라우터 추가 → `build-server.sh` 재동결 +
  서버앱 재시작해야 반영(안 하면 405). tauri:dev도 번들 바이너리 사용.
- 라우트 정적/동적 순서 주의(4절).
- 세션 전체 삭제는 파괴적 → 2단계 확인 UI 필수.
