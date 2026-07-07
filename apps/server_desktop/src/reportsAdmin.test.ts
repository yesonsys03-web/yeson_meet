import { afterEach, describe, expect, it, vi } from "vitest";
import {
  listReports,
  getReportStorage,
  reportViewUrl,
  deleteReportFiles,
  deleteReportSession,
} from "./reportsAdmin";

afterEach(() => vi.restoreAllMocks());

describe("reportsAdmin", () => {
  it("listReports calls loopback with with_sizes", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ items: [{ session_id: "a", title: "t", status: "ended", started_at: null, ended_at: null, report_ready: true, summary_ready: false }] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const rows = await listReports(8000);
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/api/v1/reports?with_sizes=true");
    expect(rows[0]!.session_id).toBe("a");
  });

  it("getReportStorage hits /reports/storage", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ total_bytes: 5, session_count: 1 }) });
    vi.stubGlobal("fetch", fetchMock);
    const st = await getReportStorage(8000);
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/api/v1/reports/storage");
    expect(st.session_count).toBe(1);
  });

  it("reportViewUrl builds the summary view url", () => {
    expect(reportViewUrl(8000, "xyz", "summary")).toBe("http://127.0.0.1:8000/api/v1/reports/xyz/summary/view");
    expect(reportViewUrl(8000, "xyz", "report")).toBe("http://127.0.0.1:8000/api/v1/reports/xyz/view");
  });

  it("deleteReportFiles calls DELETE /files", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetchMock);
    await deleteReportFiles(8000, "id1");
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/api/v1/reports/id1/files", { method: "DELETE" });
  });

  it("deleteReportSession calls DELETE /session", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetchMock);
    await deleteReportSession(8000, "id1");
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/api/v1/reports/id1/session", { method: "DELETE" });
  });
});
