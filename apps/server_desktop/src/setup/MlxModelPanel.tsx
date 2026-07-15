// === ANCHOR: MLX_MODEL_PANEL_START ===
// MLX 로컬 번역 모델 관리 — apple_mlx_live_translate provider 전용 (실리콘맥).
// ServerConfigPanel.tsx의 칩/버튼/서브텍스트 스타일 관례를 그대로 미러링한다(공유 export
// 없이 로컬 styles 객체로 복제 — 파일 간 결합을 늘리지 않기 위함).
import { listen } from "@tauri-apps/api/event";
import { useEffect, useState } from "react";
import { downloadMlxModel, mlxModelStatus } from "./serverConfig";
import { BUILTIN_MLX_MODELS, type LiveMlxModel, listLiveMlxModels } from "../translateCatalogAdmin";

export function MlxModelPanel(props: {
  selectedModel: string;
  onSelectModel: (id: string) => void;
  port: number;
  running: boolean;
}) {
  const [models, setModels] = useState<LiveMlxModel[]>(BUILTIN_MLX_MODELS);
  const [installed, setInstalled] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");

  const refresh = async () => {
    // 목록은 서버 카탈로그(빌트인+원격), 설치 여부는 Tauri — 서버가 없어도 동작한다.
    const list = await listLiveMlxModels(props.port);
    setModels(list);
    const entries = await Promise.all(
      list.map(async (m) => [m.id, await mlxModelStatus(m.id)] as const),
    );
    setInstalled(Object.fromEntries(entries));
  };

  // Tauri 진행률 리스너는 세션당 한 번만 등록한다(포트/running 변경마다 갈아끼우면
  // 다운로드 도중 이벤트를 놓칠 위험이 있다).
  useEffect(() => {
    const unlisten = listen<string>("mlx-download-progress", (e) => setProgress(e.payload));
    return () => {
      void unlisten.then((fn) => fn());
    };
  }, []);

  // 서버가 뒤늦게 뜨거나(초기 설정 단계에는 항상 다운 상태) 포트가 바뀌면 목록을
  // 다시 받아온다 — 그렇지 않으면 원격 카탈로그가 이 세션 내내 반영되지 않는다.
  useEffect(() => {
    void refresh();
    // refresh는 매 렌더 새로 만들어지는 클로저라 deps에 넣으면 무한 루프가 된다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.port, props.running]);

  const download = async (id: string) => {
    setBusy(id);
    setError("");
    try {
      await downloadMlxModel(id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
      setProgress("");
    }
  };

  return (
    <div style={styles.wrap}>
      <span style={styles.fieldLabel}>로컬 번역 모델 (MLX — 실리콘맥 전용)</span>
      {models.map((m) => (
        <div key={m.id} style={styles.row}>
          <label style={styles.radioLabel}>
            <input
              type="radio"
              name="mlx-model"
              checked={(props.selectedModel || models[0]?.id) === m.id}
              onChange={() => props.onSelectModel(m.id)}
            />
            <span>{m.label} · 약 {(m.bytes / 1_000_000_000).toFixed(1)}GB</span>
            <span style={installed[m.id] ? styles.chipOn : styles.chipOff}>
              {installed[m.id] ? "설치됨" : "미설치"}
            </span>
          </label>
          {!installed[m.id] ? (
            <button style={styles.button} disabled={busy !== null} onClick={() => void download(m.id)}>
              {busy === m.id ? "다운로드 중…" : "다운로드"}
            </button>
          ) : null}
        </div>
      ))}
      {busy && progress ? <pre style={styles.progress}>{progress}</pre> : null}
      {error ? <p style={styles.error}>{error}</p> : null}
      <p style={styles.sub}>모델 변경·설치 후에는 서버를 재시작해야 적용됩니다.</p>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: { marginTop: -4, marginBottom: 10, display: "flex", flexDirection: "column", gap: 6 },
  fieldLabel: { fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5, color: "var(--ys-text-faint)" },
  row: { display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" },
  radioLabel: { display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--ys-text-body)" },
  chipOn: {
    fontSize: 11,
    fontWeight: 600,
    padding: "3px 8px",
    borderRadius: "var(--ys-radius-pill)",
    background: "var(--ys-success-bg)",
    color: "var(--ys-success-text)",
  },
  chipOff: {
    fontSize: 11,
    fontWeight: 600,
    padding: "3px 8px",
    borderRadius: "var(--ys-radius-pill)",
    background: "var(--ys-danger-bg)",
    color: "var(--ys-danger-text)",
  },
  button: {
    padding: "7px 16px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-border-strong)",
    background: "transparent",
    color: "var(--ys-text-label)",
    cursor: "pointer",
    fontSize: 13,
  },
  progress: {
    margin: "4px 0 0",
    padding: "6px 8px",
    background: "var(--ys-bg-app)",
    border: "1px solid var(--ys-border-subtle)",
    borderRadius: "var(--ys-radius-control)",
    fontSize: 11,
    color: "var(--ys-text-muted)",
    whiteSpace: "pre-wrap",
    wordBreak: "break-all",
  },
  error: { margin: "4px 0 0", color: "var(--ys-danger-text)", fontSize: 13 },
  sub: { margin: "4px 0 0", fontSize: 12, color: "var(--ys-text-muted)" },
};
// === ANCHOR: MLX_MODEL_PANEL_END ===
