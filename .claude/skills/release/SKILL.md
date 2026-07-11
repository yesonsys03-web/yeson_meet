---
name: release
description: Use when cutting a yeson-meet release (vX.Y.Z) — bumping the app version, writing release notes, running the 4 installer CI workflows, adding Intel macOS assets, or verifying release assets and latest-*.json update manifests before declaring the release done.
---

# yeson-meet 릴리스 파이프라인

## 개요

릴리스 태그의 단일 출처는 두 `tauri.conf.json`의 `.version`이다(4개 워크플로 공통 규칙).
릴리스 커밋이 **4개 워크플로 파일 전부**의 릴리스 노트(`body:`)를 수정하고, 그 수정이 곧 push 트리거라서 PR 머지 한 번에 4개가 전부 자동 빌드된다(v1.2.0~v1.2.3 전부 push 트리거 실측). Intel 자산은 이 Intel Mac에서 로컬 스크립트로 채운다. **자산 20개 + 매니페스트 3플랫폼 확인 전에는 완료 선언 금지.**

## 순서

1. **선행 확인**: 로컬 main이 origin/main 최신이고 릴리스 대상 변경 전부 머지됨(`git log`로 확인).
   `apps/server` 소스가 바뀐 릴리스라면 재동결(`apps/server_desktop/scripts/build-server.sh`) 후 E2E가 이미 끝났는지 확인 — 동결 안 된 번들은 새 라우트가 404/405.
2. **릴리스 브랜치** `release/vX.Y.Z` 생성. main 직접 push 금지 — **차단 장치는 없음(정책)**, 실수로 push하면 그대로 워크플로가 발화하므로 반드시 브랜치+PR.
3. **버전 범프 — 반드시 2곳 모두** (두 앱은 항상 같은 버전으로 함께 릴리스 — 한쪽만 변경돼도 둘 다 범프, v1.2.3 클라가 그 사례):
   - `apps/desktop/src-tauri/tauri.conf.json` → `.version`
   - `apps/server_desktop/src-tauri/tauri.conf.json` → `.version`
4. **릴리스 노트 = 워크플로 4개 전부의 `body:` 갱신**: `windows-desktop.yml`, `server-desktop-windows.yml`, `macos-desktop.yml`, `server-desktop-macos.yml` 모두 새 버전 내용으로 수정.
   각 워크플로의 push paths 필터가 자기 파일만 매치하므로, 이 수정이 곧 4개 전부의 자동 빌드 트리거다. macOS 쪽을 빼먹으면 ①macOS 빌드가 안 돌고 ②나중에 실행될 때 **옛 버전 노트로 릴리스 본문을 덮어쓴다**(softprops가 body를 교체, append 아님).
5. **PR 생성 → 머지**: 머지 push로 4개 워크플로가 각 1회씩 자동 실행되고 릴리스(vX.Y.Z)에 자산을 올린다.
6. **4개 런 성공 확인**: `gh run list --limit 8` — macOS 2개도 머지 push로 자동 실행된다(macOS 러너는 10x 과금이라 불필요한 재실행 금지). macOS body를 빼먹어 안 돌았다면 dispatch가 아니라 **body를 고치는 후속 PR 머지**로 트리거할 것 — 릴리스가 이미 존재하는 상태의 dispatch는 옛 노트로 본문을 덮어쓸 수 있다.
7. **Intel 자산(이 Intel Mac에서만)**: CI 릴리스가 존재하게 된 뒤
   `scripts/release-intel-macos.sh vX.Y.Z`
   → Intel dmg 2종 + `_x64` 업데이터 아티팩트 업로드 + `latest-*.json`에 darwin-x86_64 병합.
   전제: `~/.tauri/yeson_meet_updater.key` 존재, `gh` 로그인, x86_64 Mac, `jq`·`python3`.
8. **검증 → 완료 선언**:
   `gh release view vX.Y.Z --json assets --jq '.assets[].name'`
   - 자산 **20개** = 매니페스트 2(`latest-client.json`/`latest-server.json`) + 앱별 9파일 × 2앱. CI 4개만 끝난 시점엔 14개, Intel 단계(7) 후 20개.
     앱별 9파일: `aarch64.dmg`, `x64.dmg`, `x64_en-US.msi`, `x64-setup.exe`+`.sig`, `app.tar.gz`+`.sig`, `x64.app.tar.gz`+`.sig` (설치본 4 + 업데이터 아티팩트 5)
   - 두 매니페스트에 3플랫폼이 모두 있는지 확인 (`darwin-aarch64`·`darwin-x86_64`·`windows-x86_64`):
     `for f in latest-client.json latest-server.json; do gh release download vX.Y.Z -p "$f" -O - | jq -c '.platforms | keys'; done`
     매니페스트는 CI 워크플로들이 생성·병합하고, Intel 스크립트가 마지막에 `darwin-x86_64`를 병합한다.
   - 릴리스 페이지 본문이 **새 버전의** "무엇이 바뀌었나"인지 확인 — 옛 노트면 4단계에서 어느 워크플로 body를 빼먹은 것.

## mac 콘솔(server_desktop) 릴리스 — apple-live-translate 스테이징 (필수 선행, arm64 전용)

`apple-translate-*` 리소스 글롭은 더 이상 모든 macOS 빌드에 자동 병합되지
않는다. 파일이 `tauri.macos.conf.json`(자동 로드)이 아니라 커스텀 이름
`tauri.macos-arm.conf.json`(비자동, 명시적 `--config`로만 적용)로 옮겨졌기
때문 — 인텔 맥 빌드가 이 글롭의 영향을 받지 않도록 하기 위한 조치
(2026-07-11, `feature/apple-on-device-translate` 브랜치 회귀 수정). 아키텍처별
빌드 경로가 갈린다:

- **arm64 맥 릴리스**: **tauri build 전에** 반드시
  `apps/native_helper_mac/scripts/build_apple_translate.sh`로
  apple-live-translate를 release 빌드해 번들 위치
  (`apps/server_desktop/src-tauri/binaries/apple-translate-aarch64-apple-darwin/`)에
  스테이징한 뒤, `pnpm tauri:build:mac-arm`(= `tauri build --config
  src-tauri/tauri.macos-arm.conf.json`)로 빌드한다. 이 config의
  `bundle.resources` 글롭 `binaries/apple-translate-*/**/*`가 파일을 번들에
  포함시킨다. 스테이징을 빼먹으면 프로바이더가 바이너리를 못 찾아
  **count-only로 조용히 강등**된다(라이브 자막 번역이 사라짐).
- **인텔 맥 릴리스**: apple 리소스 글롭이 없는 기본 `tauri.conf.json`(3개
  글롭)만 적용되므로 그냥 `tauri build`(플레인, `--config` 없이)로 빌드한다.
  apple-live-translate 바이너리는 애초에 arm64 전용(macOS 26 SDK 필요)이라
  존재할 수 없고, 런타임에서 `locate_bundled_apple_translate`가 못 찾으면
  provider가 자연스럽게 비활성화된다(gracefully unavailable) — 별도 조치
  불필요.

**CI 러너 제약 (미확인)**: apple-live-translate는 macOS 26 SDK(Xcode 26)로만
빌드된다. GitHub Actions macOS 러너의 Xcode가 macOS 26 SDK를 지원하는지 아직
미확인이다. 미지원이면 CI에서 이 스텝이 실패하므로 다음 중 하나를 **의식적으로**
결정해야 한다:
- (a) 이 실리콘맥에서 로컬 빌드한 아티팩트를 CI 잡에 업로드해 스테이징, 또는
- (b) 바이너리 없이 릴리스 — 프로바이더는 count-only로 강등되며 라이브 번역
  기능이 빠진 릴리스임을 릴리스 노트에 명시.

**글롭 미스매치 동작 (실험 검증 — fail-loud, `--config tauri.macos-arm.conf.json`
경로에 한함)**: 2026-07-11 실측 — `apple-translate-aarch64-apple-darwin/`를
치우고 `pnpm tauri:build:mac-arm`(당시엔 자동 병합되던
`tauri.macos.conf.json` 기준, 지금은 명시적 `--config
src-tauri/tauri.macos-arm.conf.json`)을 돌리면 Tauri v2가 빌드를
**명시적으로 실패**시킨다: `glob pattern binaries/apple-translate-*/**/* path
not found or didn't match any files` → `failed to build app`. 즉 arm 스테이징을
빼먹은 arm64 릴리스는 tauri build 단계에서 눈에 띄게 실패하므로(silent-skip
아님) 바이너리 없이 조용히 배포될 위험은 없다. 단, CI Xcode가 macOS 26 SDK를
지원하지 않아 빌드 자체가 아예 안 되는 경우는 위 (a)/(b) 결정으로 별도
처리해야 한다.

같은 날 재검증: 같은 디렉터리를 치운 채로 `--config` 없이 플레인 `tauri
build --debug --bundles app`(= 인텔 경로, 기본 `tauri.conf.json`의 3개
글롭만 적용)을 돌리면 apple 글롭이 아예 없으므로 **정상적으로 번들링까지
성공**한다(업데이터 서명 키 미설정으로 인한 마지막 서명 단계 실패는 별개
사안). 인텔 맥에서 apple-translate 바이너리 없이도 `tauri build`가 통과함을
직접 확인.

## 함정 (전부 실사고)

| 함정 | 현실 |
|---|---|
| 릴리스 후 main HEAD에서 워크플로 새 dispatch (4개 모두 해당) | 릴리스 자산·노트를 미릴리스 상태로 **덮어씀** — v1.2.2에서 매니페스트/설치본 서명 불일치로 자동업데이트 파손. 복구는 dispatch가 아니라 **실패한 런의 Re-run**(같은 릴리스 커밋 SHA라 안전) |
| 버전 범프 누락(한쪽만 올림 포함) | 이전 태그 릴리스에 자산이 덮어써짐 |
| 서버 번들 재동결 누락 | 새 라우트 404 (QR viewer-url 실사고) — 릴리스 전에 재동결+E2E |
| Intel dmg를 일반 `tauri build --bundles dmg`로 시도 | Kaspersky가 bundle_dmg.sh를 "Resource busy"로 실패시킴 — 스크립트가 쓰는 makehybrid 경로 유지 |
| `~/.tauri/yeson_meet_updater.key` 분실 | 자동 업데이트 체인 영구 단절 — CI 시크릿과 동일 키 |
| 릴리스를 prerelease로 전환 | 워크플로 기본이 full release(어디에도 prerelease 플래그 없음). 수동으로 prerelease로 바꾸면 자동 업데이트 대상에서 빠짐 |
