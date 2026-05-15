# VibeLign + Antigravity 작업 흐름

**1단계 — 상태 확인**
```
vib doctor --strict
```

**2단계 — 안전 구역 설정**
```
vib anchor
```

**3단계 — 현재 상태 저장**
```
vib checkpoint "작업 설명"
```

**4단계 — 작업 요청서 작성**
TASK_ARTIFACT.md를 채워서 AI에게 전달하세요.

**5단계 — 작업 완료 후 체크**
VERIFICATION_CHECKLIST.md 항목을 순서대로 확인하세요.

**6단계 — vib로 최종 검증**
```
vib explain --write-report
vib guard --strict
```

**문제 없으면** → `vib checkpoint "완료"`
**문제 있으면** → `vib undo`
