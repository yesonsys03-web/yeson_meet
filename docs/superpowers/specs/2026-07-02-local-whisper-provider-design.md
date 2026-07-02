# 완전 무료 로컬 자막 provider (`local_whisper`) 설계

- 날짜: 2026-07-02
- 상태: 사용자 설계 승인 완료(접근안 1), 스펙 리뷰 대기
- 근거 실측: `docs/caption-latency-research-2026-07-01.md` §4 (faster-whisper base int8 + LocalAgreement-2, i9-9900K CPU에서 확정 지연 중앙값 2.93초·backlog 없음·flicker 0)

## 1. 목표와 비목표

**목표**
- 클라우드 호출 0, API 비용 0원인 자막 provider를 추가한다: 로컬 STT(faster-whisper) + 로컬 MT(CTranslate2 NMT), EN→KO 전용.
- 서버 콘솔 config의 provider 드롭다운에서 `local_whisper`로 선택 가능해야 한다.
- 기존 wire 프로토콜(`utterance.transcribed`, seq/partial/final)을 그대로 사용해 뷰어·페이서·회의록·보고서가 무수정으로 동작한다.

**비목표 (v1에서 하지 않음)**
- 다국어(EN→KO 외) 지원 — 다른 언어는 기존 Gemini provider가 담당.
- KO→EN 역방향.
- 약한 PC(4코어급)에서의 성능 보장 — 경고 로그만, 검증은 후속.
- whisper 모델 업그레이드(base 고정; small은 실측상 폭주).

## 2. 확정된 결정

| 항목 | 결정 | 근거 |
|---|---|---|
| STT | faster-whisper `base` int8, 16kHz | 실측상 CPU에서 따라잡는 유일한 실용 모델 |
| 스트리밍 확정 | LocalAgreement-2 자체 경량 구현 | ufal/whisper_streaming은 CLI·다중 백엔드 등 불필요 코드가 많아 핵심 알고리즘만 구현(~150줄) |
| MT | CTranslate2 + `Helsinki-NLP/opus-mt-tc-big-en-ko` int8 변환본(~240MB) | faster-whisper가 이미 CTranslate2 런타임 → 추가 무거운 의존성 0(sentencepiece만 추가) |
| MT 품질 escape hatch | `YESON_LOCAL_MT_MODEL_DIR` env로 모델 디렉터리 교체 가능(예: NLLB-200-distilled-600M int8) | v1은 Opus-MT, 품질 불만 시 교체 경로 확보 |
| 모델 배포 | 최초 provider 선택 시 자동 다운로드 → `{STORAGE_ROOT}/models/` 캐시 | 사용자 AFK로 권장안 채택(스펙 리뷰에서 뒤집기 가능). 서버 앱 크기 불변, 첫 1회만 인터넷 필요 |
| 용어사전 | `glossary.apply_ko_corrections()` 후처리 재활용 | gemini_live_translate와 동일 패턴(`gemini_live_translate.py:239`) |
| 언어 범위 | EN→KO 전용 | 사용자 확정 |

## 3. 아키텍처

### 3.1 신규 모듈 `apps/server/ai/local_whisper_translate.py`

`LocalWhisperTranslateProvider` — `STTProvider` Protocol(`apps/server/ai/providers.py:29`) 구현. 유일 공개 메서드:

```python
async def stream(self, audio: AsyncIterator[bytes], lang_hint: str) -> AsyncIterator[TranslatedUtterance]
```

내부 구성(테스트를 위해 순수 로직과 모델 호출을 분리):

- **PCM 변환**: s16le 640B 청크 → `np.frombuffer(chunk, dtype="<i2").astype(np.float32)/32768.0` (리샘플 불필요, 이미 16kHz mono — `apps/client_sidecar/audio/source.py` 규약).
- **StreamingTranscriber**: 오디오 누적 버퍼 + ~1초 주기로 faster-whisper 재전사(`asyncio.to_thread`, 블로킹 방지). `cpu_threads`를 제한(기본 4, `YESON_LOCAL_WHISPER_THREADS`)해 서버 프로세스 기아 방지. `vad_filter=True`로 무음 구간 재전사 낭비 억제.
- **LocalAgreementConfirmer** (순수 로직, 단위테스트 대상): 직전 전사와 이번 전사의 공통 접두(토큰 단위)만 확정. 확정 텍스트는 불변. 문장 경계(., ?, ! 등)에서 오디오 버퍼를 트림해 무한 재전사를 방지.
- **SentenceAssembler** (순수 로직): 확정 EN 텍스트를 seq 단위 utterance로 조립. 문장 완결 → final, 진행 중 → partial. 세그먼트당 seq=1 시작, `stream()` 재호출마다 `provider_segment` 증가 — 기존 `AISequenceNormalizer`(`apps/server/ws/sidecar.py:62`)가 전역 seq로 정규화.
- **LocalTranslator**: CTranslate2 `Translator` + sentencepiece 토크나이저. 호출도 `asyncio.to_thread`.

### 3.2 partial/final 방출 정책

- **final**: 문장 완결 시 문장 전체를 MT → `apply_ko_corrections()` → `TranslatedUtterance(is_final=True)`. final의 KO는 불변.
- **partial**: 확정 EN이 자랄 때마다 `text_en` 갱신 방출. `text_ko`는 확정 EN 프리픽스를 **최대 1회/초 스로틀**로 번역해 채움(프리픽스 재번역이므로 partial KO는 다시 써질 수 있음 — final에서 안정화). 방출 전 기존 choke point `_strip_caption_markup`(`sidecar.py:161`)를 그대로 통과.
- 스트림 종료(`flush`) 시 잔여 확정 텍스트를 final로 방출.

### 3.3 모델 관리 (`{STORAGE_ROOT}/models/`)

- **whisper**: faster-whisper 내장 다운로더에 `download_root={STORAGE_ROOT}/models/whisper` 지정 (HF `Systran/faster-whisper-base`, ~75MB).
- **MT**: 사전 변환(int8) Opus-MT CTranslate2 모델을 이 레포 GitHub Release 자산(별도 `models-*` 태그)으로 호스팅. provider 초기화 시 없으면 다운로드(+SHA256 검증) → `{STORAGE_ROOT}/models/mt-en-ko/`에 압축 해제. 변환은 개발 머신에서 1회(`ct2-transformers-converter`, 스크립트를 `scripts/`에 보관하되 런타임 의존성 아님).
- **실패 처리**: 다운로드 실패·모델 부재 시 `create_ai_provider()`가 `None` 반환(기존 S2 count-only 폴백 규약) + 원인과 재시도 방법을 서버 로그에 명시(콘솔 로그 패널에 노출됨).
- **env**: `YESON_LOCAL_MT_MODEL_DIR`(MT 모델 경로 오버라이드), `YESON_LOCAL_WHISPER_MODEL`(기본 `base`), `YESON_LOCAL_WHISPER_THREADS`(기본 4).

### 3.4 등록·콘솔 노출 (변경 지점 전체)

1. `apps/server/ws/sidecar.py:28-31` import + `create_ai_provider()`(`:120-136`)에 분기 추가 — 이름 `{"local_whisper", "local", "whisper_local"}`. API 키 게이트 없음.
2. `apps/server_desktop/src/setup/ServerConfigPanel.tsx:23` `PROVIDERS` 배열에 `"local_whisper"` 추가.
3. `apps/server/pyproject.toml` deps에 `faster-whisper`, `sentencepiece` 추가(ctranslate2·huggingface_hub는 faster-whisper 의존으로 포함).
4. `apps/server_desktop/scripts/build-server.sh` PyInstaller에 `--collect-all faster_whisper --collect-all ctranslate2` 추가. 모델 파일은 번들 제외(런타임 다운로드). 번들 증가분 ~40-60MB 예상.
5. Rust(`server_process.rs`) 변경 불필요 — provider 문자열은 기존 경로로 전달되고, 로컬 provider 전용 튜닝 env는 서버 기본값으로 충분(필요 시 `.env`로 조정 가능).

## 4. 오류 처리

- 모델 미다운로드/손상 → provider `None` → count-only 폴백 + 로그 안내(기존 규약, 자막만 안 나오고 회의는 진행).
- 전사 지연 폭주(약한 CPU) → 기존 lossy 오디오 큐(최근 ~30초, `live_session.py:19`)가 메모리 폭주를 차단. provider는 처리 랩(버퍼 나이)이 임계 초과 시 경고 로그 + 버퍼를 최근 구간으로 강제 트림(자막 공백 발생을 감수하고 실시간성 유지).
- MT 예외(문장 단위) → 해당 문장은 `text_ko=""`로 final 방출(EN이라도 표시) + 로그.

## 5. 테스트 전략

- **단위**: LocalAgreementConfirmer(접두 확정·불변성·문장 트림), SentenceAssembler(seq/partial/final 규약), glossary 적용, PCM 변환. whisper/MT는 fake 주입.
- **통합(로컬, 모델 실물)**: 53초 실측 오디오로 스트리밍 시뮬 → 확정 지연 ~3초·backlog 없음 재확인(7/1 실측 재현).
- **frozen 스모크**: `smoke-server-bundle.sh` 경로에 provider import 확인 추가, build-server.sh 재동결 후 부팅 검증.
- **E2E**: 이 Mac에서 콘솔 provider=local_whisper 선택 → 실회의 오디오 → 뷰어 자막 확인.

## 6. 리스크와 완화

| 리스크 | 완화 |
|---|---|
| `opus-mt-tc-big-en-ko` 모델 존재/품질 미실확인 | 구현 1단계에서 실확인. 부재·품질 미달 시 NLLB-200-distilled-600M int8로 즉시 대체(같은 CTranslate2 경로, 스펙 변경 불필요) |
| ctranslate2/faster-whisper PyInstaller 동결(특히 Windows) | `--collect-all` + 스모크 테스트. Windows 재동결 검증은 기존 릴리스 절차에 포함 |
| partial KO 다시 써짐(flicker) | 1회/초 스로틀 + final 불변 보장. 실사용 피드백 후 조정 |
| base 전사 품질(전문용어) | glossary KO 후처리 + `{STORAGE_ROOT}/glossary_ko.txt` 오버라이드로 현장 보정 |
| 약한 턴키 PC 성능 | v1 비목표. 경고 로그 + 강제 트림으로 폭주만 방지 |

## 7. 성공 기준

- 콘솔에서 `local_whisper` 선택 → 서버 재시작 → 실오디오에서 EN→KO 자막이 확정 지연 ~3-5초로 표시된다.
- 회의 진행 중 외부 네트워크 호출이 0건이다(모델 다운로드 완료 이후).
- 기존 provider(gemini_live 등)로 되돌려도 회귀가 없다.
- 회의록·보고서·지식저장고가 local_whisper 세션에서도 정상 생성된다.
