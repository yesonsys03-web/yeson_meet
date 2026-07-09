# 자동 업데이트 릴리스 체크리스트

> 근거: `docs/superpowers/plans/2026-07-09-desktop-auto-update.md` Task 8.
> 업데이터는 릴리스 2회에 걸쳐서만 실증된다. 앞으로의 릴리스에서 아래 항목을 확인할 것.

## 1회 셋업 (첫 업데이터 릴리스 전, 반드시)

- [ ] 업데이터 개인키 백업: `~/.tauri/yeson_meet_updater.key` 를 키체인 등 안전한 곳에 보관 (**분실 = 업데이트 체인 단절, 재발급 시 전 사용자 수동 재설치**)
- [ ] GitHub Actions 시크릿 등록:
  ```bash
  gh secret set TAURI_SIGNING_PRIVATE_KEY < ~/.tauri/yeson_meet_updater.key
  gh secret set TAURI_SIGNING_PRIVATE_KEY_PASSWORD --body ""
  ```
- [ ] 로컬 리허설(plan Task 8 Steps 1–7): localhost 매니페스트로 0.0.1→0.0.2 감지→다운로드→배너→재시작 적용 확인 (업데이터는 설치된 앱에서만 동작, `tauri dev` 불가)

## 릴리스 N (업데이터 첫 탑재)

- [ ] 수동 다운로드로 설치·배포 (이 릴리스 자체는 자동 업데이트 대상 아님)
- [ ] `latest-client.json` / `latest-server.json` 이 릴리스에 첨부되고 각각 `darwin-aarch64` + `windows-x86_64` 항목을 갖는지 확인
- [ ] 릴리스가 정식(prerelease 아님)으로 발행됐는지 확인 (`releases/latest` 조회 조건)

## 릴리스 N+1 (자동 업데이트 실증)

- [ ] N을 실행 중인 **Windows 실기기**에서 몇 분 내 "vN+1 준비됨 — 재시작하여 적용" 배너 표시 + 클릭 시 N+1로 재시작 확인
- [ ] N을 실행 중인 **Apple Silicon Mac**에서 동일 확인
- [ ] Mac 전용: N→N+1 자동 업데이트 후 화면기록 권한 상태 확인 (풀렸으면 앱의 기존 배너로 재부여)
- [ ] Intel Mac 사용자(darwin-x86_64)는 자동 업데이트 **대상 아님** — 로컬 빌드 Intel dmg 수동 재설치 안내

## 상시 항목

- [ ] 매니페스트 병합 스텝이 4개 워크플로 모두 성공했는지 확인 (경합 시 재시도 로그 확인; 3회 실패 시 워크플로가 빨갛게 실패함 — 해당 워크플로만 재실행하면 병합 복구됨)
- [ ] 오디오 수신 프로토콜 변경 시 사이드카·웹 캡처 이중 클라이언트 검증 (웹 캡처 가이드 참조)
