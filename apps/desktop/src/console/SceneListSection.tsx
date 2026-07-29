// 구간 목록 구역 — 필터 탭(전체/확인필요/경계오류)·라벨 검색·일괄 도구(제안 적용,
// 접두 일괄 이름 바꾸기, 오독 갈라짐 정리, 인접 중복 병합, 되돌리기)·일괄 적용 확인
// 모달·안내문·구간 줄(SceneFilmstrip). 필터 상태·교정 계산은 전부 부모
// (SceneSplitView)가 들고, 여기는 표시와 콜백 연결만 한다(JSX 분리 — 로직 이동 없음).
import type { ComponentProps } from "react";
import { consoleStyles } from "./consoleStyles";
import { formatMs, type LabelFix } from "./sceneSplitLogic";
import type { SceneSegment } from "./videoApi";
import { SceneFilmstrip } from "./SceneFilmstrip";

type Props = {
  segments: SceneSegment[];
  anomaliesCount: number;
  onlyAnomalies: boolean;
  onlyBoundaryErrors: boolean;
  onFilterAll: () => void;
  onFilterAnomalies: () => void;
  onFilterBoundary: () => void;
  // 경계 오류 탭·재검사는 씬 모드 전용(boundary_issues가 segments_scene 기준).
  showBoundaryTab: boolean;
  boundaryCount: number;
  // 목록에 남아 실제로 무언가를 숨기고 있는 '문제없음' 확인 수 — '모두 해제' 안내.
  boundaryOkCount: number;
  onClearBoundaryOk: () => void;
  canRecheck: boolean;
  busy: boolean;
  onRecheckBoundaries: () => void;
  labelQuery: string;
  onLabelQueryChange: (v: string) => void;
  // Enter로 첫 결과 선택(포커스 해제는 여기서) — 어떤 줄을 고를지는 부모 몫.
  onSearchEnter: () => void;
  visibleIndices: number[] | null;
  confidentSuggestionCount: number;
  onOpenFixPreview: () => void;
  renameFrom: string;
  renameTo: string;
  onRenameFromChange: (v: string) => void;
  onRenameToChange: (v: string) => void;
  renameFixCount: number;
  onOpenRenamePreview: () => void;
  flankedCount: number;
  onCleanFlanked: () => void;
  adjacentDupCount: number;
  onMergeDuplicates: () => void;
  canUndoFixes: boolean;
  onUndoFixes: () => void;
  pendingFixes: LabelFix[] | null;
  fixChecked: Set<number>;
  onFixCheckedChange: (s: Set<number>) => void;
  onConfirmFixes: () => void;
  onCancelFixes: () => void;
  // 구간 줄은 SceneFilmstrip 그대로 — 부모가 조립한 props를 통째로 전달한다.
  filmstrip: ComponentProps<typeof SceneFilmstrip>;
};

export function SceneListSection({
  segments, anomaliesCount, onlyAnomalies, onlyBoundaryErrors,
  onFilterAll, onFilterAnomalies, onFilterBoundary,
  showBoundaryTab, boundaryCount, boundaryOkCount, onClearBoundaryOk,
  canRecheck, busy, onRecheckBoundaries,
  labelQuery, onLabelQueryChange, onSearchEnter, visibleIndices,
  confidentSuggestionCount, onOpenFixPreview,
  renameFrom, renameTo, onRenameFromChange, onRenameToChange,
  renameFixCount, onOpenRenamePreview,
  flankedCount, onCleanFlanked, adjacentDupCount, onMergeDuplicates,
  canUndoFixes, onUndoFixes,
  pendingFixes, fixChecked, onFixCheckedChange, onConfirmFixes, onCancelFixes,
  filmstrip,
}: Props) {
  return (
    <>
      {/* 구간 목록 탭 — 오독 의심 행만 모아 일괄 교정할 수 있게 한다. */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button"
          style={(onlyAnomalies || onlyBoundaryErrors)
            ? consoleStyles.mutedAction : consoleStyles.action}
          onClick={onFilterAll}>
          전체 ({segments.length})
        </button>
        <button type="button"
          style={onlyAnomalies ? consoleStyles.action : consoleStyles.mutedAction}
          disabled={anomaliesCount === 0}
          onClick={onFilterAnomalies}>
          {anomaliesCount > 0
            ? `⚠ 확인 필요 (${anomaliesCount})` : "확인 필요 없음"}
        </button>
        {/* 경계 오류(혼입) — 씬 모드 전용. 머리/꼬리 프레임에 이웃 슬레이트가
            잡힌 구간만 모아 본다(runAll 마지막 단계가 채운다). */}
        {showBoundaryTab ? (
          <button type="button"
            style={onlyBoundaryErrors ? consoleStyles.action : consoleStyles.mutedAction}
            disabled={boundaryCount === 0}
            onClick={onFilterBoundary}>
            {boundaryCount > 0
              ? `⚠ 경계 오류 (${boundaryCount})` : "경계 오류 없음"}
          </button>
        ) : null}
        {/* 고친 뒤 재검증 — 현재 세그먼트 그대로 경계만 다시 OCR 검사(세그먼트
            재계산 없음). 편집한 씬은 즉시 필터에서 빠지고, 이 버튼으로 전체를
            다시 확인할 수 있다(미저장 편집은 자동 저장 후 검사). */}
        {canRecheck ? (
          <button type="button" style={consoleStyles.mutedAction}
            disabled={busy}
            onClick={onRecheckBoundaries}>🔄 경계 다시 검사</button>
        ) : null}
        {/* 라벨 검색 — 슬레이트 번호 일부만 쳐도 좁혀진다(대소문자·구분자 무시).
            400+ 줄을 스크롤로 훑는 대신 쓰는 주 경로. */}
        <label style={{ display: "inline-flex", alignItems: "center", gap: 4,
                        fontSize: 12, opacity: 0.85 }}>
          검색
          <input value={labelQuery}
            onChange={(e) => onLabelQueryChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              // Enter로 첫 결과를 고르고 포커스를 뺀다 — 입력칸에 포커스가 있으면
              // 방향키가 캐럿 이동이라 ←/→ 훑기가 안 먹는다(검색 직후가 바로
              // 그 상황이다). 검색 → Enter → ←/→로 이어지게 한다.
              e.preventDefault();
              onSearchEnter();
              e.currentTarget.blur();
            }}
            placeholder="씬 번호 일부"
            style={{ width: 130, fontSize: 12, padding: "4px 6px", borderRadius: 4,
                     fontFamily: "monospace", background: "rgba(255,255,255,0.08)",
                     color: "inherit", border: "1px solid rgba(255,255,255,0.2)" }} />
        </label>
        {labelQuery ? (
          <>
            <button type="button" style={consoleStyles.mutedAction}
              title="검색 지우기" onClick={() => onLabelQueryChange("")}>×</button>
            <span style={{ fontSize: 12, opacity: 0.7 }}>
              {visibleIndices?.length ?? 0}개 표시
              {(visibleIndices?.length ?? 0) === 0 ? " — 일치하는 씬이 없어요" : ""}
            </span>
          </>
        ) : null}
        {onlyAnomalies && confidentSuggestionCount > 0 ? (
          <button type="button" style={consoleStyles.mutedAction}
            onClick={onOpenFixPreview}>
            제안 일괄 적용 ({confidentSuggestionCount})…
          </button>
        ) : null}
        {/* 일괄 이름 바꾸기 — 자동 제안이 못 다루는 접두(오독 단정 불가한
            '다른 단어' 급, 실기 Scene12→Seq12 26건)를 명시적 치환으로.
            접두 일치 행만 대상이고, 적용은 제안 일괄 적용과 같은 확인
            다이얼로그를 거친다(체크 선별·되돌리기·인접 병합 포함).
            전체·확인필요 목록에서 노출 — 치환 대상은 필터와 무관하게 전체
            라벨이라, 경계 오류 검수 화면에서만 숨긴다(맥락이 다른 도구). */}
        {!onlyBoundaryErrors ? (
          <label style={{ display: "inline-flex", alignItems: "center", gap: 4,
                          fontSize: 12, opacity: 0.85 }}>
            일괄 이름 바꾸기
            <input value={renameFrom}
              onChange={(e) => onRenameFromChange(e.target.value)}
              placeholder="찾을 접두"
              style={{ width: 100, fontSize: 12, padding: "4px 6px",
                       borderRadius: 4, fontFamily: "monospace",
                       background: "rgba(255,255,255,0.08)", color: "inherit",
                       border: "1px solid rgba(255,255,255,0.2)" }} />
            →
            <input value={renameTo}
              onChange={(e) => onRenameToChange(e.target.value)}
              placeholder="바꿀 접두"
              style={{ width: 100, fontSize: 12, padding: "4px 6px",
                       borderRadius: 4, fontFamily: "monospace",
                       background: "rgba(255,255,255,0.08)", color: "inherit",
                       border: "1px solid rgba(255,255,255,0.2)" }} />
            <button type="button" style={consoleStyles.mutedAction}
              disabled={renameFixCount === 0}
              onClick={onOpenRenamePreview}>
              바꾸기 ({renameFixCount})…
            </button>
          </label>
        ) : null}
        {/* 오독 갈라짐 정리 — 앞뒤 동일 라벨 사이 낀 오독을 흡수(시퀀스 특효). */}
        {flankedCount > 0 ? (
          <button type="button" style={consoleStyles.action}
            onClick={onCleanFlanked}>
            오독 갈라짐 정리 ({flankedCount})
          </button>
        ) : null}
        {/* 인접 중복 병합 — 교정으로 같아졌거나 수동 교정 후 갈라진 씬을 합친다. */}
        {adjacentDupCount > 0 ? (
          <button type="button" style={consoleStyles.mutedAction}
            onClick={onMergeDuplicates}>
            인접 중복 병합 ({adjacentDupCount})
          </button>
        ) : null}
        {canUndoFixes ? (
          <button type="button" style={consoleStyles.mutedAction}
            onClick={onUndoFixes}>되돌리기</button>
        ) : null}
      </div>

      {/* 일괄 적용 확인 — 무엇이 어떻게 바뀌는지 보고 체크한 것만 적용한다. */}
      {pendingFixes ? (
        <div style={{ border: "1px solid rgba(255,255,255,0.15)", borderRadius: 6,
                      padding: 10, display: "flex", flexDirection: "column", gap: 6 }}>
          <strong style={{ fontSize: 13 }}>
            이렇게 바꿉니다 — 체크한 것만 적용됩니다 ({fixChecked.size}/{pendingFixes.length})
          </strong>
          <div style={{ maxHeight: 260, overflowY: "auto", display: "flex",
                        flexDirection: "column", gap: 3 }}>
            {pendingFixes.map((f) => (
              <label key={f.index}
                     style={{ display: "flex", gap: 8, alignItems: "center",
                              fontSize: 12, fontFamily: "monospace",
                              padding: "3px 4px", borderRadius: 3,
                              background: "rgba(255,255,255,0.04)" }}>
                <input type="checkbox" checked={fixChecked.has(f.index)}
                  onChange={(e) => {
                    const next = new Set(fixChecked);
                    if (e.target.checked) next.add(f.index); else next.delete(f.index);
                    onFixCheckedChange(next);
                  }} />
                <span style={{ opacity: 0.55, flexShrink: 0 }}>
                  {formatMs(segments[f.index]?.start_ms ?? 0)}
                </span>
                <span style={{ color: "#e2b340", overflowWrap: "anywhere" }}>{f.from}</span>
                <span style={{ opacity: 0.6, flexShrink: 0 }}>→</span>
                <span style={{ color: "#3f9a5f", overflowWrap: "anywhere" }}>{f.to}</span>
              </label>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button type="button" style={consoleStyles.action}
              disabled={fixChecked.size === 0}
              onClick={onConfirmFixes}>적용 ({fixChecked.size})</button>
            <button type="button" style={consoleStyles.mutedAction}
              onClick={onCancelFixes}>취소</button>
            <span style={{ fontSize: 11, opacity: 0.6 }}>
              적용해도 저장 전이라 "되돌리기"로 한 번 물릴 수 있습니다.
            </span>
          </div>
        </div>
      ) : null}
      {onlyAnomalies ? (
        <p style={{ fontSize: 12, opacity: 0.7, margin: 0 }}>
          라벨 모양이 다수와 어긋나는 구간입니다(주로 OCR이 구분자를 놓친 경우).
          제안이 있으면 라벨 오른쪽에 표시되고, 숫자가 남아 애매한 제안은
          일괄 적용에서 빠집니다 — 썸네일을 눌러 실제 프레임을 확인하세요.
        </p>
      ) : null}
      {onlyBoundaryErrors ? (
        <p style={{ fontSize: 12, opacity: 0.7, margin: 0 }}>
          경계(머리·꼬리) 프레임에 이웃 씬의 슬레이트가 잡힌 구간입니다 —
          익스포트 시 앞뒤 씬이 한두 프레임 섞일 수 있습니다. 썸네일을 눌러 실제
          경계 프레임을 확인하고, 필요하면 병합하거나 경계를 조정하세요.
          확인했는데 문제가 없으면 <b>✓ 문제없음</b>으로 목록에서 뺄 수 있습니다.
          {boundaryOkCount > 0 ? (
            <>
              {"  "}확인함 {boundaryOkCount}건 ·{" "}
              <button type="button" style={consoleStyles.mutedAction}
                title="확인 표시를 전부 지우고 처음부터 다시 봅니다"
                onClick={onClearBoundaryOk}>모두 해제</button>
            </>
          ) : null}
        </p>
      ) : null}
      <SceneFilmstrip {...filmstrip} />
    </>
  );
}
