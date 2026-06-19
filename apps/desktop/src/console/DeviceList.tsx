// === ANCHOR: DEVICE_LIST_START ===
import { useCallback, useEffect, useState } from "react";
import { consoleStyles } from "./consoleStyles";
import { fetchDevices, revokeDevice } from "./sessionApi";
import type { DeviceOut } from "./sessionApi";

type DeviceListProps = {
  adminToken: string;
};

// === ANCHOR: DEVICE_LIST_DEVICELIST_START ===
export function DeviceList({ adminToken }: DeviceListProps) {
  const [devices, setDevices] = useState<DeviceOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<Set<number>>(new Set());

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchDevices(adminToken)
      .then((list) => setDevices(list))
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [adminToken]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleRevoke(id: number) {
    if (revoking.has(id)) return;
    setRevoking((s) => new Set(s).add(id));
    try {
      await revokeDevice(id, adminToken);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRevoking((s) => {
        const next = new Set(s);
        next.delete(id);
        return next;
      });
    }
  }

  const active = devices.filter((d) => d.is_active);
  const inactive = devices.filter((d) => !d.is_active);

  return (
    <section style={consoleStyles.card} aria-label="Device key list">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
        <strong style={{ color: "#f8fafc", fontSize: 15 }}>사이드카 디바이스 키 (Device Keys)</strong>
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          style={{ ...consoleStyles.mutedAction, fontSize: 12, padding: "6px 12px" }}
        >
          {loading ? "로드 중..." : "새로고침"}
        </button>
      </div>

      {error && (
        <p style={consoleStyles.statusError}>{error}</p>
      )}

      {active.length === 0 && !loading && !error && (
        <p style={{ color: "#94a3b8", fontSize: 13 }}>활성 키 없음 (No active keys)</p>
      )}

      {active.length > 0 && (
        <div style={{ display: "grid", gap: 8 }}>
          {active.map((device) => (
            <DeviceRow
              key={device.id}
              device={device}
              revoking={revoking.has(device.id)}
              onRevoke={() => void handleRevoke(device.id)}
            />
          ))}
        </div>
      )}

      {inactive.length > 0 && (
        <details style={{ marginTop: 14 }}>
          <summary style={{ color: "#64748b", fontSize: 12, cursor: "pointer" }}>
            비활성 키 {inactive.length}개 (Revoked)
          </summary>
          <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
            {inactive.map((device) => (
              <DeviceRow
                key={device.id}
                device={device}
                revoking={false}
                onRevoke={undefined}
              />
            ))}
          </div>
        </details>
      )}
    </section>
  );
}
// === ANCHOR: DEVICE_LIST_DEVICELIST_END ===

type DeviceRowProps = {
  device: DeviceOut;
  revoking: boolean;
  onRevoke: (() => void) | undefined;
};

// === ANCHOR: DEVICE_LIST_DEVICEROW_START ===
function DeviceRow({ device, revoking, onRevoke }: DeviceRowProps) {
  const createdDate = new Date(device.created_at).toLocaleString();
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "10px 14px",
        borderRadius: 12,
        background: device.is_active ? "rgba(15,23,42,.78)" : "rgba(15,23,42,.38)",
        border: device.is_active
          ? "1px solid rgba(148,163,184,.2)"
          : "1px solid rgba(148,163,184,.1)",
        gap: 12,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <span style={{ color: "#e2e8f0", fontSize: 14, fontWeight: 700 }}>{device.name}</span>
        <span style={{ color: "#64748b", fontSize: 11, marginLeft: 8 }}>#{device.id}</span>
        <p style={{ margin: "2px 0 0", color: "#64748b", fontSize: 11 }}>{createdDate}</p>
      </div>
      {device.is_active && onRevoke && (
        <button
          type="button"
          disabled={revoking}
          onClick={onRevoke}
          style={{
            flexShrink: 0,
            padding: "7px 12px",
            borderRadius: 10,
            border: "1px solid rgba(251,113,133,.4)",
            color: "#fda4af",
            background: "rgba(159,18,57,.18)",
            fontSize: 12,
            fontWeight: 800,
            cursor: revoking ? "not-allowed" : "pointer",
            opacity: revoking ? 0.5 : 1,
          }}
        >
          {revoking ? "취소 중..." : "Revoke"}
        </button>
      )}
      {!device.is_active && (
        <span style={{ flexShrink: 0, color: "#64748b", fontSize: 11 }}>취소됨</span>
      )}
    </div>
  );
}
// === ANCHOR: DEVICE_LIST_DEVICEROW_END ===
// === ANCHOR: DEVICE_LIST_END ===
