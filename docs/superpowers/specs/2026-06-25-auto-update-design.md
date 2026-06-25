# 자동 업데이트 설계 (yeson server console + operator client)

- 날짜: 2026-06-25
- 대상: `apps/server_desktop` (서버 콘솔), `apps/desktop` (operator 클라이언트)
- 상태: 승인됨 (브레인스토밍 A·B 양쪽 승인)

## 목표

두 Tauri v2 앱이 **시작 시 1회** 새 버전을 확인하고, 있으면 **상단 배너로 알림**한 뒤
사용자가 **[지금 업데이트]를 누르면 그 자리에서 다운로드·설치·재시작**한다. 강제/사일런트
설치는 하지 않는다(회의 중 갑작스런 재시작 방지).

## 비목표 (YAGNI)

- 완전 사일런트 자동 설치, 주기적 폴링, 다중 채널(stable/beta), 롤백 UI — 하지 않음.
- Apple 공증 / Windows Authenticode 코드서명 — 보류 유지(별개 트랙). updater의 minisign
  서명은 이와 무관하게 동작한다.
- macOS x86_64 / universal — 릴리스는 aarch64 전용이므로 다루지 않음.

## 핵심 사실 (조사 결과)

- 리포 `yesonsys03-web/yeson_meet` 는 **PUBLIC** → GitHub Releases의 정적 매니페스트를
  인증 없이 읽을 수 있다.
- 현재 릴리스 CI 4종(`macos-desktop.yml`, `windows-desktop.yml`,
  `server-desktop-macos.yml`, `server-desktop-windows.yml`)은 `pnpm tauri build` 후
  `softprops/action-gh-release@v2`로 **버전 태그(예: `v0.9.6`) prerelease**에 업로드한다.
  태그는 워크플로에 하드코딩(수동 릴리스 케이던스).
- 두 앱 모두 `src-tauri/src/lib.rs`의 `tauri::Builder::default()`에서 플러그인을 등록하고,
  권한은 `src-tauri/capabilities/default.json`에 둔다. updater 플러그인은 아직 없다.
- 앱 버전 권위는 각 `tauri.conf.json`의 `version`(현재 `0.9.6`). updater는 이 값과
  매니페스트의 `version`을 비교한다.

## 아키텍처 개요

```
앱 시작
  └─ updater.ts: check()  ──HTTP──▶  GitHub release "updater-latest"
                                       /latest-server.json  (또는 latest-client.json)
       │  매니페스트.version > 앱.version ?
       ▼ yes
  UpdateBanner: "새 버전 vX.Y.Z 사용 가능 [지금 업데이트] [나중에]"
       │ 사용자가 [지금 업데이트] 클릭
       ▼
  downloadAndInstall() (진행률 콜백 → 배너 "다운로드 중 45%")
       ▼
  relaunch()  → 새 버전으로 재시작
```

매니페스트(`latest-*.json`) 내부의 아티팩트 URL은 **버전 태그 릴리스**(`v0.9.7` 등)에
업로드된 서명 번들을 가리킨다. 안정적 엔드포인트(`updater-latest`)에는 **매니페스트만**
이동시킨다. 따라서 사람용 prerelease 흐름은 그대로 두고 updater만 별도 안정 URL을 쓴다.

## 컴포넌트

### 1. 프런트엔드 — 업데이트 확인/설치 (앱당 신규 2파일)

`apps/<app>/src/.../updater.ts`
- 책임: updater 상태머신. 외부 의존(`check`)을 **주입 가능**하게 설계해 단위 테스트한다.
- 상태: `idle | checking | available | downloading(progress%) | installing | uptodate | error`.
- 공개 API(개념):
  - `checkForUpdate(check?) → {available, version, notes} | null`
  - `installUpdate(update, onProgress) → Promise<void>` (다운로드+설치 후 `relaunch()`)
- Tauri 런타임이 아니면(브라우저/테스트) `check()`가 throw → catch 후 `idle`/숨김 처리.
  (기존 `getVersion()` 가드 패턴과 동일.)

`apps/<app>/src/.../UpdateBanner.tsx`
- 책임: updater 상태를 받아 배너 렌더. `available`일 때 버전·[지금 업데이트]·[나중에],
  `downloading`일 때 진행률, `error`일 때 짧은 실패 문구. `[나중에]`는 세션 동안 dismiss.
- 의존: updater 상태/액션(props). 스타일 토큰은 각 앱의 기존 컨벤션(`var(--ys-*)`).

배선:
- 서버 콘솔: `ServerConsole.tsx` 헤더의 `TunnelDegradedBanner` 인접 위치에 `<UpdateBanner/>`.
- 클라이언트: `DesktopConsole.tsx`의 `NativeCaptureBanner` 위치에 `<UpdateBanner/>`.

### 2. Tauri 플러그인 등록 (앱당)

- `src-tauri/Cargo.toml`: `tauri-plugin-updater = "2"`, `tauri-plugin-process = "2"` 추가.
- `src-tauri/src/lib.rs`: `.plugin(tauri_plugin_updater::Builder::new().build())`,
  `.plugin(tauri_plugin_process::init())` 등록.
- `src-tauri/capabilities/default.json`: `"updater:default"`, `"process:allow-restart"` 권한 추가.
- `package.json`: `@tauri-apps/plugin-updater`, `@tauri-apps/plugin-process` 추가.

### 3. tauri.conf.json (앱당)

```jsonc
{
  "bundle": { "createUpdaterArtifacts": true },
  "plugins": {
    "updater": {
      "pubkey": "<minisign 공개키 — 커밋>",
      "endpoints": [
        "https://github.com/yesonsys03-web/yeson_meet/releases/download/updater-latest/latest-server.json"
      ]
    }
  }
}
```
- 서버 콘솔 → `latest-server.json`, 클라이언트 → `latest-client.json`.

### 4. 서명키 (minisign, 두 앱 공용)

- 생성: `pnpm tauri signer generate -w <tmp>` → 개인키(암호화)+공개키.
- 공개키: 두 `tauri.conf.json`의 `plugins.updater.pubkey`에 커밋.
- 개인키+암호: **커밋 금지**. GitHub Secrets에 보관.
- **사용자 액션**: 리포 Secrets에 `TAURI_SIGNING_PRIVATE_KEY`,
  `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` 2개 추가(값은 구현 단계에서 전달).

### 5. CI 변경 (기존 4 워크플로)

각 `tauri build` 스텝에:
- env 주입: `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` (secrets).
- 산출물: macOS `*.app.tar.gz` + `.sig`, Windows NSIS `*-setup.exe` + `.sig`.
- 이 서명 번들 + `.sig`를 기존 버전 태그 prerelease에 함께 업로드.

### 6. 매니페스트 발행 — 로컬 스크립트 (권장)

`scripts/publish-updater-manifest.mjs`
- 실행: 메인테이너가 4개 빌드가 버전 릴리스에 업로드된 뒤 1회.
  `node scripts/publish-updater-manifest.mjs <version>` (예: `0.9.7`).
- 동작:
  1. `gh release view v<version>` 로 업로드된 `*.app.tar.gz`, `*-setup.exe`, `*.sig` 수집.
  2. 앱별로 `latest-server.json` / `latest-client.json` 조립:
     ```json
     {
       "version": "0.9.7",
       "notes": "...",
       "pub_date": "2026-…Z",
       "platforms": {
         "darwin-aarch64":  { "signature": "<sig>", "url": "<release asset url>" },
         "windows-x86_64":  { "signature": "<sig>", "url": "<release asset url>" }
       }
     }
     ```
     `url`은 버전 태그 릴리스의 자산을 가리킨다.
  3. `gh release upload updater-latest latest-*.json --clobber` 로 이동형 태그에 푸시.
- 근거: mac/win이 서로 다른 러너라 한 곳에서 양 플랫폼 서명을 모아야 함. 수동 릴리스
  케이던스에 맞춰 스크립트가 크로스 워크플로 조율보다 단순. 후에 CI 잡으로 승격 가능.

### 7. 1회 셋업

- `updater-latest` GitHub 릴리스/태그 생성(매니페스트 보관용, prerelease 무관 안정 URL).

## 데이터 흐름 / 플랫폼 키

- 매니페스트 플랫폼 키: `darwin-aarch64`, `windows-x86_64` (릴리스 타깃과 일치).
- 버전 비교: updater 플러그인이 매니페스트 `version` vs 앱 `tauri.conf.json version`.

## 에러 처리

- `check()` 실패(네트워크/비-Tauri): 조용히 무시, 배너 미표시(앱 동작 영향 없음).
- 다운로드/설치 실패: 배너에 짧은 실패 문구 + 재시도 가능. 앱은 계속 동작.
- 매니페스트 누락/파싱 실패: "최신"으로 간주, 배너 미표시.

## 미서명 앱 주의 (실기기 검증 항목)

- macOS(aarch64, 미공증): updater 설치분은 브라우저 다운로드가 아니라 quarantine 플래그가
  안 붙는 게 일반적 → "우클릭 열기" 재승인 없이 갈 가능성 높음. **실기기 검증 필요**.
- Windows(NSIS 미서명): SmartScreen 경고 가능. **실기기 검증 필요**.

## 테스트 전략

- 단위(vitest): `updater.ts` 상태머신 — `check`/`install`을 주입한 더블로 available/none/
  error/progress 전이를 검증. 배너 렌더 분기(있음/진행률/에러)도 가능하면 커버.
- 수동 E2E(실기기): vX 설치 → vX+1 빌드·업로드·매니페스트 발행 → vX 실행 시 배너 등장 →
  [지금 업데이트] → 설치·재시작 후 vX+1 확인. mac/win 각각.

## 변경 파일 요약

앱당(×2):
- `src-tauri/Cargo.toml`, `src-tauri/src/lib.rs`, `src-tauri/capabilities/default.json`,
  `src-tauri/tauri.conf.json`, `package.json`
- 신규 `src/.../updater.ts`, `src/.../UpdateBanner.tsx`, 그리고 `updater.ts` 테스트
- `ServerConsole.tsx` / `DesktopConsole.tsx` 배너 배선

리포 레벨:
- 워크플로 4종에 서명 env + 업데이트 번들 업로드
- 신규 `scripts/publish-updater-manifest.mjs`
- 두 `tauri.conf.json`에 공개키 커밋
- GitHub Secrets 2개(사용자), `updater-latest` 릴리스 1회 생성

## 오픈 이슈 / 후속

- 실기기에서 미서명 updater 동작(quarantine/SmartScreen) 확정.
- 추후 매니페스트 발행을 CI 잡으로 승격할지 여부.
