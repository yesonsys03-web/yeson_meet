---
name: release
description: Use when cutting a yeson-meet release (vX.Y.Z) — bumping the app version, writing release notes, running the 4 installer CI workflows, adding Intel macOS assets, or verifying release assets and latest-*.json update manifests before declaring the release done.
---

# yeson-meet 릴리스 파이프라인

## 개요

릴리스 태그의 단일 출처는 두 `tauri.conf.json`의 `.version`이다(4개 워크플로 공통 규칙).
릴리스 PR을 main에 머지하면 Windows 2개 워크플로가 자동 빌드하고, macOS 2개는 수동 dispatch, Intel 자산은 이 Intel Mac에서 로컬 스크립트로 채운다. **자산 20개 + 매니페스트 3플랫폼 확인 전에는 완료 선언 금지.**

## 순서

1. **선행 확인**: main 클린·릴리스 대상 변경 전부 머지됨(`git log`로 확인).
   `apps/server` 소스가 바뀐 릴리스라면 재동결(`apps/server_desktop/scripts/build-server.sh`) 후 E2E가 이미 끝났는지 확인 — 동결 안 된 번들은 새 라우트가 404/405.
2. **릴리스 브랜치** `release/vX.Y.Z` 생성. main 직접 push는 가드가 차단.
3. **버전 범프 — 반드시 2곳 모두** (두 앱은 항상 같은 버전으로 함께 릴리스 — 한쪽만 변경돼도 둘 다 범프, v1.2.3 클라가 그 사례):
   - `apps/desktop/src-tauri/tauri.conf.json` → `.version`
   - `apps/server_desktop/src-tauri/tauri.conf.json` → `.version`
4. **릴리스 노트 = 워크플로 본문 수정**: `.github/workflows/windows-desktop.yml`과 `.github/workflows/server-desktop-windows.yml`의 `body:` 릴리스 노트를 새 버전 내용으로 갱신.
   이 수정이 곧 자동 빌드 트리거다(push paths 필터가 워크플로 파일 자신만 매치). 안 고치면 머지해도 빌드가 안 돈다.
5. **PR 생성 → 머지**: 머지 즉시 Windows 클라+서버 2개가 정확히 1회씩 자동 실행되고 릴리스(vX.Y.Z)를 생성한다.
6. **macOS 2개 수동 dispatch**(10x 과금이라 자동 아님):
   `gh workflow run macos-desktop.yml` + `gh workflow run server-desktop-macos.yml`
7. **Intel 자산(이 Intel Mac에서만)**: CI 릴리스가 존재하게 된 뒤
   `scripts/release-intel-macos.sh vX.Y.Z`
   → Intel dmg 2종 + `_x64` 업데이터 아티팩트 업로드 + `latest-*.json`에 darwin-x86_64 병합.
   전제: `~/.tauri/yeson_meet_updater.key` 존재, `gh` 로그인, x86_64 Mac.
8. **검증 → 완료 선언**:
   `gh release view vX.Y.Z --json assets --jq '.assets[].name'`
   - 자산 **20개** = 매니페스트 2(`latest-client.json`/`latest-server.json`) + 앱별 9파일 × 2앱.
     앱별 9파일: `aarch64.dmg`, `x64.dmg`, `x64_en-US.msi`, `x64-setup.exe`+`.sig`, `app.tar.gz`+`.sig`, `x64.app.tar.gz`+`.sig` (설치본 4 + 업데이터 아티팩트 5)
   - 두 매니페스트에 `darwin-aarch64`·`darwin-x86_64`·`windows-x86_64` 플랫폼이 모두 있는지 확인.
     매니페스트는 CI 워크플로들이 생성·병합하고, Intel 스크립트가 마지막에 `darwin-x86_64`를 병합한다.

## 함정 (전부 실사고)

| 함정 | 현실 |
|---|---|
| 릴리스 후 main HEAD에서 Windows 워크플로 새 dispatch | 릴리스 자산을 미릴리스 빌드로 **덮어씀** — v1.2.2에서 매니페스트/설치본 서명 불일치로 자동업데이트 파손. 단, **빌드 실패 복구는 안전**: Actions의 "Re-run"은 같은 릴리스 커밋 SHA로 재실행되므로 실패한 런의 Re-run으로 복구할 것 |
| 버전 범프 누락(한쪽만 올림 포함) | 이전 태그 릴리스에 자산이 덮어써짐 |
| 서버 번들 재동결 누락 | 새 라우트 404 (QR viewer-url 실사고) — 릴리스 전에 재동결+E2E |
| Intel dmg를 일반 `tauri build --bundles dmg`로 시도 | Kaspersky가 bundle_dmg.sh를 "Resource busy"로 실패시킴 — 스크립트가 쓰는 makehybrid 경로 유지 |
| `~/.tauri/yeson_meet_updater.key` 분실 | 자동 업데이트 체인 영구 단절 — CI 시크릿과 동일 키 |
| 릴리스를 prerelease로 전환 | 워크플로 기본이 full release(어디에도 prerelease 플래그 없음). 수동으로 prerelease로 바꾸면 자동 업데이트 대상에서 빠짐 |
