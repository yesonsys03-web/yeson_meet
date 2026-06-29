# 클라이언트 무설정 온보딩 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 클라이언트 설정에서 서버 IP와 device key 수동 입력을 제거한다 — 서버 주소는 자동발견(localhost→mDNS), device key는 operator 로그인으로 자동 self-enroll.

**Architecture:** (1) 서버 백엔드에 operator 전용 self-enroll 엔드포인트 추가. (2) 서버 데스크톱이 자기 LAN IP를 감지·표시하고 mDNS로 광고. (3) 클라이언트가 localhost 프로브→mDNS로 주소를 자동 결정하고, operator 로그인 토큰으로 device key를 자동 발급받아 저장. 설정 폼은 4칸→2칸(이메일·비번)으로 축소.

**Tech Stack:** Python/FastAPI(서버), Rust/Tauri v2(데스크톱 양쪽), React/TypeScript(UI), `mdns-sd`·`local-ip-address`(Rust), Vitest·Pytest.

## Global Constraints

- 서버는 LAN에 **1대** 전제 — 다중서버 선택 UI 없음.
- device key는 **무TTL 베어러 키** → QR/페어링 코드에 절대 싣지 않음. self-enroll은 device-admin(`require_admin`, `POST /api/v1/devices`)과 **분리된 엔드포인트**로, operator에게 **자기 키 1개만** 발급.
- operator 로그인·키체인 저장·원클릭 회의 흐름은 **현행 유지**(이번 범위에서 인증 모델 변경 금지).
- 키체인이 자격증명 권위, localStorage는 파생 캐시. `deviceApiKey`는 TS로 새어나가지 않음(`storeValues`/`loadValues`에서 항상 strip).
- 주소만 변경 시 `update_server_ws_base`(Rust 부분병합, 키 보존) 사용.
- mDNS 서비스 타입: `_yeson-meet._tcp.local.` / 기본 포트 `8000`.
- Rust 의존성 추가: `mdns-sd`, `local-ip-address`. **버전은 crates.io에 실제 존재하는 최신 호환 버전으로** 확정(빌드로 확인). 확인된 값: `local-ip-address = "0.6"`(0.14는 미존재). `mdns-sd`도 동일하게 실재 버전 사용.

---

## 파일 구조

**신규 생성:**
- `apps/desktop/src-tauri/src/discovery.rs` — 클라이언트 mDNS 브라우즈 커맨드.
- `apps/desktop/src/setup/serverDiscovery.ts` — localhost 프로브 + 주소 조립(순수 로직).
- `apps/desktop/src/setup/serverDiscovery.test.ts` — 위 테스트.

**수정:**
- `apps/server/api/v1/devices.py` — self-enroll 엔드포인트.
- `apps/server/tests/test_device_provisioning.py` — self-enroll 테스트.
- `apps/server_desktop/src-tauri/Cargo.toml` / `src/lib.rs` / `src/server_process.rs` — IP 감지 + mDNS 광고.
- `apps/server_desktop/src/ServerConsole.tsx` — 서버 주소 배너.
- `apps/server_desktop/src/serverAddress.ts`(신규 작은 헬퍼) + `serverAddress.test.ts` — `ws://ip:port` 조립 순수함수.
- `apps/desktop/src-tauri/Cargo.toml` / `src/lib.rs` — discovery 커맨드 등록.
- `apps/desktop/src/console/sessionApi.ts` — `selfEnrollDevice` 호출.
- `apps/desktop/src/console/sessionApi.test.ts`(없으면 신규) — self-enroll 테스트.
- `apps/desktop/src/setup/MeetingQuickStartPanel.tsx` — 폼 축소 + 자동발견 + 자동 self-enroll.

---

## Task 1: 서버 self-enroll 엔드포인트

**Files:**
- Modify: `apps/server/api/v1/devices.py`
- Test: `apps/server/tests/test_device_provisioning.py`

**Interfaces:**
- Produces: `POST /api/v1/devices/self-enroll` — body `{ "name": str }`, 인증 `Authorization: Bearer <operatorToken>`, 응답 201 `{ id, name, api_key }`.

- [ ] **Step 1: 실패 테스트 작성** — `apps/server/tests/test_device_provisioning.py` 끝에 추가

```python
@pytest.mark.asyncio
async def test_self_enroll_with_operator_ok(client: AsyncClient, db_session: AsyncSession) -> None:
    operator = AppUser(
        email="op-enroll@test.example",
        name="Op Enroll",
        password_hash=hash_password("op-enroll-pw"),
        role="operator",
        is_active=True,
    )
    db_session.add(operator)
    await db_session.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": operator.email, "password": "op-enroll-pw"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    resp = await client.post(
        "/api/v1/devices/self-enroll",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "client-macpro"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "client-macpro"
    assert body["api_key"]


@pytest.mark.asyncio
async def test_self_enroll_requires_bearer(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/devices/self-enroll", json={"name": "nope"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_self_enroll_rejects_non_privileged(client: AsyncClient, db_session: AsyncSession) -> None:
    viewer = AppUser(
        email="viewer-enroll@test.example",
        name="Viewer",
        password_hash=hash_password("viewer-pw"),
        role="viewer",
        is_active=True,
    )
    db_session.add(viewer)
    await db_session.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": viewer.email, "password": "viewer-pw"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    resp = await client.post(
        "/api/v1/devices/self-enroll",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "nope"},
    )
    assert resp.status_code == 403
```

> 파일 상단 import에 `AppUser`, `hash_password`가 이미 있는지 확인(기존 `test_mint_rejects_non_admin`이 동일하게 사용 → 있음).

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd /Users/usabatch/coding/yeson_dev/yeson_meet && python -m pytest apps/server/tests/test_device_provisioning.py -k self_enroll -v`
Expected: FAIL — 404 Not Found (엔드포인트 미존재).

- [ ] **Step 3: 엔드포인트 구현** — `apps/server/api/v1/devices.py`의 `create_device` 핸들러 바로 아래에 추가

```python
@router.post("/self-enroll", response_model=DeviceCreateOut, status_code=status.HTTP_201_CREATED)
async def self_enroll_device(
    body: DeviceCreateIn,
    _operator: Annotated[AppUser, Depends(require_operator)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceCreateOut:
    # Self-enroll: an operator client provisions ITS OWN single device key.
    # Separate from create_device (require_admin) so the client never gains
    # device-admin (list/revoke). Issuance still happens server-side.
    plaintext = generate_api_key()
    device = Device(
        name=body.name,
        api_key_hash=hash_api_key(plaintext),
        is_active=True,
    )
    db.add(device)
    await db.flush()
    await db.commit()
    return DeviceCreateOut(id=device.id, name=device.name, api_key=plaintext)
```

> `devices.py` 상단 import에 `require_operator`를 추가: 기존 `from apps.server.auth.deps import require_admin` 줄을 `from apps.server.auth.deps import require_admin, require_operator` 로 수정.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd /Users/usabatch/coding/yeson_dev/yeson_meet && python -m pytest apps/server/tests/test_device_provisioning.py -v`
Expected: PASS (신규 3개 포함 전부 green).

- [ ] **Step 5: 커밋**

```bash
git add apps/server/api/v1/devices.py apps/server/tests/test_device_provisioning.py
git commit -m "feat(server): operator device self-enroll endpoint (separate from device-admin)"
```

---

## Task 2: 서버 LAN IP 감지 커맨드 (Rust)

**Files:**
- Modify: `apps/server_desktop/src-tauri/Cargo.toml`, `apps/server_desktop/src-tauri/src/server_process.rs`, `apps/server_desktop/src-tauri/src/lib.rs`

**Interfaces:**
- Produces: Tauri 커맨드 `detect_lan_ip() -> Result<String, String>` (주 LAN IPv4 문자열, 예 `"192.168.1.23"`).

- [ ] **Step 1: 의존성 추가** — `apps/server_desktop/src-tauri/Cargo.toml`의 `regex = "1"` 줄 다음에

```toml
local-ip-address = "0.14"
```

- [ ] **Step 2: 커맨드 구현** — `apps/server_desktop/src-tauri/src/server_process.rs` 파일 끝에 추가

```rust
#[tauri::command]
pub fn detect_lan_ip() -> Result<String, String> {
    local_ip_address::local_ip()
        .map(|ip| ip.to_string())
        .map_err(|error| format!("LAN IP 감지 실패: {error}"))
}
```

- [ ] **Step 3: 커맨드 등록** — `apps/server_desktop/src-tauri/src/lib.rs`의 `generate_handler!` 목록에서 `tunnel::lan_viewer_base_cmd,` 다음 줄에 추가

```rust
            server_process::detect_lan_ip,
```

- [ ] **Step 4: 빌드 확인**

Run: `cd /Users/usabatch/coding/yeson_dev/yeson_meet/apps/server_desktop/src-tauri && cargo build 2>&1 | tail -20`
Expected: 컴파일 성공(에러 0). `local-ip-address` 다운로드/빌드 포함.

- [ ] **Step 5: 커밋**

```bash
git add apps/server_desktop/src-tauri/Cargo.toml apps/server_desktop/src-tauri/Cargo.lock apps/server_desktop/src-tauri/src/server_process.rs apps/server_desktop/src-tauri/src/lib.rs
git commit -m "feat(server_desktop): detect_lan_ip Tauri command"
```

---

## Task 3: 서버 콘솔 "내 서버 주소" 배너 (TS)

**Files:**
- Create: `apps/server_desktop/src/serverAddress.ts`, `apps/server_desktop/src/serverAddress.test.ts`
- Modify: `apps/server_desktop/src/ServerConsole.tsx`

**Interfaces:**
- Consumes: `detect_lan_ip` (Task 2).
- Produces: `formatServerWsAddress(ip: string, port: number): string`.

- [ ] **Step 1: 순수함수 테스트 작성** — `apps/server_desktop/src/serverAddress.test.ts`

```typescript
import { describe, expect, it } from "vitest";
import { formatServerWsAddress } from "./serverAddress";

describe("formatServerWsAddress", () => {
  it("builds a ws:// address from ip and port", () => {
    expect(formatServerWsAddress("192.168.1.23", 8000)).toBe("ws://192.168.1.23:8000");
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd /Users/usabatch/coding/yeson_dev/yeson_meet/apps/server_desktop && npx vitest run src/serverAddress.test.ts`
Expected: FAIL — 모듈/함수 없음.

- [ ] **Step 3: 구현** — `apps/server_desktop/src/serverAddress.ts`

```typescript
export function formatServerWsAddress(ip: string, port: number): string {
  return `ws://${ip}:${port}`;
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd /Users/usabatch/coding/yeson_dev/yeson_meet/apps/server_desktop && npx vitest run src/serverAddress.test.ts`
Expected: PASS.

- [ ] **Step 5: 콘솔에 배너 연결** — `apps/server_desktop/src/ServerConsole.tsx`

(a) import 추가(파일 상단 import 블록):

```tsx
import { formatServerWsAddress } from "./serverAddress";
```

(b) 상태 훅 추가(기존 `useState` 선언들 근처, status 관련 state 옆):

```tsx
const [lanIp, setLanIp] = useState<string | null>(null);
```

(c) `refreshStatus`(invoke 패턴이 있는 콜백) 안, `server_status` 갱신 직후에 추가:

```tsx
try {
  setLanIp(await invoke<string>("detect_lan_ip"));
} catch {
  setLanIp(null);
}
```

(d) 상태 그리드(`<dl style={styles.statusGrid}>` ... `</dl>`) 바로 아래에 배너 추가:

```tsx
{running && lanIp ? (
  <div style={styles.lanAddressBanner}>
    <span>내 서버 주소</span>
    <code style={styles.lanAddress}>{formatServerWsAddress(lanIp, liveStatus?.port ?? port)}</code>
    <button
      type="button"
      onClick={() => navigator.clipboard.writeText(formatServerWsAddress(lanIp, liveStatus?.port ?? port))}
    >
      복사
    </button>
  </div>
) : null}
```

(e) `styles` 객체에 키 추가(다른 style 정의 옆):

```tsx
lanAddressBanner: { display: "flex", gap: 10, alignItems: "center", marginTop: 12, padding: "10px 14px", borderRadius: 12, background: "#eff6ff", border: "1px solid #bfdbfe", color: "#1e3a8a", fontSize: 13 },
lanAddress: { fontWeight: 800 },
```

> `liveStatus`/`port` 식별자는 기존 상태 그리드에서 쓰는 것과 동일하게 맞춘다(파일 내 `liveStatus?.port`, `port` 사용처 참고).

- [ ] **Step 6: 타입체크 + 테스트**

Run: `cd /Users/usabatch/coding/yeson_dev/yeson_meet/apps/server_desktop && npx tsc --noEmit && npx vitest run`
Expected: tsc 에러 0, 전체 테스트 PASS.

- [ ] **Step 7: 커밋**

```bash
git add apps/server_desktop/src/serverAddress.ts apps/server_desktop/src/serverAddress.test.ts apps/server_desktop/src/ServerConsole.tsx
git commit -m "feat(server_desktop): show LAN server address banner with copy"
```

---

## Task 4: 서버 mDNS 광고 (Rust)

**Files:**
- Modify: `apps/server_desktop/src-tauri/Cargo.toml`, `apps/server_desktop/src-tauri/src/server_process.rs`

**Interfaces:**
- Consumes: `detect_lan_ip` 로직(Task 2), `RunningServer.port`.
- Produces: 서버 기동 시 `_yeson-meet._tcp.local.` 광고, 종료 시 해제.

- [ ] **Step 1: 의존성 추가** — `apps/server_desktop/src-tauri/Cargo.toml`에

```toml
mdns-sd = "0.7"
```

- [ ] **Step 2: mDNS 핸들 보관 + 등록 함수** — `apps/server_desktop/src-tauri/src/server_process.rs`

(a) 파일 상단 use 추가:

```rust
use mdns_sd::{ServiceDaemon, ServiceInfo};
```

(b) `RunningServer` 구조체에 필드 추가(기존 `port: u16,` 아래):

```rust
    mdns: Option<ServiceDaemon>,
```

(c) mDNS 등록 헬퍼 추가(파일 내 적당한 위치, 예 `detect_lan_ip` 위):

```rust
/// Advertise the running server on the LAN so clients can auto-discover it.
/// Best-effort: a failure here never blocks server startup.
fn advertise_mdns(port: u16) -> Option<ServiceDaemon> {
    let ip = local_ip_address::local_ip().ok()?;
    let daemon = ServiceDaemon::new().ok()?;
    let host_name = format!("{}.local.", "yeson-meet-server");
    let info = ServiceInfo::new(
        "_yeson-meet._tcp.local.",
        "yeson-meet-server",
        &host_name,
        ip,
        port,
        &[("path", "/")][..],
    )
    .ok()?;
    daemon.register(info).ok()?;
    Some(daemon)
}
```

> mdns-sd 0.7의 `ServiceInfo::new` 시그니처(`ty_domain, instance_name, host_name, ip, port, properties`)와 `ip` 인자 허용 타입을 빌드 에러로 확인하며 맞춘다. 버전에 따라 `ip`는 `&str`/`IpAddr`/`AsIpAddrs`일 수 있다.

- [ ] **Step 3: 기동 시 광고 등록** — `start_server` 내부에서 서버가 `RunningServer`로 저장되는 지점(`*slot = Some(running)` 직전, `running` 생성부)에서 `mdns` 필드를 채운다:

```rust
let mdns = advertise_mdns(port);
let running = RunningServer { child, port, started_at: Instant::now(), mdns };
```

> 기존 `RunningServer { child, port, started_at: ... }` 리터럴을 위 형태로 교체. 다른 생성처가 있으면 동일하게 `mdns: None` 또는 `advertise_mdns(port)`로 채운다.

- [ ] **Step 4: 종료 시 해제** — `shutdown()`(서버 종료 함수)에서 `RunningServer`를 꺼내 정리하는 곳에 추가:

```rust
if let Some(mdns) = running.mdns.take() {
    let _ = mdns.shutdown();
}
```

> `running`을 `as_mut()`/소유로 꺼내는 기존 패턴에 맞춰 `mdns.take()` 호출. 소유로 drop되는 경로면 `ServiceDaemon` drop이 정리하므로 best-effort로 충분.

- [ ] **Step 5: 빌드 확인**

Run: `cd /Users/usabatch/coding/yeson_dev/yeson_meet/apps/server_desktop/src-tauri && cargo build 2>&1 | tail -25`
Expected: 컴파일 성공. 에러 시 `ServiceInfo::new` 인자 타입을 메시지대로 조정.

- [ ] **Step 6: 커밋**

```bash
git add apps/server_desktop/src-tauri/Cargo.toml apps/server_desktop/src-tauri/Cargo.lock apps/server_desktop/src-tauri/src/server_process.rs
git commit -m "feat(server_desktop): advertise server over mDNS for client auto-discovery"
```

---

## Task 5: 클라이언트 mDNS 발견 커맨드 (Rust)

**Files:**
- Create: `apps/desktop/src-tauri/src/discovery.rs`
- Modify: `apps/desktop/src-tauri/Cargo.toml`, `apps/desktop/src-tauri/src/lib.rs`

**Interfaces:**
- Produces: Tauri 커맨드 `discover_server() -> Result<Option<DiscoveredServer>, String>` where `DiscoveredServer { ip: String, port: u16 }`.

- [ ] **Step 1: 의존성 추가** — `apps/desktop/src-tauri/Cargo.toml` `[dependencies]`에

```toml
mdns-sd = "0.7"
```

- [ ] **Step 2: discovery 모듈 구현** — `apps/desktop/src-tauri/src/discovery.rs`

```rust
use std::time::Duration;

use mdns_sd::{ServiceDaemon, ServiceEvent};
use serde::Serialize;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DiscoveredServer {
    pub ip: String,
    pub port: u16,
}

/// Browse the LAN for the single yeson-meet server. Returns the first resolved
/// instance within a short timeout, or None if none is found / mDNS is blocked.
#[tauri::command]
pub fn discover_server() -> Result<Option<DiscoveredServer>, String> {
    let daemon = ServiceDaemon::new().map_err(|e| format!("mDNS 시작 실패: {e}"))?;
    let receiver = daemon
        .browse("_yeson-meet._tcp.local.")
        .map_err(|e| format!("mDNS 브라우즈 실패: {e}"))?;

    let deadline = Duration::from_secs(3);
    let found = loop {
        match receiver.recv_timeout(deadline) {
            Ok(ServiceEvent::ServiceResolved(info)) => {
                if let Some(addr) = info.get_addresses().iter().next() {
                    break Some(DiscoveredServer {
                        ip: addr.to_string(),
                        port: info.get_port(),
                    });
                }
            }
            Ok(_) => continue,
            Err(_) => break None, // timeout / channel closed
        }
    };
    let _ = daemon.shutdown();
    Ok(found)
}
```

> `info.get_addresses()`의 반환 타입(버전별 `&HashSet<Ipv4Addr>` 또는 `&HashSet<IpAddr>`)을 빌드 에러로 확인해 `.iter().next()` 형태를 맞춘다.

- [ ] **Step 3: 모듈 선언 + 커맨드 등록** — `apps/desktop/src-tauri/src/lib.rs`

(a) 모듈 선언부(`mod credentials;` 옆)에 추가:

```rust
mod discovery;
```

(b) `generate_handler!` 목록 마지막 항목 다음에 추가:

```rust
            discovery::discover_server,
```

- [ ] **Step 4: 빌드 확인**

Run: `cd /Users/usabatch/coding/yeson_dev/yeson_meet/apps/desktop/src-tauri && cargo build 2>&1 | tail -25`
Expected: 컴파일 성공. 에러 시 `get_addresses()` 타입에 맞춰 조정.

- [ ] **Step 5: 커밋**

```bash
git add apps/desktop/src-tauri/Cargo.toml apps/desktop/src-tauri/Cargo.lock apps/desktop/src-tauri/src/discovery.rs apps/desktop/src-tauri/src/lib.rs
git commit -m "feat(desktop): discover_server mDNS command"
```

---

## Task 6: 클라이언트 주소 자동결정 로직 (TS, 순수+프로브)

**Files:**
- Create: `apps/desktop/src/setup/serverDiscovery.ts`, `apps/desktop/src/setup/serverDiscovery.test.ts`

**Interfaces:**
- Consumes: `discover_server`(Task 5), `httpBaseFromWs`(기존 setupValues).
- Produces:
  - `wsBaseFromDiscovery(found: { ip: string; port: number }): string`
  - `probeLocalServer(fetchImpl?: typeof fetch): Promise<boolean>`
  - `resolveServerWsBase(deps: ResolveDeps): Promise<string | null>` where `ResolveDeps = { probeLocal: () => Promise<boolean>; discover: () => Promise<{ ip: string; port: number } | null> }`

- [ ] **Step 1: 테스트 작성** — `apps/desktop/src/setup/serverDiscovery.test.ts`

```typescript
import { describe, expect, it } from "vitest";
import { resolveServerWsBase, wsBaseFromDiscovery } from "./serverDiscovery";

describe("wsBaseFromDiscovery", () => {
  it("assembles ws:// from ip and port", () => {
    expect(wsBaseFromDiscovery({ ip: "192.168.1.23", port: 8000 })).toBe("ws://192.168.1.23:8000");
  });
});

describe("resolveServerWsBase", () => {
  it("prefers localhost when the local server responds", async () => {
    const result = await resolveServerWsBase({
      probeLocal: async () => true,
      discover: async () => ({ ip: "192.168.1.23", port: 8000 }),
    });
    expect(result).toBe("ws://127.0.0.1:8000");
  });

  it("falls back to mDNS discovery when localhost is absent", async () => {
    const result = await resolveServerWsBase({
      probeLocal: async () => false,
      discover: async () => ({ ip: "192.168.1.23", port: 8000 }),
    });
    expect(result).toBe("ws://192.168.1.23:8000");
  });

  it("returns null when nothing is found", async () => {
    const result = await resolveServerWsBase({
      probeLocal: async () => false,
      discover: async () => null,
    });
    expect(result).toBeNull();
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd /Users/usabatch/coding/yeson_dev/yeson_meet/apps/desktop && npx vitest run src/setup/serverDiscovery.test.ts`
Expected: FAIL — 모듈 없음.

- [ ] **Step 3: 구현** — `apps/desktop/src/setup/serverDiscovery.ts`

```typescript
import { invoke } from "@tauri-apps/api/core";

const LOCAL_WS_BASE = "ws://127.0.0.1:8000";
const LOCAL_HEALTH_URL = "http://127.0.0.1:8000/api/v1/health";

export type DiscoveredServer = { ip: string; port: number };

export type ResolveDeps = {
  probeLocal: () => Promise<boolean>;
  discover: () => Promise<DiscoveredServer | null>;
};

export function wsBaseFromDiscovery(found: DiscoveredServer): string {
  return `ws://${found.ip}:${found.port}`;
}

export async function probeLocalServer(fetchImpl: typeof fetch = fetch): Promise<boolean> {
  try {
    const response = await fetchImpl(LOCAL_HEALTH_URL);
    return response.ok;
  } catch {
    return false;
  }
}

export async function discoverServer(): Promise<DiscoveredServer | null> {
  try {
    return (await invoke<DiscoveredServer | null>("discover_server")) ?? null;
  } catch {
    return null;
  }
}

export async function resolveServerWsBase(deps: ResolveDeps): Promise<string | null> {
  if (await deps.probeLocal()) return LOCAL_WS_BASE;
  const found = await deps.discover();
  return found ? wsBaseFromDiscovery(found) : null;
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd /Users/usabatch/coding/yeson_dev/yeson_meet/apps/desktop && npx vitest run src/setup/serverDiscovery.test.ts`
Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add apps/desktop/src/setup/serverDiscovery.ts apps/desktop/src/setup/serverDiscovery.test.ts
git commit -m "feat(desktop): server address auto-resolve (localhost probe -> mDNS)"
```

---

## Task 7: 클라이언트 device key self-enroll 호출 (TS)

**Files:**
- Modify: `apps/desktop/src/console/sessionApi.ts`
- Test: `apps/desktop/src/console/sessionApi.test.ts` (없으면 생성)

**Interfaces:**
- Consumes: `apiBase`, `authHeaders`, `timedFetch`, `parseJsonResponse`(기존 sessionApi 내부).
- Produces: `selfEnrollDevice(operatorToken: string, name: string): Promise<string>` — 발급된 `api_key` 반환.

- [ ] **Step 1: 테스트 작성** — `apps/desktop/src/console/sessionApi.test.ts`에 추가(없으면 새 파일에 아래 전체)

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import { selfEnrollDevice } from "./sessionApi";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("selfEnrollDevice", () => {
  it("POSTs to self-enroll with bearer and returns the api_key", async () => {
    localStorage.setItem("yeson.setup.values", JSON.stringify({ serverWsBase: "ws://127.0.0.1:8000" }));
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ id: 1, name: "client-x", api_key: "KEY123" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const key = await selfEnrollDevice("op-token", "client-x");

    expect(key).toBe("KEY123");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/v1/devices/self-enroll");
    expect((init as RequestInit).method).toBe("POST");
    expect((init as RequestInit).headers).toMatchObject({ Authorization: "Bearer op-token" });
  });
});
```

> localStorage 키(`yeson.setup.values`)는 `setupValues.ts`의 `STORAGE_KEY` 실제 값으로 맞춘다(파일에서 확인).

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd /Users/usabatch/coding/yeson_dev/yeson_meet/apps/desktop && npx vitest run src/console/sessionApi.test.ts`
Expected: FAIL — `selfEnrollDevice` 미존재.

- [ ] **Step 3: 구현** — `apps/desktop/src/console/sessionApi.ts`의 `createSession` 아래에 추가

```typescript
export async function selfEnrollDevice(operatorToken: string, name: string): Promise<string> {
  const response = await timedFetch("Self-enroll device", `${apiBase()}/api/v1/devices/self-enroll`, {
    method: "POST",
    headers: authHeaders(operatorToken),
    body: JSON.stringify({ name }),
  });
  const body = await parseJsonResponse<{ id: number; name: string; api_key: string }>(response, "Self-enroll device");
  return body.api_key;
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd /Users/usabatch/coding/yeson_dev/yeson_meet/apps/desktop && npx vitest run src/console/sessionApi.test.ts`
Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add apps/desktop/src/console/sessionApi.ts apps/desktop/src/console/sessionApi.test.ts
git commit -m "feat(desktop): selfEnrollDevice API call"
```

---

## Task 8: 온보딩 UI 통합 (폼 축소 + 자동발견 + 자동 self-enroll)

**Files:**
- Modify: `apps/desktop/src/setup/MeetingQuickStartPanel.tsx`

**Interfaces:**
- Consumes: `resolveServerWsBase`/`probeLocalServer`/`discoverServer`(Task 6), `selfEnrollDevice`(Task 7), `loginOperator`(sessionApi), `saveCredentials`/`hydrateServerAddressFromKeychain`/`updateServerWsBase`(credentials), `loadValues`(setupValues).

- [ ] **Step 1: import 추가** — `apps/desktop/src/setup/MeetingQuickStartPanel.tsx` 상단

```tsx
import { discoverServer, probeLocalServer, resolveServerWsBase } from "./serverDiscovery";
import { loginOperator, selfEnrollDevice } from "../console/sessionApi";
```

> 기존에 `loginOperator`가 다른 파일에서 import되어 있으면 중복 import를 피하고 한 줄로 합친다.

- [ ] **Step 2: 폼 상태 축소 + 자동발견 상태 추가** — `form` 초기화(현재 4필드)를 2필드로 줄이고 주소 상태를 분리

```tsx
const [form, setForm] = useState(() => ({
  email: "admin@yeson.local",
  password: "",
}));
const [serverWsBase, setServerWsBase] = useState<string>(() => loadValues().serverWsBase);
const [discovering, setDiscovering] = useState(false);
```

- [ ] **Step 3: 자동발견 함수 + 마운트 시 실행**

```tsx
const findServer = useCallback(async () => {
  setDiscovering(true);
  try {
    const resolved = await resolveServerWsBase({
      probeLocal: probeLocalServer,
      discover: discoverServer,
    });
    if (resolved) setServerWsBase(resolved);
  } finally {
    setDiscovering(false);
  }
}, []);

useEffect(() => {
  if (!serverWsBase) void findServer();
}, [serverWsBase, findServer]);
```

> `useCallback`/`useEffect`가 import되어 있지 않으면 React import에 추가.

- [ ] **Step 4: 필드 교체** — 기존 4개 `QuickField`(서버주소/email/password/deviceApiKey)를 아래로 교체

```tsx
<div style={styles.discoveryRow}>
  <span style={styles.label}>서버 주소</span>
  <code style={styles.discoveryValue}>{serverWsBase || "찾는 중..."}</code>
  <button type="button" onClick={() => void findServer()} disabled={discovering}>
    {discovering ? "찾는 중..." : "다시 찾기"}
  </button>
</div>
<QuickField label="Operator email" value={form.email} type="email" onChange={(value) => setForm((c) => ({ ...c, email: value }))} />
<QuickField label="Operator password" value={form.password} type="password" onChange={(value) => setForm((c) => ({ ...c, password: value }))} /> {/* vibelign: allow-secret — field name only, not a key value */}
```

> mDNS가 막혀 `serverWsBase`가 빈 값이면, 사용자가 서버 콘솔의 "내 서버 주소"를 붙여넣을 수 있도록 `serverWsBase`를 입력 가능한 `QuickField`로 노출하는 폴백도 함께 둔다(값 비었을 때만 렌더).

- [ ] **Step 5: registerAndStart 자동 self-enroll로 교체**

```tsx
async function registerAndStart() {
  // 1) 주소 확정 + 저장(키체인). device key는 아직 비움 — self-enroll로 채운다.
  await saveCredentials({ serverWsBase, email: form.email, password: form.password, deviceApiKey: "" });
  await hydrateServerAddressFromKeychain();
  // 2) operator 로그인 → device key self-enroll → 키체인에 키만 갱신
  const { access_token: operatorToken } = await loginOperator(form.email, form.password);
  const deviceName = `client-${navigator.platform || "device"}`;
  const apiKey = await selfEnrollDevice(operatorToken, deviceName);
  await saveCredentials({ serverWsBase, email: form.email, password: form.password, deviceApiKey: apiKey });
  // 3) 기존 흐름
  await refreshMeta();
  setEditing(false);
  await lifecycle.startMeetingOneClick();
}
```

> `saveCredentials`는 전체 자격증명을 다시 쓰므로 device key 포함 1회 더 호출해 키를 영속화한다(키체인 권위). `apiBase()`가 방금 저장한 주소를 쓰도록 `hydrateServerAddressFromKeychain()`을 self-enroll 전에 호출한 상태를 유지한다.

- [ ] **Step 6: 스타일 키 추가** — `styles`에

```tsx
discoveryRow: { display: "flex", gap: 10, alignItems: "center", marginBottom: 10 },
discoveryValue: { fontWeight: 700, color: "#1e3a8a" },
```

- [ ] **Step 7: 타입체크 + 전체 테스트**

Run: `cd /Users/usabatch/coding/yeson_dev/yeson_meet/apps/desktop && npx tsc --noEmit && npx vitest run`
Expected: tsc 에러 0, 전체 PASS.

- [ ] **Step 8: 커밋**

```bash
git add apps/desktop/src/setup/MeetingQuickStartPanel.tsx
git commit -m "feat(desktop): zero-config onboarding — auto server address + auto device self-enroll"
```

---

## Task 9: 수동 E2E 검증 (실기기)

**Files:** 없음(검증만).

- [ ] **Step 1: 같은 PC 구성** — 서버 데스크톱 + 클라 데스크톱을 한 머신에서 실행. 클라 설정 화면에서 서버 주소가 `ws://127.0.0.1:8000`으로 **자동** 표시되는지, 이메일·비번만 넣고 "회의 시작" 시 device key 자동 발급 + 자막 송출되는지 확인.

- [ ] **Step 2: LAN 2대 구성** — 서버를 다른 PC에서 실행. 클라에서 "다시 찾기" 시 mDNS로 `ws://<서버IP>:8000` 자동 발견되는지 확인. 서버 콘솔에 "내 서버 주소" 배너가 맞는 IP를 보여주는지 확인.

- [ ] **Step 3: mDNS 차단 폴백** — (가능하면) mDNS가 막힌 망에서 자동발견 실패 시 주소 입력 폴백이 뜨고, 서버 콘솔 주소를 붙여넣어 동작하는지 확인.

- [ ] **Step 4: 결과를 메모리/플랜에 기록**

---

## Self-Review (작성자 점검 완료)

- **스펙 커버리지**: 자기 IP 감지(T2)·mDNS 광고(T4)·콘솔 주소 표시(T3)·클라 localhost 프로브+mDNS 발견(T5·T6)·device key self-enroll(T1·T7)·폼 축소(T8)·3구성 E2E(T9) — 스펙 §5 컴포넌트 전부 매핑됨.
- **Placeholder**: 각 코드 스텝에 실제 코드 포함. Rust mdns-sd API는 버전별 시그니처 차이를 빌드로 확정하라는 명시적 노트 포함(가짜 코드 아님, 실제 호출).
- **타입 일관성**: `DiscoveredServer{ip,port}`(Rust serde camelCase)↔TS `DiscoveredServer{ip:string;port:number}` 일치. `selfEnrollDevice→api_key:string`, 서버 `DeviceCreateOut.api_key` 일치. `resolveServerWsBase` 시그니처가 T6 정의와 T8 호출에서 동일.
- **보안 불변식**: self-enroll은 `require_operator`(admin 분리), device key는 QR 미사용, 키체인 권위·localStorage 캐시 유지.
