# AGENTS.md

This file is automatically read by OpenCode, Claude Code, and other AI coding tools that support AGENTS.md.

**Before making any changes to this project, read and follow `AI_DEV_SYSTEM_SINGLE_FILE.md`.**

## Core Rules

- Apply the smallest safe patch possible
- Do not rewrite entire files unless explicitly requested
- Edit only the file that is actually relevant
- Do not modify unrelated modules
- Respect anchor boundaries (`ANCHOR: NAME_START` / `ANCHOR: NAME_END`)
- Keep entry files (main.py, index.js, etc.) small and focused
- For a distinct new feature, prefer creating a new file/module/component instead of appending it to an existing file

## Module boundaries and file size

- Keep code that changes together cohesive (same module or directory when sensible).
- Prefer discoverable paths and names; split when responsibility diverges or tests need isolation.
- Respect project lint and `watch_rules` limits on file length; prefer new files over bloating an existing one.
- For very large UI or Python modules, use sub-anchors until split; keep patch targets small.
- When a file starts accumulating a second responsibility, keep the old file as wiring and move the new behavior out.

## Two Modification Modes

### Mode 1 — Normal AI edit (default)

When the user's request does NOT contain "바이브라인" or "vibelign", modify directly using your own judgment.
**Do NOT call any VibeLign MCP tools in this mode.**

```
User: "로그인 버튼 색 파란색으로 바꿔줘"
→ Modify directly. Follow Core Rules above.
```

### Mode 2 — VibeLign safe mode (keyword trigger ONLY)

**Triggered strictly when the user's message contains "바이브라인" or "vibelign" (case-insensitive).**
Do NOT switch to this mode based on your own judgment — keyword is required.

1. Call `patch_get` with the user's request — translates it to CodeSpeak and pinpoints exact `target_file` and `target_anchor`.
2. Modify **only** within the returned `target_anchor` boundary in `target_file`.
3. Call `guard_check` to validate.
4. Call `checkpoint_create` to save the state.

```
User: "바이브라인으로 로그인 버튼 색 파란색으로 바꿔줘"
→ patch_get("로그인 버튼 색 파란색으로 바꿔줘")
→ Modify only target_file at target_anchor
→ guard_check → checkpoint_create

User: "vibelign change login button color to blue"
→ Same VibeLign workflow
```

### Without MCP (CLI fallback)

```bash
vib doctor --strict
vib anchor
vib patch "<your request>"
# apply the AI edit
vib explain --write-report
vib guard --strict --write-report
```

## Project Map

Before modifying any file, read `.vibelign/project_map.json` to understand:
- File categories (entry, ui, service, core)
- Anchor locations per file (`anchor_index`)
- File dependencies via `.vibelign/anchor_meta.json` (`@CONNECTS`)

## Checkpoint Restore (Undo) via MCP

When the user asks to undo or restore a previous state (e.g. "바이브라인 언두해줘", "이전으로 돌려줘", "undo"):

**NEVER run `vib undo` from the shell** — it uses interactive `input()` and will hang in MCP context.

**Always use this flow instead:**

1. Call `checkpoint_list` — show the list to the user
2. Ask the user which checkpoint to restore
3. Call `checkpoint_restore(checkpoint_id=<selected_id>)`

```
User: "바이브라인 언두해줘"
→ checkpoint_list() → show list to user
→ "몇 번으로 복원할까요?"
→ User picks one
→ checkpoint_restore(checkpoint_id="...")
```

## Full Rules

See `AI_DEV_SYSTEM_SINGLE_FILE.md` for the complete ruleset.

<!-- VibeLign Handoff Instruction -->
## AI 전환 / Session Handoff

새 채팅을 열거나 다른 AI 툴로 이동했을 때:

1. `PROJECT_CONTEXT.md` 파일을 가장 먼저 읽으세요.
2. 파일 맨 위의 `## Session Handoff` 블록을 확인하세요.
3. `State references`에 `.vibelign/work_memory.json`이 있으면 함께 읽어 전체 작업 기록을 확인하세요.
4. `Next action` 항목에 적힌 작업부터 시작하세요.

> 이 지시는 `vib transfer --handoff` 실행 시 자동으로 추가됩니다.
