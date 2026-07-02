# 실시간 번역자막 지연 개선 조사 (2026-07-01)

> 현행 Gemini Live 기반 자막(~10초 지연)을 더 빠르게 만들 수 있는지에 대한 딥리서치 +
> 이 서버(Intel i9-9900K, GPU 없음)에서의 로컬 실측 결과를 종합한 의사결정 문서.
> 계기: 제작부 피드백 "정확도는 구글미트보다 좋으나 반응이 늦다, 품질보다 속도 우선" + Tiro(tiro.ooo) 비교.

## 1. 질문과 제약
- **질문**: 발화→번역자막 지연을 현행 ~10초에서 Tiro급(sub-second~수초)으로 낮출 수 있나?
- **제약**: 
  - 서버 **GPU 없음(CPU only)** — 결정적 제약
  - **다국어** 필요(EN→KO 중심이나 확장 필요)
  - 현재 **자가호스팅(LAN) + 단일 API 키** 단순성 선호
  - **회의실 공유화면 실시간 자막** 용도(개인 노트가 아님)

## 2. 왜 현행이 ~10초인가
Gemini Live(`gemini-3.1-flash-live-preview`)는 **턴 단위** 모델이라, 발화를 ~10초 세그먼트로 묶어 그 구간이 끝나야 전사+번역을 냅니다(`server_process.rs`가 `GEMINI_SEGMENT_MAX_SPEECH_MS=10000` 주입). 품질은 좋지만 구조적으로 저지연이 안 됨. 세그먼트를 6초로 줄여봤으나(2026-07-01) 자막이 너무 자주 바뀌고 미완결↑ 부작용으로 원복함.

## 3. 딥리서치 결론 (소스 27개·주장 128개 추출→25개 적대검증·24 확정)

### 핵심
- **sub-second 번역자막은 클라우드 스트리밍 API로만 가능.** 자가호스팅 오픈소스는 CPU에서 실시간 불가.
- 현행 Gemini도 **이미 오디오를 클라우드로 전송**하므로, 클라우드 스트리밍 API로 바꿔도 **개인정보 프로파일은 동일** — 얻는 건 지연↓, 바뀌는 건 벤더·과금 방식.

### 후보별 (검증됨)
| 후보 | 지연 | 다국어/한국어 | CPU 자가호스팅 | 비고 |
|---|---|---|---|---|
| **Google STT 스트리밍 + Translate v3** | 첫토큰 ~1.4초, 끝 ~3.2초 | 광범위+KO | ❌(클라우드) | **우리 코드에 `google_stt_translate.py`로 이미 구현됨(미사용)** |
| **Speechmatics** 통합 실시간 | 실시간 | KO + 동시 5개(69쌍) | ❌ | STT+번역 단일 API |
| **OpenAI gpt-realtime-translate** | 연속 스트리밍 delta | 광범위 | ❌ | 전용 엔드포인트, 화자 발화 중 delta 방출 |
| **Deepgram Nova-3** | STT 200~500ms | STT광범위(+별도 MT) | ❌ | STT 지연 최저, 번역 별도 조립 |
| Meta SeamlessStreaming / M4T v2 | ~3초+ | ~100개 언어(KO) | **불가(GPU)** | 다국어 최강이나 GPU 전제 |
| whisper-streaming / WhisperLive | ~3.3초(GPU 측정) | 광범위(+MT) | **CPU는 제한적**(§4 참고) | LocalAgreement-2 |
| NVIDIA Riva | 저지연 | STT+NMT | **불가(GPU)** | 온프렘이나 GPU 서버 필요 |
| **현행 Gemini Live** | ~10초 | 광범위 | ❌(클라우드) | 품질 최상 |

### 품질·안정성 트레이드오프
- 스트리밍 번역은 비스트리밍 대비 **~1~2 BLEU 손실**(~3초 지연 기준). sub-second로 갈수록 품질·안정성 더 하락 → **Gemini Live 턴 품질과 동급 아님**.
- **flicker(자막 다시 써짐)는 본질적이나 튜닝 가능**: LocalAgreement-2(두 번 연속 일치 접두만 확정, 확정분 불변)·minimal-revision 재번역으로 20%+ 감소.

## 4. 로컬 실측 — CPU 캐스케이드 (이 서버: i9-9900K, 16스레드, GPU 없음)

격리 venv에서 `faster-whisper` + `ufal/whisper_streaming`(LocalAgreement-2)로 53초 영어 오디오 실시간 시뮬.

### 배치 처리 능력 (참고)
| 모델 | 배치 전사 | 실시간 배수 |
|---|---|---|
| base int8 | 53.2초→3.0초 | **17.8×** |
| small int8 | 53.2초→6.4초 | **8.4×** |

### 실제 스트리밍 지연 (확정 자막, 결정적)
| | **base int8** | **small int8** |
|---|---|---|
| 확정 지연(중앙) | **2.93초** ✅ | **7.56초** ❌ |
| p90 / 최대 | 5.2s / 15.7s | **82.9s / 82.9s** 💥 |
| 판정 | 따라잡음·안정 | **폭주(backlog 무한증가, 최종 64초 밀림)** |
| "Toon Boom" 전사 | "tune boom"(오류) | "tune boom"(여전히 오류) |
| flicker | **없음**(LocalAgreement 확정분 불변) | — |

### 실측에서 얻은 핵심 인사이트
1. **배치 RTF가 높아도(small 8.4×) 스트리밍이 따라잡는다는 보장 없음.** whisper-streaming은 미확정 버퍼를 반복 재전사 → small은 확정 지연→버퍼 증가→재전사 지연의 악순환으로 **폭주(80초+)**. 리서치가 경고한 함정을 실측 확인.
2. **이 CPU에서 스트리밍 가능한 건 base(또는 tiny)뿐.** 품질 올리려 큰 모델 쓰면 폭주 → **base 전사 품질에 갇힘**.
3. **base·small 둘 다 "Toon Boom" 오전사** → CPU-Whisper는 전문용어에 약하고 큰 모델로 개선 불가(GPU 필요). 용어사전 후보정이나 GPU가 있어야 함.
4. **지연 ~3초는 CPU가 느려서가 아니라 LocalAgreement 알고리즘 특성** — GPU 논문값(3.3초)과 동일. CPU는 병목 아님(base는 따라잡음).

## 5. 최종 결론: 3가지 현실 경로

| 경로 | 지연 | 품질 | 데이터 | 비용 | flicker | 노력 |
|---|---|---|---|---|---|---|
| **A. 현행 Gemini Live** | ~10초 | ★★★ | 클라우드 | Gemini API | 없음 | 0 |
| **B. 클라우드 스트리밍**(v3/Speechmatics/OpenAI) | **~1~3초** | ★★ | 클라우드(현행과 동일) | 시간·문자당 | 튜닝 가능 | 중(v3는 낮음) |
| **C. CPU 자가호스팅**(whisper base + Opus-MT) | **~3~4초**(실측) | ★ base 한정 | **온프렘(유출 0)** | 무과금 | **없음** | 큼 |

### 추천 우선순위
1. **B의 첫 수 = `google_stt_translate.py` 활성화 후 A/B 실측.** 이미 구현돼 노력 최소, 지연 ~1~3초, 데이터 프로파일 현행과 동일. GCP 서비스계정만 필요. → 가장 쉽게 "확실히 빨라지는" 길.
2. **다국어 정식 채택이면 Speechmatics 통합 API**(한국어+5개 동시, 단일 연동).
3. **C(CPU 온프렘)는 "데이터 절대 유출 금지"가 최우선일 때만.** 실측상 ~3~4초·flicker0 가능하나 품질(base)·구축노력·강한 서버 필요가 발목.
4. **현행 유지(A)** — 품질 최우선 + 지연 감내 가능하면.

## 6. 미확정 / 커밋 전 확인 필요
- **실제 가격**(딥리서치에서 검증 실패): 주 몇 시간이면 대략 월 $5~25 추정, 실확인 필수.
- 클라우드 MT가 **애니메이션 용어사전**을 유지하는지(provider custom dictionary로 대체 가능한지) — pencil→펜슬 등 품질 회귀 방지.
- 단일 API 키 단순성 유지 가능한지(Speechmatics/Deepgram+MT는 계정·키 여러 개일 수 있음).
- CPU 캐스케이드를 실제 채택 시 약한 턴키 PC(4코어급)에서의 동작(이 실측은 강한 i9 기준).

## 7. 출처 (딥리서치 검증 소스)
- OpenAI Realtime translation: https://developers.openai.com/api/docs/guides/realtime-translation
- Deepgram streaming latency: https://developers.deepgram.com/docs/measuring-streaming-latency
- Speechmatics real-time translation: https://docs.speechmatics.com/features-other/translation
- whisper-streaming(지연·LocalAgreement): https://arxiv.org/html/2307.14743 , https://github.com/ufal/whisper_streaming
- Seamless: https://github.com/facebookresearch/seamless_communication
- faster-whisper(CPU 벤치): https://github.com/SYSTRAN/faster-whisper
- 캐스케이드 스트리밍 ST(지연): https://arxiv.org/html/2508.13358v1 , https://arxiv.org/abs/2407.11010
- 재번역 vs 스트리밍(flicker): https://www.researchgate.net/publication/343302734

---

## 부록 (2026-07-02): "라이브 파셜 줄" UI 아이디어 실측 기각

**아이디어**: 뷰어에 페이서 우회 라이브 줄을 추가해, 이미 서버가 보내는 fast partial을
발화 중(3~4초 지연)에 표시 → Gemini 유지한 채 체감 5초대 진입.

**실측 결과(server-2026-07-01.log, final 있는 세그먼트 446개)**:
- 파셜 없는 세그먼트 0% — 커버리지는 완벽하나
- **첫 partial publish → final publish 간격: 중앙값 0초, p90 0초, 최대 4초** (1초 초과는 3%)

**원인**: `gemini-3.1-flash-live-preview`는 input_transcription을 발화 중 스트리밍하지 않고
**세그먼트당 1회 배치로 방출**(gemini_live.py의 empty-tail cycle 주석이 관측한 그 동작).
따라서 파셜 번역 전체가 세그먼트 종료 후에 돌고, 파셜은 final보다 ~1초 먼저 도착할 뿐.

**결론**: 클라이언트 표시 계층으로는 지연을 줄일 수 없음(이득 ~1초). §5의 경로 구도 그대로 —
5초대 진입은 **B(Google STT 스트리밍 + Translate v3 A/B)가 유일한 현실 경로**임을 재확인.
전환 준비 상태: provider·키체인(GOOGLE_APPLICATION_CREDENTIALS_JSON 등)·시작요청 provider
선택까지 서버 콘솔 플럼빙 완비. 남은 것 = ①GCP 서비스계정 발급(사용자) ②용어사전(glossary.py는
Gemini 프롬프트 주입이라 Google 경로 미적용 → Translate v3 glossary로 대체 필요) ③가격 실확인.

## 부록 2 (2026-07-02): `gemini-3.5-live-translate-preview` 실측 — 새 최유력 후보

**배경**: "모델을 3.5로 바꾸면?" 검토 중 발견. 3.5 Flash는 Live 변형이 없으나, **음성 동시통역 전용
Live 모델 `gemini-3.5-live-translate-preview`가 공개 프리뷰**로 나옴(Google Meet에도 탑재 중).
같은 Live API·같은 GEMINI_API_KEY. `translationConfig{target_language_code:"ko"}` + 입·출력
전사 활성화로 **발화 도중 한국어 자막 텍스트가 연속 스트리밍**됨. 참고로 3.1의 전사 배치 방출은
공식 포럼에서도 확인된 회귀(2.5 시절엔 증분 스트리밍이었음).

**로컬 실측(이 Mac, 격리 venv + google-genai 2.10.0, 34초 합성 회의 음성 실시간 스트림)**:
- 한국어 자막 청크가 **발화 시점 대비 ~1.5~3초** 뒤로 연속 도착(EN 전사 ~1~2초, KO는 +0.3~1초).
- 첫 자막 ~3.9초(연결 0.4초 포함). 세그먼트 캡 개념 자체가 없어 10초 대기 구조가 사라짐.
- 프로브: scratchpad/probe_live_translate.py (세션 격리 스크래치, 필요시 재작성 쉬움)

**트레이드오프(실측·문서 확인)**:
- **프롬프트/용어사전 불가**("pure translation; no instructions") — 실측에서도 cleanup→"청소",
  pencil test→"연필 테스트"로 용어 회귀 확인. glossary.py 이식 불가 → KO 출력 후처리 치환 필요.
- 오역 관찰: "cut 35"→"35개의 컷"(샷 번호를 수량으로). 품질은 실회의 A/B 필수.
- 연속 스트림이라 **final/utterance 경계가 없음** → seq 단위 조립(문장 경계 휴리스틱)을 새로
  구현해야 회의록·보고서·페이서가 동작. 신규 provider 모듈 + SDK ≥2.10 업그레이드 필요.
- 가격: 오디오 in $0.0053/min + out $0.0315/min ≈ **$0.037/min(~$2.2/시간)** — 현행 3.1 구성
  (~$0.023/min)의 ~1.6배. 프리뷰 모델(스펙 변동 리스크).

**갱신된 결론**: 5초대 진입의 첫 수는 **B′ = live-translate A/B**(계정 추가 불필요, 지연 최단)로
승격. Google STT 경로(B)는 품질/용어 이슈 시의 대안으로 유지.

**구현 완료(2026-07-02 저녁, 미커밋)**: `apps/server/ai/gemini_live_translate.py`(연속 전사→seq 조립,
KO 후처리 보정) + sidecar 등록 + 콘솔 provider 드롭다운 + SDK 2.10 + frozen 재동결·부팅 검증.
실 API E2E: 파셜 제자리 성장 + 문장 단위 final, 체감 ~1.5~3초. 남은 것=실회의 A/B(콘솔에서
provider=gemini_live_translate 선택→서버 재시작).

---
*작성: 2026-07-01. 로컬 실측은 이 서버(i9-9900K, CPU only)에서 격리 venv로 수행. 가격·언어지원은 빠르게 변하므로 채택 전 재확인.*
