# AI_DEV_SYSTEM_SINGLE_FILE.md

## Purpose

This file defines the default AI development rules for this project.

Any AI coding tool working on this repository should follow these rules before making changes.

Examples:
- OpenCode
- Claude Code
- Cursor
- GPT-based coding workflows
- agent-based coding systems

The goal is simple:

**Use AI for speed, but preserve project structure, safety, and maintainability.**

---

## Core Rule

**Apply the smallest safe patch possible.**

Do not rewrite entire files unless the user explicitly asks for a rewrite.

### AI 응답 스타일

가능한 한 간결하게 응답하고, 불필요한 인사나 부연을 생략해.

---

## 1. Patch-First Editing Rules

- patch only
- prefer small changes over broad rewrites
- edit only the file that is actually relevant
- do not modify unrelated modules
- do not perform drive-by cleanup unless explicitly requested
- do not rename files unless required for the requested task
- do not move files unless required for the requested task
- do not refactor broadly unless the task explicitly asks for refactoring

### Required behavior
- preserve working code whenever possible
- keep diffs reviewable
- keep changes easy to explain
- prefer one focused change over many scattered changes
- for a distinct new feature, default to a new file/module/component instead of appending it to an existing file

---

## 2. Entry File Protection Rules

Entry files must stay small and focused.

Examples:
- `main.py`
- `index.js`
- `app.js`
- `main.ts`
- `Program.cs`

### Rules
- do not dump business logic into entry files
- keep entry files focused on bootstrapping / startup wiring
- move processing logic into dedicated modules
- move UI rendering logic out if the entry file is growing too large
- if an entry file is already too large, do not make it larger unless absolutely necessary

### Preferred pattern
- entry file → startup only
- service / worker / pipeline files → real logic
- UI file → UI only
- config file → configuration only
- when a file starts accumulating a second responsibility, keep the old file as wiring and move the new behavior out

### Module boundaries and file size (cohesion)

- Prefer keeping code that changes together in the same module or directory; split when responsibilities clearly diverge.
- Prefer paths and names that make a feature discoverable without opening many unrelated files.
- If a file grows large, prefer adding new behavior in a new file or module rather than expanding the same file indefinitely (project checks such as ESLint `max-lines` and `watch_rules` may flag this).
- Extract pieces that need isolated tests or reuse across features.
- Avoid over-splitting: if understanding one feature requires hopping across many tiny files with no clear story, prefer a slightly larger cohesive unit.

### Large pages or modules (e.g. major UI pages)

- Do not treat one outer anchor as the only safe zone for an entire very large file; prefer sub-anchors per major section until the file can be split.
- `vib patch` / CodeSpeak targeting works best when `target_anchor` spans a small, stable region.

---

## 3. Anchor Rules

Anchors define safe edit zones.

Example:

```python
# === ANCHOR: PIPELINE_WORKER_START ===
# code here
# === ANCHOR: PIPELINE_WORKER_END ===
```

### Rules
- respect anchor boundaries
- prefer editing inside anchors
- if the requested change clearly belongs to an anchor, stay inside that anchor
- do not rewrite the whole file if an anchor exists for the target area
- if a large file has no anchors, prefer adding anchors before repeated AI edits
- do not remove existing anchors unless explicitly requested

### If anchors exist
The AI should treat them as the preferred editing boundaries.

---

## 3-1. Project Map Rules

If `.vibelign/project_map.json` exists:

- Read it before making any changes to understand file roles and anchor locations
- Use the `anchor_index` field to find which anchors exist in each file
- Check `.vibelign/anchor_meta.json` for anchor intent and cross-file dependencies (`@CONNECTS`)
- Do not modify files outside the categories relevant to the request

### VibeLign patch-specific rules

- 복합 요청은 `intent / source / destination / behavior_constraint`로 먼저 분해한다.
- `삭제`와 `이동`이 같이 나오면 기능 삭제가 아니라 위치 이동 + 보존 제약인지 먼저 확인한다.
- `source`와 `destination`은 같은 규칙으로 취급하지 말고 역할별로 따로 해석한다.
- patch contract나 코드스픽 구조가 바뀌면 관련 테스트와 문서도 같이 갱신한다.
- 용어는 공통 문서와 glossary 기준으로 맞춘다.

---

## 4. Structure Safety Rules

Avoid the following patterns unless explicitly required:

- giant `main.py`
- giant `pipeline.py`
- giant `ui.py`
- giant `translator.py`
- catch-all files such as:
  - `utils.py`
  - `helpers.py`
  - `misc.py`
  - `all_utils.py`

### Rules
- prefer domain-specific module names
- separate UI code from business logic
- separate pipeline orchestration from worker logic
- separate translation logic from UI state handling
- separate configuration from execution logic
- separate formatting / validation / retry logic when files grow too large
- when a file keeps growing with new features, split the new feature into a new file instead of appending more code

---

## 5. UI and Business Logic Separation

UI files should mainly handle:
- layout
- widgets
- user interaction
- display updates
- progress display
- input validation at the UI boundary

Business logic files should mainly handle:
- processing
- file operations
- translation work
- networking
- retries
- orchestration
- worker execution

### Rules
- do not mix UI rendering and heavy processing logic in one file unless explicitly intended
- if UI files are starting to manage pipeline internals, split logic out
- if business logic starts to depend on UI state directly, introduce a cleaner interface

---

## 6. File Growth Control

When editing existing code:

- prefer small file growth
- avoid turning one file into the project center of gravity
- if a file is already large, consider splitting instead of extending it further
- if many new functions are being added to one module, ask whether a new module boundary is more appropriate
- for a new feature, default to a new file/module/component first; only extend the existing file for a small bug fix or a local wiring tweak

### Soft guidance
- small file: easy to edit safely
- medium file: still manageable
- large file: high AI rewrite risk
- huge file: strong candidate for splitting

---

## 6-1. Function Design Rules

### Function length
- if a function exceeds 40 lines, consider splitting it
- if a function exceeds 80 lines, split it — no exceptions
- each function should do exactly one thing

### Single Responsibility
- one function = one clear job
- if a function has multiple "and" steps (read AND parse AND save), split it
- helper logic that appears in more than one place → extract into its own function
- if an anchor starts absorbing a second feature, move the new behavior out into a new file or sub-anchor

### Function naming
Good examples:
- `load_config()`
- `parse_excel_row()`
- `send_retry_request()`
- `validate_input_path()`

Avoid vague names:
- `do_stuff()`
- `process()`
- `handle()`
- `run_all()`

### Inter-file connections
- do not create circular imports (A imports B, B imports A)
- if two files need to share logic, extract shared logic into a third module
- keep import chains shallow — deep chains are hard to trace and break easily

### Error handling
- handle errors where they occur — do not let exceptions silently propagate
- use specific exception types, not bare `except:`
- if the same error-handling pattern appears in many functions, extract it

---

## 7. Naming Rules

Prefer clear, specific names.

Good examples:
- `backup_worker.py`
- `hash_service.py`
- `translation_pipeline.py`
- `progress_widget.py`
- `retry_policy.py`

Avoid vague names unless there is a strong reason:
- `utils.py`
- `helpers.py`
- `common.py`
- `misc.py`

---

## 8. Change Scope Rules

Before making a code change, the AI should determine:

1. What is the smallest relevant file to edit?
2. Is there an existing anchor for the change?
3. Can this be solved with a patch instead of a rewrite?
4. Does this change affect unrelated modules?
5. Will this make the project structure worse?

If the answer to #4 or #5 is yes, reduce the change scope.

---

## 9. Explanation Rules

After making changes, the AI should be able to explain:

- what changed
- where it changed
- why that file was chosen
- why unrelated files were not modified
- whether risk increased or decreased

Changes should remain easy for a non-programmer to review.

---

## 10. Safety Rules for Non-Programmer Workflows

This project should remain usable by people who do not deeply understand the code.

Therefore:

- prefer predictable structure
- prefer explicit modules over cleverness
- avoid hidden side effects
- avoid broad changes that are hard to verify
- prefer code that can be explained plainly
- preserve working flows whenever possible

---

## 11. Recommended Vib Workflow

Use this loop whenever possible:

```bash
vib doctor --strict
vib anchor
vib patch "your request here"
# apply AI edit
vib explain --write-report
vib guard --strict --write-report
```

### Meaning
- `doctor` → inspect structure
- `anchor` → create safer edit zones
- `patch` → generate structured request
- `explain` → summarize what changed
- `guard` → verify whether it is safe to continue

---

## 12. Tool-Specific Use

### For OpenCode
Also consult:
- `vibelign_exports/opencode/RULES.md`
- `vibelign_exports/opencode/PROMPT_TEMPLATE.md`
- `vibelign_exports/opencode/SETUP.md`

### For Claude Code
Also consult:
- `vibelign_exports/claude/RULES.md`
- `vibelign_exports/claude/PROMPT_TEMPLATE.md`
- `vibelign_exports/claude/SETUP.md`

### For Cursor
Also consult:
- `vibelign_exports/cursor/RULES.md`
- `vibelign_exports/cursor/PROMPT_TEMPLATE.md`
- `vibelign_exports/cursor/SETUP.md`

### For Antigravity
Also consult:
- `vibelign_exports/antigravity/TASK_ARTIFACT.md`
- `vibelign_exports/antigravity/VERIFICATION_CHECKLIST.md`
- `vibelign_exports/antigravity/SETUP.md`

---

## 13. Default AI Instruction Template

Use this when sending tasks to an AI tool:

```text
Follow AI_DEV_SYSTEM_SINGLE_FILE.md.

Task:
[describe the requested change]

Target file:
[fill in target file]

Target anchor:
[fill in anchor if available]

Constraints:
- patch only
- do not rewrite unrelated files
- keep entry files small
- respect anchors
- avoid mixing UI and business logic

Goal:
[describe expected result]
```

---

## 14. Maintainability Rules for Non-Programmer Workflows

These rules ensure the codebase stays understandable and fixable even by people who did not write it.

### No magic numbers or strings
- do not hardcode unexplained values inline
- assign them to named constants with clear names

```python
# bad
if retry > 3:
# good
MAX_RETRY = 3
if retry > MAX_RETRY:
```

### Error messages must be human-readable
- error messages should explain what went wrong and what to check
- do not expose raw exception types as the only message

```python
# bad
raise Exception("NoneType")
# good
raise Exception(f"파일을 찾을 수 없습니다. 경로를 확인하세요: {path}")
```

### No silent failures
- do not use bare `except: pass` — always log or surface the error
- if an operation fails quietly, non-programmers have no way to diagnose it

```python
# bad
try:
    do_something()
except:
    pass
# good
except Exception as e:
    print(f"오류 발생: {e}")
```

### No dead code
- do not leave commented-out function blocks in the codebase
- remove unused imports, unused variables, and unused functions
- dead code confuses future edits and makes AI suggestions less reliable

### Keep dependencies in sync
- if a new `import` is added, update `pyproject.toml` or `requirements.txt` immediately
- do not leave undeclared dependencies that only work on the developer's machine

### Keep comments in sync with code
- if a function's behavior changes, update its comment or docstring
- outdated comments are worse than no comments — they actively mislead

---

## 15. Final Principle

**Fast AI edits are useful. Safe AI edits are better.**

If there is a conflict between speed and structure, prefer structure.
