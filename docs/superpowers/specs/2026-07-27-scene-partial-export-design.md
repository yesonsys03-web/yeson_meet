# 개별 씬 익스포트(부분 익스포트) — 설계

- 날짜: 2026-07-27
- 대상 기능: 자막메이커 씬 분할 목록에서 한 씬(+맞닿은 이웃)만 다시 익스포트
- 상태: 설계 승인 완료(2026-07-27) · 구현 완료
- 관련: [In/Out 트림 경계 교정](2026-07-27-scene-inout-trim-design.md)

## 1. 목적

경계 혼입을 나중에 하나 발견해 고치면, 지금은 **전체 익스포트**밖에 없다. 씬이 400개인 실기 번들에서 클립 하나를 갱신하려고 400개를 재인코딩한다(25분+). 목록 행에서 그 씬만 다시 굽게 한다.

## 2. 핵심 결정 (확정)

1. **행 버튼은 '이 씬 + 맞닿은 이웃'을 굽는다.** 경계는 두 씬이 공유하므로 경계를 옮기면 이웃의 프레임 수도 함께 바뀐다. 고른 씬만 내보내면 이웃 mp4가 옛 경계로 남아 폴더가 정합을 잃는다. 라벨만 고친 경우엔 클립 2개를 더 굽지만 클립당 수 초라 사실상 공짜다.
2. **파일명 dedupe는 항상 전체 목록 기준.** 서버는 `dedupe_labels([전체 라벨])[i]`로 파일명을 정한다. 선택분만으로 dedupe하면 중복 라벨의 접미사가 달라져(`0010_02` → `0010`) 전체 익스포트가 만든 파일을 갱신하지 못하고 유령 파일이 생긴다.
3. **저장 폴더는 지난 익스포트 폴더 재사용.** 서버 `export_status.json`에 남아 앱을 다시 켜도 복구된다("아까 그 폴더의 그 파일만 갱신"이 목적). 없을 때만 폴더 선택 다이얼로그.
4. **동시 실행 금지.** 서버는 새 익스포트를 시작할 때 `_bump_generation`으로 진행 중인 작업을 `StaleRunCancelled`로 취소한다 → 익스포트/스캔 중에는 행 버튼을 잠근다. 안 막으면 개별 익스포트가 진행 중인 전체 익스포트를 죽인다.
5. **범위 밖 인덱스는 거부.** 목록이 어긋난 채 엉뚱한 씬을 덮어쓰는 것이 최악의 결과라, 자르지 않고 409로 되돌린다.

## 3. 서버 API

`POST /api/v1/video-jobs/{id}/scenes/export`

```jsonc
{ "mode": "scene", "out_dir": "/path", "indices": [3, 4, 5] }  // indices 생략/null = 전체(기존)
```

- 검증: 빈 배열이거나 `i < 0 || i >= len(segments)`가 하나라도 있으면 409 "익스포트할 씬 번호가 목록과 맞지 않습니다 — 씬 목록을 다시 불러오세요."
- 통과 시 `sorted(set(indices))`로 정규화, 응답 `count` = 선택 개수(프론트 진행바 기준).
- `run_scene_export(external_id, mode, out_dir, indices)`: dedupe는 전체 목록으로 계산하고 `picked`만 순회. `total`/`done`은 선택 개수 기준.

## 4. 클라이언트

- `SceneFilmstrip` 행에 `⬇익스포트` 버튼(병합 오른쪽). props: `onExportOne`, `exportingIndex`, `exportDisabled`.
- `neighborIndices(i, n)` 순수함수 → `[i-1, i, i+1]` 양끝 클램프, 범위 밖은 `[]`.
- `exportOne(i)`: dirty 차단(전체 익스포트와 같은 문구) → 진행 중 차단 → `getExportStatus`의 `out_dir` 재사용(없으면 다이얼로그) → `exportScenes(..., indices)` → 폴링.
- 진행률 폴링은 `pollExport(doneMsg)`로 전체/개별이 공유(같은 `export_status`를 쓰므로).
- 완료 알림에 갱신한 라벨과 폴더를 적는다: `2/2개 클립 익스포트 완료 — 0010, 0020 (/path). 경계를 공유한 이웃 씬까지 갱신했습니다.`

## 5. 변경 파일

| 파일 | 변경 |
|---|---|
| `apps/server/api/v1/video_jobs.py` | `SceneExportIn.indices`, 범위 검증, `_start_scene_export` 시그니처 |
| `apps/server/domain/video_captions/pipeline.py` | `run_scene_export(..., indices)` — `picked` 순회, 전체 기준 dedupe 유지 |
| `apps/server/tests/test_api_video_jobs.py` | 부분 익스포트 인덱스 전달·정규화, 범위 밖 409, 기존 seam 시그니처 |
| `apps/server/tests/test_video_pipeline.py` | 부분 익스포트가 고른 파일만 만들고 전체 dedupe 이름을 쓰는지 |
| `apps/desktop/src/console/sceneSplitLogic.ts(.test.ts)` | `neighborIndices` + 테스트 |
| `apps/desktop/src/console/videoApi.ts` | `exportScenes(..., indices?)` |
| `apps/desktop/src/console/SceneSplitView.tsx` | `exportOne`, `pollExport` 공유, 버튼 배선 |
| `apps/desktop/src/console/SceneFilmstrip.tsx` | 행 `⬇익스포트` 버튼 + 안내 문구 |
| `apps/desktop/src/help/helpManualContent.ts` | 도움말 8번에 한 줄 |

## 6. 검증

- 단위(클라): `neighborIndices` — 중간/양끝/범위 밖.
- 단위(서버): 부분 익스포트 파일명이 전체 dedupe와 동일 + 고르지 않은 파일 미생성 + `total/done` = 선택 개수; API 인덱스 정규화·409.
- **주의: `apps/server` 테스트는 Postgres(127.0.0.1:5432)가 필요하다** — `tests/conftest.py`가 DSN을 하드코딩하고 수집 시점에 접속한다. 서버 코드 자체는 SQLite를 지원하므로(`db/session.py` + `aiosqlite`), Postgres 없는 환경에서는 워커(`run_scene_export`)와 엔드포인트 함수(`export_scenes`)를 SQLite 세션으로 직접 호출해 같은 코드 경로를 검증할 수 있다(2026-07-27 이 방식으로 실증). 테스트 하네스를 SQLite로 옮기는 것은 서버 테스트 전체에 영향이 가는 별도 작업.
- 실기: 씬 하나 경계 교정 → 저장 → `⬇익스포트` → 지난 폴더의 해당 3개 mp4 mtime·프레임 수 갱신 확인, 나머지 파일 불변 확인.

## 7. 배포 주의

서버 코드가 바뀌므로 데스크톱 번들에 반영하려면 **`build-server.sh` 재동결 + 서버앱 재시작**이 필요하다. 안 하면 옛 라우트가 `indices`를 무시하고 전체를 굽는다(Pydantic 모델에 없는 필드는 무시되므로 조용히 전체 익스포트가 된다).
