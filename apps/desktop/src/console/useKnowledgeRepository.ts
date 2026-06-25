// === ANCHOR: USE_KNOWLEDGE_REPOSITORY_START ===
import { useCallback, useEffect, useRef, useState } from "react";
import { listSessions, type SessionListItem, type SessionListOut } from "./knowledgeApi";

const DEBOUNCE_MS = 350;
const PAGE_SIZE = 30;

export type KnowledgeRepositoryState = {
  items: SessionListItem[];
  hasMore: boolean;
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  query: string;
  selectedId: string | null;
  selectedItem: SessionListItem | null;
  // cached report HTML keyed by external_id
  reportHtmlCache: Map<string, string>;
};

export type KnowledgeRepositoryActions = {
  setQuery: (q: string) => void;
  loadMore: () => void;
  selectSession: (id: string | null) => void;
  cacheReportHtml: (id: string, html: string) => void;
  reload: () => void;
};

export function useKnowledgeRepository(operatorToken: string): KnowledgeRepositoryState & KnowledgeRepositoryActions {
  const [items, setItems] = useState<SessionListItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQueryRaw] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reportHtmlCache, setReportHtmlCache] = useState<Map<string, string>>(new Map());
  const [offset, setOffset] = useState(0);
  // bump this to force a reload (e.g. after explicit reload() call)
  const [reloadKey, setReloadKey] = useState(0);

  // Debounce query
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  function setQuery(q: string) {
    setQueryRaw(q);
    if (debounceTimer.current !== null) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => {
      setDebouncedQuery(q);
      setOffset(0);
    }, DEBOUNCE_MS);
  }

  // Initial load and re-load on query/reloadKey change
  useEffect(() => {
    if (!operatorToken) return;

    let cancelled = false;
    setLoading(true);
    setError(null);
    setOffset(0);

    listSessions({ q: debouncedQuery || undefined, limit: PAGE_SIZE, offset: 0, operatorToken })
      .then((out: SessionListOut) => {
        if (cancelled) return;
        setItems(out.items);
        setHasMore(out.has_more);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setItems([]);
        setHasMore(false);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [operatorToken, debouncedQuery, reloadKey]);

  const loadMore = useCallback(() => {
    if (!hasMore || loadingMore || loading || !operatorToken) return;
    const nextOffset = offset + PAGE_SIZE;
    setLoadingMore(true);

    listSessions({ q: debouncedQuery || undefined, limit: PAGE_SIZE, offset: nextOffset, operatorToken })
      .then((out: SessionListOut) => {
        setItems((prev) => [...prev, ...out.items]);
        setHasMore(out.has_more);
        setOffset(nextOffset);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        setLoadingMore(false);
      });
  }, [hasMore, loadingMore, loading, operatorToken, debouncedQuery, offset]);

  const selectSession = useCallback((id: string | null) => {
    setSelectedId(id);
  }, []);

  const cacheReportHtml = useCallback((id: string, html: string) => {
    setReportHtmlCache((prev) => {
      const next = new Map(prev);
      next.set(id, html);
      return next;
    });
  }, []);

  const reload = useCallback(() => {
    setReloadKey((k) => k + 1);
  }, []);

  const selectedItem = items.find((it) => it.external_id === selectedId) ?? null;

  return {
    items,
    hasMore,
    loading,
    loadingMore,
    error,
    query,
    selectedId,
    selectedItem,
    reportHtmlCache,
    setQuery,
    loadMore,
    selectSession,
    cacheReportHtml,
    reload,
  };
}
// === ANCHOR: USE_KNOWLEDGE_REPOSITORY_END ===
