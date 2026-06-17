# DEPLOY — 사내 서버 운영 가이드

> 최종 갱신: 2026-05-14  
> 대상: Ubuntu Server 24.04 LTS, 단일 노드, 사내망 내부.

---

## 1. 하드웨어 권장

| 항목 | 최소 | 권장 | 비고 |
|---|---|---|---|
| CPU | 4코어 | 8코어 | x86_64 |
| RAM | 8GB | 16GB | PostgreSQL + Caddy + FastAPI 동시 |
| 저장공간 | 256GB SSD | 1TB NVMe | 오디오/리포트 누적 |
| 네트워크 | 유선 1Gbps | 동일 | 사내망 고정 IP |
| 백업 | 사내 NAS / 외장 HDD | 동일 | nightly rsync |

**저전력 권장 미니PC**: Beelink, Minisforum, Intel NUC 등. **₩50~80만 원대**에서 충분.

---

## 2. 초기 셋업

### 2.1 OS / 기본 패키지
```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install ca-certificates curl gnupg ufw fail2ban htop git
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
```

### 2.2 Docker / Compose
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# 로그아웃 후 다시 로그인
```

### 2.3 고정 IP + HTTPS 인증서 (도메인 없음 운영)
- **고정 IP** 사용. 사내 공유기에서 MAC 기반 IP 예약 또는 정적 IP 할당. 예: `192.168.1.50`.
- **DNS 등록 불필요** — 클라이언트는 IP로 직접 접속.
- **인증서**: Caddy `tls internal` — 서버 시작 시 자체 CA + IP SAN 인증서 자동 발급.
- **Root CA 신뢰 등록**: 회의실 PC + 참석자 폰/노트북 모두에 Caddy root CA(`.crt`) 1회 설치 필요. (시스템 부서가 SETUP_SERVER 가이드에서 추출 → 배포)

### 2.4 디렉토리 준비
```bash
sudo mkdir -p /opt/yeson-meet
sudo chown $USER:$USER /opt/yeson-meet
cd /opt/yeson-meet
git clone <사내 git URL> .
cp deploy/env.example .env
# .env 편집: JWT_SECRET, DB_PASSWORD, GEMINI_API_KEY 등
```

---

## 3. Docker Compose 구성

### 3.1 `deploy/docker-compose.yml` (개요)
```yaml
services:
  postgres:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_DB: yeson_meet
      POSTGRES_USER: yeson_meet
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
      - ./deploy/postgres/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U yeson_meet"]

  server:
    build: ./apps/server
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql+asyncpg://yeson_meet:${DB_PASSWORD}@postgres/yeson_meet
      JWT_SECRET: ${JWT_SECRET}
      ACCESS_MODE: lan
      VIEWER_BASE_URL: https://${SERVER_HOST}
      STORAGE_PATH: /storage
    volumes:
      - ./data/storage:/storage
    depends_on:
      postgres:
        condition: service_healthy

  web:
    build: ./apps/web
    restart: unless-stopped
    # Vite 빌드 결과를 Caddy가 직접 서빙하도록 정적 볼륨 공유

  caddy:
    image: caddy:2
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./deploy/Caddyfile:/etc/caddy/Caddyfile:ro
      - ./data/caddy:/data
      - ./data/web-dist:/srv/web:ro
    depends_on:
      - server
```

### 3.2 `deploy/Caddyfile`
```caddy
{$SERVER_HOST}:443 {
    tls internal      # 자체 CA + IP SAN 인증서 자동 발급

    handle_path /api/* {
        reverse_proxy server:8000
    }
    handle_path /ws/* {
        reverse_proxy server:8000
    }
    handle {
        root * /srv/web
        try_files {path} /index.html
        file_server
    }

    encode gzip zstd
    log {
        output file /data/access.log
    }
}
```

### 3.3 `deploy/env.example`
```env
SERVER_HOST=<SERVER_IP>             # 운영자가 설정. 사내 고정 IP (예: 192.168.x.x)
DB_PASSWORD=replace-me-strong-password  # vibelign: allow-secret
JWT_SECRET=replace-me-32-byte-random  # vibelign: allow-secret
GEMINI_API_KEY=replace-me  # vibelign: allow-secret
ACCESS_MODE=lan
```

---

## 4. 마이그레이션 / 시드

```bash
# 컨테이너 안에서 Alembic 마이그레이션
docker compose run --rm server alembic upgrade head

# 초기 어드민 사용자 생성
docker compose run --rm server python -m apps.server.scripts.create_admin \
    --email admin@corp.local --name "관리자"
# → 임시 비밀번호 콘솔 출력 → 첫 로그인 후 변경 강제
```

---

## 5. 회의실 PC 셋업

### 5.1 Windows — MVP-α 1순위
1. **Voicemeeter Banana 설치**: https://vb-audio.com
2. 시스템 기본 출력 = Voicemeeter Input
3. Voicemeeter A1 = 실제 스피커, B1 = 가상 출력
4. **yeson-meet 데스크톱 앱 MSI 설치 → 로그인 → 기기 등록**
5. 입력 장치 = Voicemeeter Output

### 5.2 Mac — 2순위
1. **BlackHole 설치**: https://github.com/ExistentialAudio/BlackHole
2. **Audio MIDI 설정**:
   - 멀티 출력 장치 생성 → 실제 스피커 + BlackHole 체크
   - 시스템 출력을 멀티 출력 장치로
3. **Google Meet 출력**: 시스템 출력 또는 BlackHole 직접 선택
4. **yeson-meet 데스크톱 앱 DMG 설치 → 로그인 → 기기 등록**
5. 입력 장치 = BlackHole

### 5.3 기기 등록 / Device API Key
- 관리자 UI에서 새 Device 생성 → 1회성 **Device API Key** 발급 (서버 인증용)
- 회의실 PC에 자동 입력 (앱 첫 실행 시 키 입력 페이지)
- 키는 OS keychain에 저장. 평문 파일 X.

> ⚠️ **회의실 PC에는 `GEMINI_API_KEY`를 두지 않는다.**  
> Gemini Live 호출은 사내 서버가 단독으로 책임. 회의실 PC는 Device API Key만 보유. 키 관리·노출 위험·사용량 추적 모두 서버 1곳에서.

---

## 6. 운영 — 일상 작업

### 6.1 시작 / 정지 / 재시작
```bash
docker compose up -d        # 시작
docker compose down         # 정지
docker compose restart server  # 단일 서비스 재시작
docker compose logs -f server  # 로그 추적
```

### 6.2 상태 확인
```bash
docker compose ps
docker compose exec postgres pg_isready -U yeson_meet
curl -sk https://${SERVER_HOST}/api/v1/health
```

### 6.3 백업 (nightly cron)
```bash
# /etc/cron.daily/yeson-meet-backup
#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR=/mnt/nas/yeson-meet/$(date +%Y-%m-%d)
mkdir -p "$BACKUP_DIR"

# DB dump
docker compose -f /opt/yeson-meet/deploy/docker-compose.yml exec -T postgres \
    pg_dump -U yeson_meet yeson_meet | gzip > "$BACKUP_DIR/db.sql.gz"

# Storage (오디오/리포트)
rsync -a --delete /opt/yeson-meet/data/storage/ "$BACKUP_DIR/storage/"

# 30일 이상 된 백업 삭제
find /mnt/nas/yeson-meet -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;
```

### 6.4 보관 기간 만료 정리
```bash
# 매일 새벽 회의 90일 초과 음성·리포트 삭제 (DB는 보존, 파일만 삭제)
docker compose exec server python -m apps.server.scripts.retention_cleanup --days 90
```

---

## 7. 업데이트 절차

```bash
cd /opt/yeson-meet
git fetch && git checkout v0.x.y
docker compose pull             # 변경된 이미지가 있으면
docker compose build            # 자체 빌드 이미지
docker compose run --rm server alembic upgrade head
docker compose up -d
```

**다운타임 최소화**: PostgreSQL은 거의 재시작 불필요. server 컨테이너만 1~3초 다운.  
**롤백**: `git checkout v(previous)` + 마이그레이션이 역방향 가능한지 확인 후 `alembic downgrade`.

---

## 8. 모니터링 / 알림 (MVP는 최소)

### 최소
- Caddy 액세스 로그 → `data/caddy/access.log`
- `docker compose logs server > journal` cron
- 외부 헬스 ping: 사내 다른 PC에서 `curl -k https://<SERVER_IP>/api/v1/health` 분당 1회

### 확장 (MVP-β-7)
- Prometheus exporter (FastAPI middleware)
- Grafana 대시보드: 활성 세션 수, viewer 동시 접속, AI 지연, 큐 길이
- 알림: 사내 SDK 또는 단순 webhook → Slack 미사용 시 사내 채팅으로

---

## 9. 보안 체크리스트

- [ ] `JWT_SECRET`, `DB_PASSWORD`, `GEMINI_API_KEY`는 32바이트 이상 랜덤
- [ ] `.env`는 git에 절대 커밋 X (`.gitignore`에 `.env`, `.env.*`, `!.env.example` 패턴 포함 확인)
- [ ] HTTPS 강제 (Caddy automatic redirect)
- [ ] PostgreSQL 외부 노출 X (Docker network 내부만)
- [ ] SSH key-only 로그인, password 로그인 비활성화
- [ ] `ufw` 80/443/SSH만 허용
- [ ] `fail2ban` SSH 보호
- [ ] OS 자동 보안 업데이트: `sudo apt -y install unattended-upgrades`
- [ ] PostgreSQL 백업 암호화 (NAS 측에서)
- [ ] 보관 기간 만료 자동 삭제 cron 활성화
- [ ] 회의 오디오 보관 정책 법무·HR 승인 완료

---

## 10. 장애 대응 가이드

| 증상 | 점검 |
|---|---|
| 회의실 PC가 서버 못 찾음 | 서버 IP ping, Caddy 기동, 클라이언트의 Root CA 신뢰 등록 상태, 방화벽 |
| viewer가 접속 후 자막 안 옴 | `docker compose logs server` WebSocket 오류, 토큰 만료, sidecar 연결 상태 |
| 자막 지연 ≥5초 | Gemini Live 연결 확인, sidecar 큐 길이, 회의실 PC CPU |
| 서버 재시작 후 회의 데이터 없음 | 볼륨 마운트(`./data/postgres`, `./data/storage`) 확인 |
| 디스크 가득 참 | 오디오 보관 기간 단축, NAS 이전, 오래된 회의 삭제 |
| 토큰이 동작 안 함 (PIN은 β-3) | 시스템 시간(TZ, NTP), session.status, expires_at |

---

## 11. 운영 SOP — 회의 1회 흐름

```
운영자
1. 회의실 PC에 데스크톱 앱 띄움 (자동 시작 가능)
2. 로그인 (저장된 자격증명 또는 SSO)
3. "회의 시작" 클릭 → 제목·클라이언트 라벨·visibility 선택
4. QR을 회의실 모니터에 크게 표시
5. 참석자가 폰으로 QR 스캔 (PIN 입력은 β-3 추가 기능)
6. 회의 진행 ─ 운영자는 자막 검수, 필요 시 일시정지
7. 회의 종료 클릭 → MD 리포트 자동 생성
8. 리포트 다운로드, 필요 시 수동 공유

서버 (자동)
- 모든 데이터 PostgreSQL/Storage에 기록
- 90일 후 음성 파일 자동 삭제
- 매일 새벽 NAS로 백업
```

---

## 12. 외부 배포 시 변경점 (Phase 5+ 메모)

- `ACCESS_MODE=tunnel`로 변경
- Cloudflare Tunnel 또는 ngrok 설정
- HTTPS는 자동 (Cloudflare가 처리)
- viewer 토큰 + 사내 SSO 또는 매직링크 인증 추가
- Google OAuth verification 신청 (스코프별)
- macOS / Windows 코드사인 + 노타리제이션
- 회의 데이터 외부 노출 정책 재검토 (법무 필수)

### Quick Tunnel (핸드폰 셀룰러 접속) — 계정 불필요

회의실 와이파이가 서버에 닿지 않을 때, 핸드폰이 각자 셀룰러로 공개 URL을 열어 자막을 본다.
전제: 서버가 인터넷 egress 가능(이미 Gemini 호출로 충족).

1. 회의 시작 전, `deploy/` 에서: `./tunnel-quick.sh`
   - cloudflared(Quick Tunnel) 기동 → `https://<랜덤>.trycloudflare.com` 발급
   - 그 URL을 `deploy/.env`의 `VIEWER_BASE`에 기록 → server 재생성
   - 출력된 URL이 참가자 viewer base
2. 운영자 앱에서 회의 시작 → QR이 자동으로 `https://<랜덤>.trycloudflare.com/v/<token>` 을 담음
3. 참가자: 셀룰러 상태로 QR 스캔 (룸 와이파이 불필요)
4. 종료: `docker compose --profile tunnel down` (또는 cloudflared만 stop)

수동 폴백(스크립트 로그 파싱 실패 시): `docker compose logs cloudflared` 에서
`https://...trycloudflare.com` 복사 → `deploy/.env`의 `VIEWER_BASE=` 에 설정 →
`docker compose up -d server`.

보안: 인터넷 노출 시 게이트는 세션별 viewer 토큰(32B, 회의 종료 시 만료)뿐이다. QR은
회의실에서만 배포하고, trycloudflare URL은 터널이 떠 있는 동안만 유효하다. URL은 재시작마다
바뀐다(Quick Tunnel). 고정 도메인/SSO·PIN은 후속(Named Tunnel / 위 Phase 5+ 항목).
