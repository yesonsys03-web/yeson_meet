# 씬 익스포트 — 같은 PC면 지정 폴더에 직접 굽기 (설계)

- 날짜: 2026-07-28
- 대상 기능: 자막메이커 씬 익스포트에서 서버·클라가 같은 폴더를 공유하면 중계(굽기→받기)를 건너뛰고 사용자가 고른 폴더에 바로 굽는다
- 상태: 설계 승인 완료(2026-07-28)
- 관련: [개별 씬 익스포트](2026-07-27-scene-partial-export-design.md) · [In/Out 트림 경계 교정](2026-07-27-scene-inout-trim-design.md)

## 1. 목적

v1.7.3에서 익스포트를 **서버가 자기 폴더에 굽고 → 클라가 HTTP로 받아 사용자 폴더에 쓰고 → 서버 사본을 지우는** 중계 방식으로 바꿨다. 서버·클라가 다른 PC일 때 사용자 폴더가 끝까지 비어 있던 실기 버그(윈도우)를 고치기 위해서였고, 그건 옳았다.

그런데 서버·클라가 **같은 PC**일 때도 이 중계를 그대로 치른다. 비용이 작지 않다:

1. **디스크 쓰기 2배** — 서버가 `scene_out/`에 굽고(1회), 클라가 받아 다시 쓴다(2회). 씬 400개 번들이면 수십 GB를 두 번 쓴다.
2. **피크 용량 2배** — 전부 받기 전에는 서버 사본을 지우지 않는다(중간 실패 시 재인코딩을 피하려는 의도된 안전장치). 익스포트 중 순간 점유가 결과물의 2배다.
3. **복사 패스가 통째로 추가** — 굽기가 **전부 끝난 뒤에** 순차 다운로드 루프가 시작된다(`pollExport` → `saveExportedFiles`).
4. **체감** — 굽는 내내 사용자 폴더는 비어 있다가 마지막에 우르르 찬다. 직접 모드는 클립이 하나씩 바로 보인다.

굽기 파이프라인의 직접 저장 경로(`run_scene_export(out_dir=...)`)는 지금도 살아 있다(`pipeline.py:1574`). 클라가 일부러 `undefined`를 넘길 뿐이다(`SceneSplitView.tsx:415,454`). 즉 되살리는 작업이지 새로 만드는 작업이 아니다.

## 2. 핵심 결정 (확정)

1. **"같은 PC"를 추측하지 않고 증명한다.** 잘못 판정하면 v1.7.3에서 고친 그 실패 — 서버 디스크에 폴더만 생기고 사용자 폴더는 비어 있는데 **에러도 안 나는** — 가 그대로 부활한다. 호스트명 비교나 사용자 체크박스는 "같은 컴퓨터냐"만 맞힐 뿐, 실제로 필요한 조건을 검사하지 못한다.

2. **진짜 조건은 "같은 PC냐"가 아니라 "서버가 이 경로에 쓰면 사용자가 보는 그 폴더에 바이트가 놓이냐"다.** 같은 PC여도 못 쓰는 경우가 있다(macOS TCC — 서버 앱은 클라와 다른 번들이라 문서/데스크톱 접근이 막힐 수 있다; 윈도우 제어된 폴더 액세스; 세션별 매핑 드라이브). 반대로 **다른 PC라도 공유 폴더(UNC/NAS)를 고르면 조건을 만족**하고, 그때는 전송 한 번을 통째로 아낀다.

3. **탐침(probe) 왕복으로 두 가지를 한 번에 검사한다.** 클라가 고른 폴더에 토큰 파일을 쓰고, 서버에게 "그 경로에서 이 토큰이 읽히나 + 너도 거기 쓸 수 있나"를 묻는다. 둘 다 참일 때만 직접 모드.

4. **조금이라도 어긋나면 지금 방식(중계).** 폴더 없음·토큰 불일치·쓰기 실패·타임아웃·라우트 404(구버전 서버)·예외 — 전부 `direct=false`로 수렴한다. **버전이 어긋나도 깨지지 않고 느려질 뿐이다.**

5. **직접 모드에서는 정리(cleanup)를 부르지 않는다.** 사용자 폴더의 파일이 결과물 자체다. 설령 실수로 불려도 정리 라우트는 **작업 폴더 안만** 지우므로 사용자 파일은 안전하다(`video_jobs.py:857-871`, 기존 가드).

6. **탐침은 매 익스포트마다 한 번씩 한다.** 개별 익스포트가 재사용하는 기억된 폴더도 상황이 변할 수 있다(네트워크 드라이브 끊김, 권한 변경). 수십 ms짜리라 아낄 이유가 없다.

## 3. 탐침 프로토콜

```
클라(Rust): <고른 폴더>/yeson_probe_<16hex>.tmp 에 토큰을 쓴다
클라 → 서버: POST .../scenes/export/probe { dir, token }
서버: ① 그 경로에서 파일을 읽어 토큰 일치 확인 (3회 × 0.3초 재시도)
      ② 같은 폴더에 yeson_probe_ack_<token>.tmp 를 써보고 읽어서 확인 → finally 삭제
      ③ 둘 다 성공이면 { direct: true }
클라(Rust): 자기 토큰 파일 삭제 (finally — 실패 경로에서도)
```

- **양쪽 다 자기가 만든 파일만 지운다** → 어느 경로로 끝나도 잔여물이 없다.
- 파일명에 토큰을 넣어 **이전 실행의 잔여 파일을 같은 폴더로 오인하지 않는다.** 내용도 토큰과 대조한다.
- **재시도 3회 × 0.3초** — 백신(Kaspersky/Defender)의 검사 지연과 SMB 음성 캐싱으로 방금 만든 파일이 잠깐 안 보일 수 있다.
- 서버는 항상 200으로 응답하고 `direct` 불리언과 `reason`(로그·진단용)을 준다. 실패를 예외로 만들지 않아 클라 분기가 단순해진다.

### 서버 API

`POST /api/v1/video-jobs/{external_id}/scenes/export/probe`

```jsonc
// 요청
{ "dir": "C:\\Users\\me\\clips", "token": "aaaabbbbccccdddd" }
// 응답 (항상 200; 잡이 없으면 404)
{ "direct": true,  "reason": "ok" }
{ "direct": false, "reason": "token_mismatch" }   // 다른 폴더 = 다른 PC
{ "direct": false, "reason": "not_a_dir" }        // 서버에 그 경로가 없음
{ "direct": false, "reason": "write_denied" }     // 같은 폴더지만 서버가 못 씀
```

- 잡 범위 라우트로 둔다 — 나머지 익스포트 라우트와 같은 네임스페이스이고 `_get_job_or_404`를 그대로 쓴다.
- `dir`은 서버 로컬 경로 문자열이다. 기존 `SceneExportIn.out_dir`과 신뢰 경계가 같다(LAN 신뢰 — `project_lan_trust_0000_bind` 참조). 새로 열리는 권한이 없다.

### Rust 커맨드

`plugin-fs`의 `writeFile`은 capabilities 스코프(`$HOME/**`)에 묶여 윈도우의 다른 드라이브(`D:\`)·네트워크 폴더에서 거부된다 — `download_to_file`이 Rust로 내려간 것과 **같은 이유**다(`video_download.rs:1-8`). 탐침 파일도 Rust에서 쓴다.

```rust
probe_file_write(path: String, token: String) -> Result<(), String>
probe_file_remove(path: String)               -> Result<(), String>
```

- **두 커맨드 모두 파일명이 `yeson_probe_`로 시작하지 않으면 거부한다.** 범용 "아무 파일 쓰기/지우기" 표면을 만들지 않기 위한 가드이고, `probe_file_remove`가 사용자 파일을 지울 가능성을 원천 차단한다.
- 파일명 접두사는 클라(TS)·Rust 양쪽이 공유하는 계약이므로 `sceneSplitLogic.ts`의 `probeFileName(token)`으로 뽑아 테스트로 잠근다.

## 4. 클라이언트 흐름

`doExport`(전체)·`exportOne`(개별) 둘 다 폴더를 고른 직후 탐침한다.

```ts
const direct = saveDir ? await probeDirect(saveDir) : false;
const res = await exportScenes(jobId, mode, direct ? saveDir : undefined, indices);
const st = await pollExport(direct ? 완료문구 : "구웠습니다. 저장 중…");
if (!st) return;
if (direct)        setNotice(`${st.files?.length ?? 0}개 클립 저장 완료 (${saveDir})`);
else if (saveDir)  await saveExportedFiles(st.files ?? [], saveDir);   // 기존 중계 경로
else               setNotice(`... (서버 폴더 ${st.out_dir})`);          // 브라우저 모드
```

- `probeDirect`는 어떤 예외도 밖으로 내보내지 않는다 — `catch { return false }`. 구버전 서버의 404도 여기서 흡수된다.
- 직접 모드에는 "저장 중 n/N" 단계가 없다. 굽기 진행바가 그대로 최종 진행바다.
- 직접 모드에서 `export_status.out_dir`은 사용자가 고른 폴더가 되므로 진행 표시(`SceneSplitView.tsx:1475`)가 더 정확해진다.
- 브라우저(비-Tauri) 모드는 폴더 선택 자체가 없다 → 탐침 없음, 서버 폴더에 남는 기존 동작 그대로.

## 5. 윈도우 점검 (사전 확인 완료)

**이미 해결되어 재발하지 않는 것**

| 항목 | 근거 |
|---|---|
| `D:\`·네트워크 폴더 쓰기 거부 | `download_to_file`이 Rust로 내려가며 해결됨(`video_download.rs:1-8`). 탐침도 같은 방식. |
| 백슬래시 경로 전달 | JSON 문자열·파이썬 `Path()` 모두 그대로 처리. 서버가 맥/리눅스면 `Path("C:\\...")`가 안 맞아 탐침 실패 → 폴백. |
| 사용자 폴더 삭제 위험 | 정리 라우트가 작업 폴더 밖은 건드리지 않음(`video_jobs.py:857-871`). 직접 모드는 정리를 아예 호출하지 않음. |

**탐침이 잡아내는 윈도우 고유 문제**

- **매핑 드라이브는 세션마다 다르다.** 서버가 다른 계정/세션에서 돌면 같은 `Z:\`가 딴 곳을 가리킨다 → 토큰 불일치로 폴백.
- **Defender 제어된 폴더 액세스**가 서버 앱의 문서/바탕화면 쓰기를 막으면 → `write_denied`로 폴백.

**남는 위험과 대응**

1. **경로 길이 260자 제한** — `longPathAware` 매니페스트 설정이 없다(확인함). 아주 깊은 폴더에서는 ffmpeg(별도 exe)가 못 쓸 수 있다. 대응 둘:
   - 탐침 파일명(`yeson_probe_<16hex>.tmp` = 32자)이 실제 클립명(`0240ACV01N.mp4` ≈ 14자)보다 길어 **한계 근처를 미리 걸러내는 카나리아**로 겸한다.
   - 그래도 뚫리면 굽기 자체가 클립마다 파일 존재·크기를 검사해 에러로 표면화한다(`pipeline.py:1613-1616`) — 조용한 실패가 아니다.
   - **의도된 절충**: 경로가 아슬아슬하면 탐침만 실패하고 중계 모드로 떨어질 수 있다(거짓 음성). 그래도 결과는 정상 저장이다. 안전한 쪽으로 틀린다.
2. **백신 지연/격리** — 탐침 읽기 3회 재시도로 흡수. 굽기 검증은 위 1번과 동일.
3. **플레이어가 mp4를 열어두면 덮어쓰기 실패** — 두 모드 모두 동일하고 안내 문구가 이미 있다(`SceneSplitView.tsx:375-379`). 직접 모드에서는 ffmpeg 실패가 `export_status.error`로 올라와 같은 문구가 뜬다.

## 6. 변경 파일

| 파일 | 변경 |
|---|---|
| `apps/server/api/v1/video_jobs.py` | `SceneExportProbeIn` + `POST .../scenes/export/probe` |
| `apps/desktop/src-tauri/src/video_download.rs` | `probe_file_write` / `probe_file_remove` (접두사 가드 + 단위 테스트) |
| `apps/desktop/src-tauri/src/lib.rs` | 커맨드 2개 등록 |
| `apps/desktop/src/console/videoApi.ts` | `probeExportDir(jobId, dir, token)` |
| `apps/desktop/src/console/sceneSplitLogic.ts(.test.ts)` | `probeFileName(token)` + 접두사 계약 테스트 |
| `apps/desktop/src/console/SceneSplitView.tsx` | `probeDirect`, `doExport`·`exportOne` 분기, 완료 문구 |
| `apps/server/tests/test_api_video_jobs.py` | 탐침 라우트 케이스 |
| `apps/desktop/src/help/helpManualContent.ts` | 도움말 익스포트 항목에 한 줄 |

**굽기 파이프라인(`pipeline.py`)은 변경 없다** — `out_dir` 경로가 이미 완성되어 있다.

## 7. 검증

**단위(서버)** — `tmp_path`로 네 갈래:
- 토큰 파일이 있고 폴더가 쓰기 가능 → `direct: true`, **ack 파일이 남지 않음**
- 토큰 파일 없음 → `false / token_mismatch`
- 토큰 내용 불일치(다른 실행의 잔여 파일) → `false / token_mismatch`
- 폴더 없음 → `false / not_a_dir`

> **테스트 DB**: `apps/server` 테스트의 기본 DB는 로컬 Postgres지만, `conftest.py`가 `TEST_DATABASE_URL`을 지원한다(`conftest.py:6-13`). Docker 없이 돌릴 때는 `TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db"`를 앞에 붙인다 — 탐침 라우트는 잡 조회 외에 DB를 쓰지 않아 SQLite로 충분하다.

**단위(Rust)** — tempdir로: 접두사가 맞으면 쓰기·삭제 성공, `yeson_probe_`로 시작하지 않는 이름은 두 커맨드 모두 거부(`video_upload.rs`에 테스트 선례 있음).

**단위(클라)** — `probeFileName`이 Rust 가드와 같은 접두사를 쓰는지(양 언어 계약 잠금).

**실기 3종** — 각각 사용자 폴더에 클립이 생기는지 + 서버 `scene_out/`에 잔여물이 없는지:
1. 맥 같은 PC → 직접 모드로 붙는지(굽는 동안 폴더에 하나씩 쌓이는지)
2. **윈도우 같은 PC** → 직접 모드, 특히 `D:\` 등 다른 드라이브
3. **윈도우 다른 PC** → 중계 모드로 폴백하고 v1.7.3 동작 그대로인지

**⚠️ dev 실행 시**: 서버에 라우트를 추가했으므로 `build-server.sh`로 재동결하지 않으면 탐침이 404다. 다만 404는 폴백으로 흡수되므로 "안 깨지고 중계 모드로 동작"한다 — 직접 모드가 안 붙으면 재동결부터 확인할 것.
