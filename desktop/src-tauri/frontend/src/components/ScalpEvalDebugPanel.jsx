import React, { useState, useEffect } from "react";

// Default export component
export default function ScalpEvalDebugPanel() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [intervalMs, setIntervalMs] = useState(5000);

  useEffect(() => {
    let t = null;
    if (autoRefresh) {
      t = setInterval(() => {
        fetchEval();
      }, intervalMs);
    }
    return () => {
      if (t) clearInterval(t);
    };
  }, [autoRefresh, intervalMs]);

  async function fetchEval() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/eval_current");
      if (!res.ok) throw new Error(`HTTP ${res.status} - ${res.statusText}`);
      const json = await res.json();
      setData(json);
    } catch (err) {
      setError(err.message || String(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  function prettyJSON(obj) {
    try {
      return JSON.stringify(obj, null, 2);
    } catch (e) {
      return String(obj);
    }
  }

  function copyToClipboard() {
    if (!data) return;
    navigator.clipboard.writeText(prettyJSON(data));
  }

  return (
    <div className="max-w-4xl mx-auto p-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold">Scalp — Strategy Eval Debug</h2>
        <div className="flex items-center gap-2">
          <button
            onClick={fetchEval}
            className="px-3 py-1 rounded-md border bg-white hover:bg-gray-50 text-sm"
            disabled={loading}
          >
            {loading ? "Fetching..." : "Fetch Now"}
          </button>
          <button
            onClick={() => {
              copyToClipboard();
            }}
            className="px-3 py-1 rounded-md border bg-white hover:bg-gray-50 text-sm"
            disabled={!data}
          >
            Copy JSON
          </button>
        </div>
      </div>

      <div className="mb-4 flex items-center gap-3">
        <label className="text-sm">Auto-refresh</label>
        <input
          type="checkbox"
          checked={autoRefresh}
          onChange={(e) => setAutoRefresh(e.target.checked)}
          className="h-4 w-4"
        />
        <label className="text-sm">Interval (ms)</label>
        <input
          type="number"
          value={intervalMs}
          onChange={(e) => setIntervalMs(Number(e.target.value))}
          className="border rounded px-2 py-1 w-28 text-sm"
        />
        <div className="ml-4 text-sm text-gray-600">Endpoint: <span className="font-mono">/api/eval_current</span></div>
      </div>

      <div className="bg-black/80 text-white rounded p-3 min-h-[200px] overflow-auto">
        {error ? (
          <div className="text-red-300">Error: {error}</div>
        ) : !data ? (
          <div className="text-gray-300">No data yet. Click "Fetch Now" to run evaluation.</div>
        ) : (
          <pre className="whitespace-pre-wrap text-sm leading-5">{prettyJSON(data)}</pre>
        )}
      </div>

      <div className="mt-3 text-xs text-gray-500">
        Tip: if your backend runs on a different host/port while developing, set a proxy or adjust the fetch URL.
      </div>
    </div>
  );
}
