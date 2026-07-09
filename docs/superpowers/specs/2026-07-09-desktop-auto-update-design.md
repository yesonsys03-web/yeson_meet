# 데스크탑 자동 업데이트 설계 (2026-07-09)

## 목적

클라이언트 앱(`apps/desktop`)과 서버 콘솔 앱(`apps/server_desktop`) **둘 다**에 cmux(Sparkle)식 자동 업데이트를 넣는다: 최초 설치 이후에는 앱이 백그라운드로 새 버전을 받아두고 재시작 시 적용 — 수동 재설치 제거.

## 확정된 요구사항

| 항목 | 결정 |
|---|---|
| 대상 | 클라이언트 + 서버 콘솔 둘 다 (같은 Tauri v2 구조라 셋업 공유) |
| UX | cmux식: 백그라운드 확인·다운로드 → "재시작하여 적용" 배너. 강제 팝업 없음 |
| 배포 채널 | GitHub Releases (repo `yesonsys03-web/yeson_meet`는 PUBLIC — 무인증 다운로드 가능) |
| 릴리스 표시 | **prerelease → 정식 릴리스로 전환** (`releases/latest/download/…` 매니페스트 조회가 정식 릴리스만 인식) |

## 현황 (탐사로 확정)

- 두 앱 모두 **Tauri v2** (`apps/desktop/src-tauri/Cargo.toml:17`) → 적용 메커니즘은 `tauri-plugin-updater`(crate) + `@tauri-apps/plugin-updater`(JS). 현재 **미설치·미설정**
- `tauri.conf.json`에 updater 블록·`createUpdaterArtifacts` 없음. 클라이언트 버전 단일 소스는 `tauri.conf.json:4`
- CI 4개 워크플로(`macos-desktop.yml`, `windows-desktop.yml`, `server-desktop-macos.yml`, `server-desktop-windows.yml`) 모두 `softprops/action-gh-release@v2` 사용 — `latest.json` 자동 생성 없음, 서명 시크릿 참조 없음
- 업로드 자산: mac=dmg만, win=msi만 (windows conf targets는 `["nsis","msi"]`)
- 코드서명: 양 플랫폼 미서명(기존 보류 결정 유지). 업데이터의 ed25519 서명은 코드서명과 **무관**하며 별도로 필수
- 다운로드 스택 reqwest(rustls)+tokio 이미 존재 — 단 플러그인 사용이 정석이므로 참고만

## 설계 (승인안: tauri-plugin-updater + GitHub Releases)

### 1회 셋업 — 서명 키

- `tauri signer generate`로 업데이터 전용 ed25519 키쌍 생성 (Apple 공증·Windows 인증서 무관)
- 공개키 → 두 앱 `tauri.conf.json`의 `plugins.updater.pubkey`
- 개인키+비밀번호 → GitHub Actions 시크릿 `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`
- **개인키 분실 = 업데이트 체인 단절.** 발급 즉시 키체인(또는 안전 저장소)에 백업하는 절차를 릴리스 문서에 기록

### 앱 설정 (두 앱 공통)

- `bundle.createUpdaterArtifacts: true`
  - macOS 산출물: `.app.tar.gz` + `.sig`
  - Windows 산출물: NSIS `.exe` + `.sig` (업데이트 설치는 사일런트 모드)
- `plugins.updater.endpoints`:
  - 클라이언트: `https://github.com/yesonsys03-web/yeson_meet/releases/latest/download/latest-client.json`
  - 서버 콘솔: `…/latest-server.json`
- capabilities에 updater 권한 추가 (`apps/desktop/src-tauri/capabilities/default.json` — 현재 updater 권한 없음)

### 매니페스트 — 앱별 2개 파일

앱이 둘이므로 `latest.json` 하나로 못 쓴다. 같은 릴리스에 `latest-client.json`, `latest-server.json`을 첨부하고 각 파일에 플랫폼별(`darwin-aarch64`, `darwin-x86_64`, `windows-x86_64`) URL+서명을 담는다.

### CI 변경 (워크플로 4개 공통 패턴)

1. 빌드 스텝에 서명 시크릿 env 주입 (이때부터 `.sig` 생성됨)
2. 업데이터 아티팩트(`.app.tar.gz`/NSIS `.exe` + `.sig`)를 릴리스에 추가 업로드
3. **매니페스트 병합 스텝**: 릴리스에서 기존 `latest-<app>.json`을 내려받아 자기 플랫폼 항목만 갱신 후 `gh release upload --clobber`로 재업로드 — Mac/Win 워크플로가 서로 다른 시점에 돌아도 안전 (동시 실행은 수동 dispatch 운용상 드묾, 알려진 잔여 리스크로 기록)
4. `prerelease: true` 제거 (정식 릴리스로 발행)

### 앱 내 UX

- 시작 시 + 4시간 간격 백그라운드 `check()` → 있으면 조용히 다운로드
- 완료 시 하단 네비(버전 표시 옆) 배너: **"vX.Y.Z 준비됨 — 재시작하여 적용"** → 클릭 시 install + relaunch
- 설정에 "지금 업데이트 확인" 수동 버튼
- 실패(네트워크·서명 불일치)는 앱 사용을 절대 막지 않음: 로그만 남기고 다음 확인 주기에 재시도

### macOS 미서명 앱 주의

업데이트로 바이너리 cdhash가 바뀌면 화면기록 권한(TCC)이 풀릴 수 있음(기존 유령 권한 이슈와 동일 뿌리). 클라이언트 앱의 기존 화면기록 권한 배너가 재감지하므로 별도 로직은 불필요하고, 업데이트 배너에 "Mac은 업데이트 후 화면기록 권한 재확인이 필요할 수 있습니다" 문구만 추가한다.

## 에러·롤백

- 다운로드/서명 검증 실패 → 해당 주기 포기, 로그, 다음 주기 재시도. 사용자에게는 수동 확인 버튼 경로만 노출
- 롤백: 이전 버전 설치 파일이 GitHub Releases에 남아 있으므로 수동 재설치가 폴백. 자동 롤백은 범위 밖

## 테스트

1. **로컬 리허설**: 로컬 http 서버에 가짜 `latest-client.json`을 두고 endpoint를 임시 변경, 0.0.1→0.0.2 업데이트 전 과정(감지→다운로드→배너→재시작 적용) 확인 (이 Mac). 업데이터는 설치된 앱에서만 동작(dev 모드 불가)에 유의
2. **실전 검증(릴리스 2회에 걸침)**: N번째 릴리스에 업데이터 탑재 → N+1번째 릴리스가 Windows 실기기·Mac 실기기에서 자동 도착·적용되는지 확인. 릴리스 체크리스트에 항목 추가
3. Mac에서 업데이트 후 화면기록 권한 상태 확인 항목 추가

## 범위 밖

- 코드서명/공증 (기존 보류 결정 유지)
- 델타 업데이트, 강제 자동 재시작, 나이틀리/베타 채널 분리, 자동 롤백
