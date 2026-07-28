# 씬 익스포트 — 같은 PC면 지정 폴더에 직접 굽기 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 서버·클라가 같은 폴더를 공유한다는 사실이 **증명될 때만** 익스포트 중계(굽기→받기→서버 사본 삭제)를 건너뛰고 사용자가 고른 폴더에 바로 굽는다.

**Architecture:** 폴더를 고른 직후 탐침 왕복 1회 — 클라(Rust)가 그 폴더에 토큰 파일을 쓰고, 서버에게 "그 경로에서 이 토큰이 읽히나 + 너도 거기 쓸 수 있나"를 묻는다. 둘 다 참이면 `exportScenes`에 `out_dir`을 넘겨 서버가 그 폴더에 직접 굽는다(굽기 파이프라인은 이미 이 경로를 지원한다). 어떤 실패든 — 폴더 없음·토큰 불일치·쓰기 거부·구버전 서버 404·예외 — `direct=false`로 수렴해 v1.7.3의 중계 동작 그대로 간다.

**Tech Stack:** FastAPI + Pydantic(서버) · Tauri v2 + Rust(클라 커맨드) · React + TypeScript(UI) · pytest / cargo test / vitest

## Global Constraints

- 탐침 파일명 접두사는 **`yeson_probe_`** — TypeScript(`probeFileName`)·Rust(`PROBE_PREFIX`)·Python(`_PROBE_PREFIX`) 세 곳이 지키는 계약이다. 한쪽만 바꾸면 탐침이 조용히 실패해 같은 PC에서도 느린 중계 경로로 떨어진다.
- 탐침의 어떤 실패도 익스포트를 막지 않는다. **모든 실패 경로는 `direct=false`(중계)로 수렴한다.**
- 클라·서버는 **각자 자기가 만든 탐침 파일만** 지운다. 사용자 파일을 지울 수 있는 경로를 만들지 않는다.
- 직접 모드에서는 `cleanupSceneExport`를 호출하지 않는다 — 사용자 폴더의 파일이 결과물이다.
- 굽기 파이프라인(`apps/server/domain/video_captions/pipeline.py`)은 **변경하지 않는다** — `out_dir` 경로가 이미 완성되어 있다(`pipeline.py:1574`).
- 기존 파일만 수정한다(새 소스 파일 없음). 프로젝트 규칙: 가능한 가장 작은 패치, 요청한 파일만.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `apps/desktop/src-tauri/src/video_download.rs` | 탐침 파일 쓰기/삭제 커맨드 + 접두사 가드. `download_to_file`과 같은 이유로 Rust에 둔다(fs 플러그인 스코프가 `D:\`·네트워크 폴더를 거부). |
| `apps/desktop/src-tauri/src/lib.rs` | 커맨드 2개 등록 |
| `apps/server/api/v1/video_jobs.py` | 탐침 라우트 — 토큰 읽기 확인 + 서버 쓰기 확인 |
| `apps/desktop/src/console/sceneSplitLogic.ts` | `probeFileName(token)` — 3개 언어가 공유하는 파일명 계약의 단일 출처 |
| `apps/desktop/src/console/videoApi.ts` | `probeExportDir()` HTTP 래퍼 |
| `apps/desktop/src/console/SceneSplitView.tsx` | `probeDirect()` + 익스포트 두 곳의 분기·완료 문구 |
| `apps/desktop/src/help/helpManualContent.ts` | 사용자 도움말 한 문장 |

---

### Task 1: Rust 탐침 커맨드

**Files:**
- Modify: `apps/desktop/src-tauri/src/video_download.rs` (현재 19줄 — 끝에 추가)
- Modify: `apps/desktop/src-tauri/src/lib.rs:41` (invoke_handler 목록)
- Test: `apps/desktop/src-tauri/src/video_download.rs` (`#[cfg(test)] mod tests` — `video_upload.rs:67-93`과 같은 배치)

**Interfaces:**
- Consumes: 없음(첫 태스크)
- Produces: Tauri 커맨드 `probe_file_write(path: String, token: String) -> Result<(), String>`, `probe_file_remove(path: String) -> Result<(), String>`. JS에서 `invoke("probe_file_write", { path, token })` / `invoke("probe_file_remove", { path })`로 부른다. 상수 `PROBE_PREFIX = "yeson_probe_"`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`apps/desktop/src-tauri/src/video_download.rs` 맨 끝에 추가:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn probe_commands_refuse_non_probe_names() {
        // 이 가드가 없으면 probe_file_remove가 "아무 파일이나 지우는" 커맨드가 된다 —
        // 하필 사용자가 고른 익스포트 폴더를 인자로 받는 커맨드라 위험이 크다.
        let tmp = std::env::temp_dir().join(format!("vdt-guard-{}", std::process::id()));
        std::fs::create_dir_all(&tmp).unwrap();
        let victim = tmp.join("Scene0010.mp4");
        std::fs::write(&victim, b"user-clip").unwrap();
        let victim_s = victim.to_string_lossy().into_owned();

        assert!(probe_file_write(victim_s.clone(), "ddddccccbbbbaaaa".into()).is_err());
        assert!(probe_file_remove(victim_s).is_err());
        assert_eq!(std::fs::read(&victim).unwrap(), b"user-clip");

        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn probe_write_then_remove_roundtrip() {
        let tmp = std::env::temp_dir().join(format!("vdt-rt-{}", std::process::id()));
        std::fs::create_dir_all(&tmp).unwrap();
        let path = tmp.join("yeson_probe_aaaabbbbccccdddd.tmp");
        let path_s = path.to_string_lossy().into_owned();

        probe_file_write(path_s.clone(), "aaaabbbbccccdddd".into()).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "aaaabbbbccccdddd");

        probe_file_remove(path_s.clone()).unwrap();
        assert!(!path.exists());
        // 이미 없는 파일 삭제도 성공이어야 한다 — 클라가 finally에서 무조건 부르므로
        // 여기서 에러를 던지면 실패 경로가 요란해진다.
        probe_file_remove(path_s).unwrap();

        std::fs::remove_dir_all(&tmp).ok();
    }
}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml probe`
Expected: FAIL — `cannot find function 'probe_file_write' in this scope` (컴파일 에러). 첫 빌드는 몇 분 걸릴 수 있다.

- [ ] **Step 3: 최소 구현**

`apps/desktop/src-tauri/src/video_download.rs` — 파일 맨 위 `#[tauri::command]` 앞에 `use std::path::Path;`를 추가하고, `download_to_file` 아래(테스트 모듈 위)에 추가:

```rust
/// 탐침 파일 이름 접두사 — sceneSplitLogic.ts `probeFileName`, 서버 `_PROBE_PREFIX`와
/// 같은 계약이다. 한쪽만 바꾸면 탐침이 조용히 실패해 같은 PC에서도 중계로 떨어진다.
const PROBE_PREFIX: &str = "yeson_probe_";

/// 이 접두사로 시작하는 파일만 허용한다. 두 커맨드는 사용자가 고른 익스포트 폴더를
/// 인자로 받으므로, 가드가 없으면 "아무 파일 쓰기/지우기" 표면이 열린다.
fn probe_path(path: &str) -> Result<&Path, String> {
    let p = Path::new(path);
    let ok = p.file_name()
        .and_then(|n| n.to_str())
        .map(|n| n.starts_with(PROBE_PREFIX))
        .unwrap_or(false);
    if !ok {
        return Err(format!("탐침 파일이 아닙니다: {path}"));
    }
    Ok(p)
}

/// 익스포트 폴더에 토큰 한 줄짜리 탐침 파일을 쓴다.
///
/// plugin-fs의 writeFile은 capabilities 스코프($HOME/**)에 묶여 윈도우의 다른
/// 드라이브(D:\)·네트워크 폴더에서 거부된다 — download_to_file이 Rust로 내려간 것과
/// 같은 이유로 여기도 Rust에서 쓴다.
#[tauri::command]
pub fn probe_file_write(path: String, token: String) -> Result<(), String> {
    let p = probe_path(&path)?;
    std::fs::write(p, token.as_bytes()).map_err(|e| e.to_string())
}

/// 탐침 파일을 지운다. 없는 파일은 성공으로 본다 — 클라가 실패 경로에서도 finally로
/// 무조건 부르기 때문이다.
#[tauri::command]
pub fn probe_file_remove(path: String) -> Result<(), String> {
    let p = probe_path(&path)?;
    match std::fs::remove_file(p) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml probe`
Expected: PASS — `probe_commands_refuse_non_probe_names`, `probe_write_then_remove_roundtrip` 2개 통과

- [ ] **Step 5: 커맨드를 등록한다**

`apps/desktop/src-tauri/src/lib.rs:41` `video_download::download_to_file,` 바로 아래에 두 줄 추가:

```rust
            video_download::download_to_file,
            video_download::probe_file_write,
            video_download::probe_file_remove,
```

- [ ] **Step 6: 컴파일 확인**

Run: `cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml`
Expected: 에러 없이 완료(경고는 무방)

- [ ] **Step 7: 커밋**

```bash
git add apps/desktop/src-tauri/src/video_download.rs apps/desktop/src-tauri/src/lib.rs
git commit -m "feat(desktop): 익스포트 폴더 탐침용 Rust 커맨드"
```

---

### Task 2: 서버 탐침 라우트

**Files:**
- Modify: `apps/server/api/v1/video_jobs.py` (모델은 `SceneExportIn` 아래 = `:171` 부근, 라우트는 `scene_export_status` 아래 = `:806` 부근)
- Test: `apps/server/tests/test_api_video_jobs.py` (기존 `test_scene_export_file_*` 묶음 뒤 = `:1313` 부근)

**Interfaces:**
- Consumes: Task 1의 `PROBE_PREFIX` 계약(`yeson_probe_`) — 클라가 쓴 파일명을 서버가 같은 규칙으로 조립한다.
- Produces: `POST /api/v1/video-jobs/{external_id}/scenes/export/probe`, 본문 `{"dir": str, "token": str}` → 200 `{"direct": bool, "reason": str}`. `reason` 값: `"ok"` · `"not_a_dir"` · `"token_mismatch"` · `"write_denied"`. 잡이 없으면 404.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`apps/server/tests/test_api_video_jobs.py` 끝에 추가:

```python
async def test_scene_export_probe_confirms_shared_folder(
        client, db_session, admin_user, tmp_path):
    """클라가 쓴 토큰이 서버 쪽에서도 같은 경로로 읽히면 = 같은 폴더.

    같은 PC(또는 공유 폴더)면 중계(서버가 굽고→클라가 받고→서버 사본 삭제)가 통째로
    낭비다. 다만 "같은 PC냐"를 추측하면 v1.7.3에서 고친 실패 — 사용자 폴더는 빈 채
    서버 디스크에만 파일이 생기는데 에러도 안 나는 — 가 되살아나므로 증명한다.
    """
    job = await _new_scene_job(db_session, admin_user, status="done")
    # 변수명을 token으로 두지 않는다 — 커밋 훅의 비밀정보 스캐너가 `token = "..."`
    # 꼴을 실제 키로 오인해 커밋을 막는다(실측).
    probe_id = "aaaabbbbccccdddd"
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / f"yeson_probe_{probe_id}.tmp").write_text(probe_id, encoding="utf-8")

    r = await client.post(
        f"/api/v1/video-jobs/{job.external_id}/scenes/export/probe",
        json={"dir": str(shared), "token": probe_id})

    assert r.status_code == 200
    assert r.json() == {"direct": True, "reason": "ok"}
    # 서버가 쓴 ack 파일이 남으면 안 된다 — 탐침은 흔적을 남기지 않는다.
    assert [p.name for p in shared.iterdir()] == [f"yeson_probe_{probe_id}.tmp"]


async def test_scene_export_probe_rejects_when_token_file_absent(
        client, db_session, admin_user, tmp_path):
    """서버에도 같은 경로가 있지만 클라의 토큰 파일이 없다 = 다른 폴더(다른 PC).

    윈도우 매핑 드라이브가 세션마다 다른 곳을 가리키는 경우가 정확히 이 모양이다.
    """
    job = await _new_scene_job(db_session, admin_user, status="done")
    other = tmp_path / "server_side"
    other.mkdir()

    r = await client.post(
        f"/api/v1/video-jobs/{job.external_id}/scenes/export/probe",
        json={"dir": str(other), "token": "aaaabbbbccccdddd"})

    assert r.status_code == 200
    assert r.json() == {"direct": False, "reason": "token_mismatch"}


async def test_scene_export_probe_rejects_stale_token(
        client, db_session, admin_user, tmp_path):
    """지난 실행의 잔여 탐침 파일을 '같은 폴더'로 오인하면 안 된다 — 내용도 대조한다."""
    job = await _new_scene_job(db_session, admin_user, status="done")
    probe_id = "aaaabbbbccccdddd"
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / f"yeson_probe_{probe_id}.tmp").write_text("0000000000000000",
                                                       encoding="utf-8")

    r = await client.post(
        f"/api/v1/video-jobs/{job.external_id}/scenes/export/probe",
        json={"dir": str(shared), "token": probe_id})

    assert r.status_code == 200
    assert r.json() == {"direct": False, "reason": "token_mismatch"}


async def test_scene_export_probe_rejects_missing_dir(
        client, db_session, admin_user, tmp_path):
    """서버에 그 경로가 아예 없다 = 다른 PC. 폴더를 만들어주면 안 된다 —
    v1.7.3에서 고친 '서버에 빈 폴더만 생기던' 실패가 바로 그것이다."""
    job = await _new_scene_job(db_session, admin_user, status="done")
    missing = tmp_path / "nope"

    r = await client.post(
        f"/api/v1/video-jobs/{job.external_id}/scenes/export/probe",
        json={"dir": str(missing), "token": "aaaabbbbccccdddd"})

    assert r.status_code == 200
    assert r.json() == {"direct": False, "reason": "not_a_dir"}
    assert not missing.exists()
```

- [ ] **Step 2: 실패를 확인한다**

Run:
```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest \
  apps/server/tests/test_api_video_jobs.py -k probe -v
```
Expected: 4개 모두 FAIL — 404(라우트 없음)라 `assert r.status_code == 200`에서 깨진다.

- [ ] **Step 3: 모듈 로거를 정의한다 (기존 NameError 동반 수정)**

`video_jobs.py`는 `:871`에서 `logger.exception(...)`을 부르는데 **모듈에 `logger`가 정의되어 있지 않다**(확인함: `import logging`도, `logger = ...`도 없다). 익스포트 정리 중 파일 삭제가 실패하면 그 자리에서 `NameError`가 나 정리 요청이 500으로 끝난다. 아래 라우트도 로거를 쓰므로 여기서 같이 바로잡는다.

`apps/server/api/v1/video_jobs.py`의 `import asyncio`(`:12`) 아래에 `import logging`을 추가하고, `router = APIRouter(...)`(`:72`) 바로 아래에 추가:

```python
logger = logging.getLogger(__name__)
```

- [ ] **Step 4: 최소 구현 — 입력 모델**

`apps/server/api/v1/video_jobs.py`의 `SceneExportIn` 클래스(`:165-170`) 바로 아래에 추가:

```python
class SceneExportProbeIn(BaseModel):
    # dir는 서버 로컬 경로 문자열이다 — 기존 SceneExportIn.out_dir과 신뢰 경계가 같다
    # (LAN을 신뢰 경계로 두는 이 API의 전제, 파일 상단 주석 참조).
    dir: str = Field(min_length=1)
    token: str = Field(min_length=8, max_length=64, pattern="^[0-9a-f]+$")
```

- [ ] **Step 5: 최소 구현 — 라우트**

같은 파일, `scene_export_status`(`:793-805`) 아래·`scene_export_file`(`:808`) 위에 추가:

```python
# 탐침 파일 이름 접두사 — 클라 sceneSplitLogic.ts probeFileName, Rust PROBE_PREFIX와
# 같은 계약. 한쪽만 바꾸면 탐침이 조용히 실패해 같은 PC에서도 중계로 떨어진다.
_PROBE_PREFIX = "yeson_probe_"


@router.post("/{external_id}/scenes/export/probe")
async def scene_export_probe(
    external_id: UUID,
    body: SceneExportProbeIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """서버가 이 폴더에 직접 구워도 되는지 확인한다.

    서버·클라가 같은 PC면 중계(서버가 굽고 → 클라가 HTTP로 받아 쓰고 → 서버 사본
    삭제)가 통째로 낭비다: 디스크에 두 번 쓰고, 굽기가 다 끝난 뒤에야 복사가 시작된다.

    그렇다고 호스트명 따위로 '같은 PC냐'를 추측하면 v1.7.3에서 고친 실패가 되살아난다
    — 서버 디스크에만 파일이 생기고 사용자가 고른 폴더는 끝까지 빈 채로 남는데 에러도
    안 나던 그 버그(실기 윈도우). 그래서 추측하지 않고 증명한다.

    두 가지를 함께 본다. ①클라가 방금 쓴 토큰 파일이 이 경로에서 읽히는가(같은 폴더인가)
    ②서버가 거기에 쓸 수 있는가. 같은 PC여도 ②가 거짓일 수 있다(macOS TCC — 서버 앱은
    클라와 다른 번들이다; 윈도우 제어된 폴더 액세스). 반대로 다른 PC라도 공유 폴더를
    고르면 둘 다 참이고, 그때는 전송 한 번을 통째로 아낀다.

    서버가 쓴 파일은 서버가 지운다 — 어느 경로로 끝나도 잔여물이 없다.
    """
    await _get_job_or_404(db, external_id)
    dest = Path(body.dir)
    # 폴더를 만들지 않는다 — 서버에 빈 폴더만 생기던 그 실패를 재현하지 않기 위해서다.
    if not dest.is_dir():
        return {"direct": False, "reason": "not_a_dir"}

    mine = dest / f"{_PROBE_PREFIX}{body.token}.tmp"
    # 방금 만들어진 파일이 백신 검사나 SMB 음성 캐싱으로 잠깐 안 보일 수 있다.
    for attempt in range(3):
        if attempt:
            await asyncio.sleep(0.3)
        try:
            if mine.read_text(encoding="utf-8").strip() == body.token:
                break
        except OSError:
            continue
    else:
        return {"direct": False, "reason": "token_mismatch"}

    ack = dest / f"{_PROBE_PREFIX}ack_{body.token}.tmp"
    try:
        ack.write_text(body.token, encoding="utf-8")
        if ack.read_text(encoding="utf-8") != body.token:
            return {"direct": False, "reason": "write_denied"}
    except OSError:
        logger.info("scene export probe: 서버가 %s 에 쓸 수 없다 — 중계로 간다", dest)
        return {"direct": False, "reason": "write_denied"}
    finally:
        try:
            ack.unlink(missing_ok=True)
        except OSError:
            logger.exception("탐침 ack 파일 삭제 실패: %s", ack)
    return {"direct": True, "reason": "ok"}
```

- [ ] **Step 6: 테스트 통과 확인**

Run:
```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest \
  apps/server/tests/test_api_video_jobs.py -k probe -v
```
Expected: 4 passed

- [ ] **Step 7: 기존 익스포트 테스트 무회귀 확인**

Run:
```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest \
  apps/server/tests/test_api_video_jobs.py -q
```
Expected: 전량 통과(실패 0)

- [ ] **Step 8: 커밋**

```bash
git add apps/server/api/v1/video_jobs.py apps/server/tests/test_api_video_jobs.py
git commit -m "feat(server): 익스포트 폴더 공유 여부 탐침 라우트

정리 라우트가 부르던 logger가 모듈에 정의되어 있지 않아 삭제 실패 시
NameError가 나던 것도 함께 고친다."
```

---

### Task 3: 클라 파일명 계약 + API 래퍼

**Files:**
- Modify: `apps/desktop/src/console/sceneSplitLogic.ts` (`neighborIndices`가 있는 `:634` 부근 뒤)
- Modify: `apps/desktop/src/console/videoApi.ts` (`cleanupSceneExport`가 있는 `:433` 뒤)
- Test: `apps/desktop/src/console/sceneSplitLogic.test.ts`

**Interfaces:**
- Consumes: Task 2의 라우트 `POST .../scenes/export/probe` → `{direct, reason}`
- Produces: `probeFileName(token: string): string` (sceneSplitLogic), `probeExportDir(jobId: string, dir: string, token: string): Promise<{ direct: boolean; reason: string }>` (videoApi)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`apps/desktop/src/console/sceneSplitLogic.test.ts` 끝에 추가:

```ts
describe("probeFileName", () => {
  it("keeps the yeson_probe_ prefix shared with Rust and the server", () => {
    // 이 접두사는 3개 언어가 함께 지키는 계약이다: Rust probe_file_write/remove가
    // 이걸로 시작하지 않는 경로를 거부하고, 서버도 같은 이름으로 파일을 찾는다.
    // 한쪽만 바뀌면 탐침이 조용히 실패해 같은 PC에서도 느린 중계 경로로 떨어진다.
    expect(probeFileName("aaaabbbbccccdddd"))
      .toBe("yeson_probe_aaaabbbbccccdddd.tmp");
    expect(probeFileName("ddddccccbbbbaaaa").startsWith("yeson_probe_")).toBe(true);
  });
});
```

같은 파일 첫 줄의 import 목록 끝(`exportedFileName` 뒤)에 `, probeFileName`을 추가한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `pnpm -C apps/desktop test -- sceneSplitLogic`
Expected: FAIL — `probeFileName is not a function` (또는 import 해석 실패)

- [ ] **Step 3: 최소 구현**

`apps/desktop/src/console/sceneSplitLogic.ts`의 `neighborIndices`(`:634-639`) 아래에 추가:

```ts
// 익스포트 탐침 파일 이름. 접두사 `yeson_probe_`는 Rust(probe_file_write/remove의
// PROBE_PREFIX)와 서버(_PROBE_PREFIX)가 함께 지키는 계약이다 — Rust는 이 접두사가
// 아닌 경로를 거부하고(사용자 파일을 지울 통로를 막는다), 서버는 같은 이름으로
// 파일을 찾는다. 한쪽만 바꾸면 탐침이 조용히 실패해 같은 PC에서도 중계로 떨어진다.
export function probeFileName(token: string): string {
  return `yeson_probe_${token}.tmp`;
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pnpm -C apps/desktop test -- sceneSplitLogic`
Expected: PASS

- [ ] **Step 5: API 래퍼 추가**

`apps/desktop/src/console/videoApi.ts`의 `cleanupSceneExport`(`:430-433`) 아래에 추가:

```ts
// 서버가 이 폴더에 직접 구워도 되는지 확인한다. 클라가 방금 쓴 탐침 파일이 서버
// 쪽에서도 같은 경로로 읽히고 서버도 거기에 쓸 수 있으면 direct=true — 같은 PC이거나
// 같은 공유 폴더라는 증거다. 그때는 굽기→받기 중계를 통째로 건너뛴다.
// 구버전 서버에는 이 라우트가 없어 404가 나는데, 호출자가 잡아 중계로 폴백한다.
export async function probeExportDir(
  jobId: string, dir: string, token: string,
): Promise<{ direct: boolean; reason: string }> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/export/probe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dir, token }),
  });
}
```

- [ ] **Step 6: 타입 검사**

Run: `pnpm -C apps/desktop build:vite`
Expected: `tsc --noEmit` 통과 후 vite 빌드 성공

- [ ] **Step 7: 커밋**

```bash
git add apps/desktop/src/console/sceneSplitLogic.ts \
        apps/desktop/src/console/sceneSplitLogic.test.ts \
        apps/desktop/src/console/videoApi.ts
git commit -m "feat(desktop): 익스포트 탐침 파일명 계약과 API 래퍼"
```

---

### Task 4: 익스포트 분기 배선

**Files:**
- Modify: `apps/desktop/src/console/SceneSplitView.tsx` (import `:4-17`, `saveExportedFiles` 아래 `:363` 부근, `exportOne` `:391-433`, `doExport` `:435-469`)
- Modify: `apps/desktop/src/help/helpManualContent.ts:219`

**Interfaces:**
- Consumes: Task 1의 커맨드 `probe_file_write`/`probe_file_remove`, Task 3의 `probeFileName`·`probeExportDir`, 기존 `exportScenes(jobId, mode, outDir?, indices?)`
- Produces: 없음(최종 배선)

- [ ] **Step 1: import를 추가한다**

`apps/desktop/src/console/SceneSplitView.tsx`:
- `:4-8`의 sceneSplitLogic import 목록에서 `neighborIndices` 뒤에 `, probeFileName`을 추가
- `:10-17`의 videoApi import 목록에서 `cleanupSceneExport, sceneExportFileUrl,` 줄 뒤에 `probeExportDir,`를 추가

- [ ] **Step 2: probeDirect를 추가한다**

`saveExportedFiles` 함수(`:350-363`) 바로 아래에 추가:

```ts
  // 서버가 사용자가 고른 그 폴더에 직접 구워도 되는지 한 번 확인한다(수십 ms).
  //
  // 같은 PC면 중계(서버가 굽고 → 클라가 받아 쓰고 → 서버 사본 삭제)가 통째로 낭비다:
  // 같은 바이트를 디스크에 두 번 쓰고, 굽기가 전부 끝난 뒤에야 복사가 시작된다.
  // 그렇다고 '같은 PC냐'를 호스트명으로 추측하면 위(saveExportedFiles)에 적힌 그
  // 실패가 되살아난다 — 사용자 폴더는 빈 채 서버에만 파일이 생기는데 에러도 안 나던.
  // 그래서 추측하지 않고 증명한다: 여기서 쓴 토큰 파일을 서버가 같은 경로에서 읽고,
  // 서버도 거기 쓸 수 있을 때만 직접 모드.
  //
  // 어떤 실패든(구버전 서버의 404 포함) false를 돌려 기존 중계 경로로 간다 — 이 확인이
  // 익스포트를 막는 일은 없어야 한다.
  const probeDirect = async (dir: string): Promise<boolean> => {
    if (!hasTauriRuntime()) return false;
    const bytes = crypto.getRandomValues(new Uint8Array(8));
    const token = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    const { join } = await import("@tauri-apps/api/path");
    const { invoke } = await import("@tauri-apps/api/core");
    const path = await join(dir, probeFileName(token));
    try {
      await invoke("probe_file_write", { path, token });
      const res = await probeExportDir(jobId, dir, token);
      return res.direct === true;
    } catch {
      return false;
    } finally {
      // 실패 경로에서도 우리가 만든 파일은 치운다(없으면 Rust가 성공으로 처리).
      try { await invoke("probe_file_remove", { path }); } catch { /* 잔여물뿐 */ }
    }
  };
```

- [ ] **Step 3: exportOne을 분기시킨다**

`exportOne`(`:391-433`)에서 `const saveDir = await pickSaveDir(true);` 블록 다음(`:409` 뒤, `setBusy(true)` 앞)에 한 줄 추가:

```ts
    const direct = saveDir ? await probeDirect(saveDir) : false;
```

이어서 `try` 블록 안의 `:414-427`을 아래로 교체:

```ts
      // 직접 모드면 out_dir을 넘겨 서버가 그 폴더에 바로 굽는다. 아니면 넘기지
      // 않는다 — 서버는 자기 폴더에 굽고, 받아 쓰는 건 아래에서.
      const res = await exportScenes(jobId, mode, direct ? saveDir : undefined,
                                     indices);
      const labels = indices.map((k) => segments[k]?.label ?? "?").join(", ");
      const st = await pollExport((s) => direct
        ? `${s.files?.length ?? res.count}개 클립 저장 완료 — ${labels} (${saveDir}). `
          + "경계를 공유한 이웃 씬까지 갱신했습니다."
        : `${res.count}개 클립을 구웠습니다 — ${labels}. 저장 중…`);
      if (!st) return;
      // 직접 모드는 서버가 이미 사용자 폴더에 썼다 — 받을 것도, 지울 사본도 없다.
      if (direct) return;
      if (saveDir) {
        await saveExportedFiles(st.files ?? [], saveDir);
        setNotice(`${st.files?.length ?? 0}개 클립 저장 완료 — ${labels} (${saveDir}). `
          + "경계를 공유한 이웃 씬까지 갱신했습니다.");
      } else {
        setNotice(`${res.count}개 클립 익스포트 완료 — ${labels} `
          + `(서버 폴더 ${st.out_dir ?? ""}).`);
      }
```

- [ ] **Step 4: doExport를 분기시킨다**

`doExport`(`:435-469`)에서 `const saveDir = await pickSaveDir(false);` 블록 다음(`:448` 뒤, `setBusy(true)` 앞)에 한 줄 추가:

```ts
    const direct = saveDir ? await probeDirect(saveDir) : false;
```

이어서 `try` 블록 안의 `:453-462`를 아래로 교체:

```ts
      // 직접 모드면 out_dir을 넘겨 서버가 그 폴더에 바로 굽는다. 아니면 넘기지
      // 않는다 — 서버는 자기 폴더에 굽고, 받아 쓰는 건 아래에서.
      const res = await exportScenes(jobId, mode, direct ? saveDir : undefined);
      const st = await pollExport((s) => direct
        ? `${s.files?.length ?? res.count}개 클립 저장 완료 (${saveDir})`
        : `${res.count}개 클립을 구웠습니다. 저장 중…`);
      if (!st) return;
      // 직접 모드는 서버가 이미 사용자 폴더에 썼다 — 받을 것도, 지울 사본도 없다.
      if (direct) return;
      if (saveDir) {
        await saveExportedFiles(st.files ?? [], saveDir);
        setNotice(`${st.files?.length ?? 0}개 클립 저장 완료 (${saveDir})`);
      } else {
        setNotice(`${res.count}개 클립 익스포트 완료 (서버 폴더 ${st.out_dir ?? ""})`);
      }
```

- [ ] **Step 5: 도움말 한 문장 추가**

`apps/desktop/src/help/helpManualContent.ts:219`의 씬 분할 설명 문자열에서 `"…씬별(또는 시퀀스별) mp4가 라벨 이름으로 저장됩니다."` 바로 뒤에 아래 문장을 이어 붙인다:

```
서버와 클라이언트가 같은 PC이거나 같은 공유 폴더를 쓰면 고른 폴더에 바로 저장되어 더 빠르고, 다른 PC면 서버가 구운 뒤 받아서 저장합니다 — 어느 쪽이든 결과는 같습니다.
```

- [ ] **Step 6: 타입 검사와 기존 테스트**

Run: `pnpm -C apps/desktop build:vite && pnpm -C apps/desktop test`
Expected: tsc 통과 + vitest 전량 통과

- [ ] **Step 7: 커밋**

```bash
git add apps/desktop/src/console/SceneSplitView.tsx \
        apps/desktop/src/help/helpManualContent.ts
git commit -m "feat(desktop): 같은 PC면 지정 폴더에 직접 익스포트"
```

---

### Task 5: 재동결 + 실기 검증

**Files:**
- 변경 없음(빌드·검증 전용)

**Interfaces:**
- Consumes: Task 1~4 전부
- Produces: 없음

- [ ] **Step 1: 서버 번들을 재동결한다**

서버에 라우트를 추가했으므로 재동결하지 않으면 `tauri:dev`에서도 옛 번들이 떠서 탐침이 404다(그래도 중계 모드로 정상 동작하므로 "느려질 뿐"이지만, 직접 모드를 검증할 수 없다).

Run: `./apps/server_desktop/scripts/build-server.sh`
Expected: 성공 종료. 실행 중인 서버 앱이 있으면 재시작한다.

- [ ] **Step 2: 맥 같은 PC — 직접 모드 확인**

`pnpm -C apps/desktop tauri:dev`로 클라를 띄우고, 완료된 씬 분할 잡에서 익스포트 → 폴더 선택.

확인할 것:
- 굽는 **도중에** 그 폴더에 mp4가 하나씩 쌓인다(중계 모드는 끝난 뒤 한꺼번에 찬다 — 이게 직접 모드가 붙었다는 눈에 보이는 증거다)
- 진행 표시의 폴더 경로가 서버 작업 폴더가 아니라 **고른 폴더**로 나온다
- 완료 후 서버 작업 폴더의 `scene_out/`에 사본이 생기지 않았다
- 폴더에 `yeson_probe_*` 잔여 파일이 없다

- [ ] **Step 3: 맥 폴백 확인**

폴더 선택창에서 서버가 접근할 수 없는 위치(예: 외장 디스크를 뽑은 뒤 그 경로, 또는 권한 없는 폴더)를 고르거나, 서버 번들을 옛 것으로 되돌려 404를 만든다.

확인할 것: 익스포트가 **실패하지 않고** 중계 모드로 끝나며 파일이 정상 저장된다(진행 문구에 "저장 중 n/N"이 나온다).

- [ ] **Step 4: 윈도우 실기 — 같은 PC**

윈도우 PC에 서버·클라를 함께 설치한 상태에서 익스포트한다. `D:\` 등 시스템 드라이브가 **아닌** 경로를 최소 한 번 포함한다.

확인할 것: Step 2와 동일 + `D:\` 경로에서도 직접 모드가 붙는지.

- [ ] **Step 5: 윈도우 실기 — 다른 PC**

서버와 클라를 서로 다른 윈도우 PC에 두고 익스포트한다.

확인할 것: 중계 모드로 떨어지고 v1.7.3과 동일하게 동작한다(사용자 폴더에 전부 저장, 서버 `scene_out/` 사본 삭제됨). 서버 쪽에 사용자가 고른 경로의 **빈 폴더가 생기지 않았는지**도 확인한다.

- [ ] **Step 6: PR 생성**

```bash
git push -u origin feature/scene-export-same-pc-direct
gh pr create --base main --title "feat: 같은 PC면 씬 익스포트를 지정 폴더에 직접 굽기" --body "$(cat <<'EOF'
## 요약
서버·클라가 같은 폴더를 공유한다는 것이 증명될 때만 익스포트 중계를 건너뛰고 사용자가 고른 폴더에 바로 굽는다. 디스크 쓰기 2배·피크 용량 2배·복사 패스가 사라지고, 클립이 굽는 즉시 폴더에 보인다.

판정은 추측이 아니라 탐침 왕복이다 — 클라가 쓴 토큰 파일을 서버가 같은 경로에서 읽고 서버도 거기 쓸 수 있을 때만 직접 모드. 실패는 전부 기존 중계로 수렴하므로 구버전 서버에서도 깨지지 않는다.

설계: `docs/superpowers/specs/2026-07-28-scene-export-same-pc-direct-design.md`

## 검증
- cargo test(탐침 커맨드 가드·왕복), pytest(탐침 라우트 4케이스), vitest(파일명 계약)
- 실기: 맥 같은 PC 직접 모드 / 폴백 / 윈도우 같은 PC / 윈도우 다른 PC

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01VwNSe6SopfMBiM1QAqCi1h
EOF
)"
```

> **주의**: 머지는 사용자가 직접 한다(`! gh pr merge`) — 이 리포는 자기 PR 머지가 가드에 막힌다.

---

## 자체 점검 결과

**설계 커버리지** — 설계 문서 각 절이 어느 태스크에 있는지:
- §2 결정 1·2(증명 방식) → Task 2 라우트 + Task 4 `probeDirect`
- §2 결정 3(탐침 왕복) → Task 1·2·4
- §2 결정 4(전부 폴백) → Task 4 `catch { return false }` + Task 2의 200 응답
- §2 결정 5(직접 모드는 cleanup 미호출) → Task 4의 `if (direct) return;`
- §2 결정 6(매 익스포트 탐침) → Task 4 두 함수 모두에 `probeDirect` 호출
- §3 프로토콜·API·Rust 커맨드 → Task 1·2·3
- §4 클라 흐름 → Task 4
- §5 윈도우(재시도·접두사 카나리아·잠금) → Task 2 재시도 루프, Task 1 파일명, 기존 에러 문구 유지
- §6 변경 파일 → Task 1~4가 8개 파일 전부 덮는다
- §7 검증 → Task 1·2·3의 테스트 단계 + Task 5 실기 4종

**설계에 없던 동반 수정 하나**: `video_jobs.py:871`이 정의되지 않은 `logger`를 부른다(모듈에 `import logging`도 `logger = ...`도 없다). 익스포트 정리 중 파일 삭제가 실패하면 `NameError`로 500이 된다. 새 라우트도 로거를 쓰므로 Task 2 Step 3에서 같이 고친다 — 같은 파일, 두 줄이다.

**남은 판단 하나**: 설계 §5의 260자 카나리아는 파일명 길이(`yeson_probe_<16hex>.tmp` = 32자 > 클립 `0240ACV01N.mp4` ≈ 14자)로 자연히 얻어진다 — 별도 코드가 없다. 의도된 절충(경로가 아슬아슬하면 직접 모드가 안 붙고 중계로 떨어지며, 저장 자체는 정상)이므로 태스크를 추가하지 않는다.
