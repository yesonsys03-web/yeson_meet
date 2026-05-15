<!-- VibeLign Rules (vib export claude) -->
# VibeLign 규칙 (Claude Code용)

> 전체 규칙은 프로젝트 루트의 `AI_DEV_SYSTEM_SINGLE_FILE.md`를 읽으세요.

> **반드시 맨 먼저**: 어떤 코드 탐색/수정 도구(Read·Grep·Glob)를 호출하기 전에 `.vibelign/project_map.json` 을 먼저 Read 하세요. 파일 구조·앵커 위치·카테고리를 머리에 올린 뒤 작업 시작. (규칙 8번의 운용 강제)

## 핵심 원칙

1. **가능한 가장 작은 패치를 적용하세요**
2. **요청한 파일만 수정하세요** — 연관 없는 파일은 절대 건드리지 마세요
3. **파일 전체를 재작성하지 마세요** — 명시적 요청이 없는 한 금지
4. **앵커 경계를 지키세요** — `ANCHOR: NAME_START` ~ `ANCHOR: NAME_END` 사이만 수정
5. **진입 파일을 작게 유지하세요** — main.py, index.js 등에 비즈니스 로직을 넣지 마세요
6. **새 파일을 임의로 생성하지 마세요** — 명시적 요청이 있을 때만 생성
7. **임포트 구조를 바꾸지 마세요** — 명시적 허락 없이 변경 금지
8. **코드맵을 먼저 읽으세요** — `.vibelign/project_map.json`에서 파일 구조와 앵커 위치를 확인
9. **응집도** — 같이 바뀌는 코드는 한 모듈·디렉터리에 두고, 책임이 갈라지면 분리
10. **이름·경로** — 기능을 경로·이름만으로 찾을 수 있게
11. **큰 파일** — 무한 확장보다 새 파일·모듈; ESLint `max-lines`·`watch_rules` 한도 준수; 거대 UI는 구역 앵커 후 분할; `vib patch`는 작은 `target_anchor`가 안전

## 작업 흐름

```
vib doctor --strict        # 상태 확인
vib anchor                 # 안전 구역 설정
vib checkpoint "설명"      # 현재 상태 저장
# AI 작업 수행
vib guard --strict         # 결과 검증
vib checkpoint "완료"      # 또는 vib undo
```

