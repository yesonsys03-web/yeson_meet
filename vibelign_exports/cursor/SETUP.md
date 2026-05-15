# VibeLign 작업 흐름

## AI 작업 전 (순서대로 실행)

**1단계 — 상태 확인**
```
vib doctor --strict
```
문제가 있으면 먼저 해결하세요.

**2단계 — 안전 구역 설정**
```
vib anchor
```
앵커가 없는 파일에 앵커를 자동으로 삽입합니다.

**3단계 — 현재 상태 저장 (세이브 포인트)**
```
vib checkpoint "작업 설명"
```
예: `vib checkpoint "로그인 기능 추가 전"`

**4단계 — 패치 요청 준비**
```
vib patch "원하는 변경사항"
```
AI에게 전달할 안전한 프롬프트가 VIBELIGN_PATCH_REQUEST.md에 생성됩니다.

---

## AI 작업 후 (반드시 확인)

**5단계 — 변경사항 확인**
```
vib explain --write-report
```

**6단계 — 위험도 체크**
```
vib guard --strict
```

**문제 없으면** → 저장
```
vib checkpoint "완료: 작업 설명"
```

**문제 있으면** → 되돌리기
```
vib undo
```
