"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, Eye, Loader2, RefreshCw, Send, Layers } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type ModeInfo = {
  mode_id: string;
  display_name: string;
  icon: string;
  description?: string;
};

type Status = {
  device: { host: string; port: number };
  screen: { w: number; h: number };
  latest_persona: string | null;
  latest_bw_cached: boolean;
  latest_bw_len: number;
};

export default function CloudModulePage() {
  const [modes, setModes] = useState<ModeInfo[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [pushingAll, setPushingAll] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const cacheBuster = useRef(0);

  const appendLog = (line: string) =>
    setLog((prev) => [`[${new Date().toLocaleTimeString()}] ${line}`, ...prev].slice(0, 40));

  const refreshModes = useCallback(async () => {
    try {
      const r = await fetch("/api/cloud-module/modes", { cache: "no-store" });
      const d = await r.json();
      setModes(d.modes || []);
      if (d.modes?.length && !selected) {
        setSelected(d.modes[0].mode_id);
      }
      appendLog(`loaded ${d.count} modes from bridge`);
    } catch (e) {
      appendLog(`modes load failed: ${e}`);
    }
  }, [selected]);

  const refreshStatus = useCallback(async () => {
    try {
      const r = await fetch("/api/cloud-module/status", { cache: "no-store" });
      const d = await r.json();
      setStatus(d);
    } catch {
      /* keep last */
    }
  }, []);

  useEffect(() => {
    refreshModes();
    refreshStatus();
    const t = setInterval(refreshStatus, 5000);
    return () => clearInterval(t);
  }, [refreshModes, refreshStatus]);

  const doPreview = useCallback(async () => {
    if (!selected) return;
    setPreviewLoading(true);
    cacheBuster.current += 1;
    setPreviewUrl(null);
    try {
      const r = await fetch(`/api/cloud-module/preview/${selected}?t=${cacheBuster.current}`, {
        cache: "no-store",
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const blob = await r.blob();
      setPreviewUrl(URL.createObjectURL(blob));
      appendLog(`preview ${selected} ok (${blob.size} bytes)`);
    } catch (e) {
      appendLog(`preview ${selected} failed: ${e}`);
    } finally {
      setPreviewLoading(false);
    }
  }, [selected]);

  const doPush = useCallback(async () => {
    if (!selected) return;
    setPushing(true);
    try {
      const r = await fetch("/api/cloud-module/push", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ persona: selected }),
      });
      const d = await r.json();
      appendLog(
        d.ok
          ? `push ${selected} ok, bw=${d.bw_len} bytes, queued for next device connect`
          : `push ${selected} failed: ${d.error || JSON.stringify(d)}`,
      );
      refreshStatus();
    } catch (e) {
      appendLog(`push ${selected} failed: ${e}`);
    } finally {
      setPushing(false);
    }
  }, [selected, refreshStatus]);

  const doPushAll = useCallback(async () => {
    if (!confirm(`依次推送全部 ${modes.length} 个模式到设备？每个之间墨水屏刷 ~6s，大约 ${Math.ceil(modes.length * 6 / 60)} 分钟。`)) return;
    setPushingAll(true);
    try {
      const r = await fetch("/api/cloud-module/push_all", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ delay: 6 }),
      });
      const d = await r.json();
      const ok = d.results?.filter((x: { ok: boolean }) => x.ok).length || 0;
      appendLog(`push_all done: ${ok}/${d.total} ok`);
    } catch (e) {
      appendLog(`push_all failed: ${e}`);
    } finally {
      setPushingAll(false);
      refreshStatus();
    }
  }, [modes.length, refreshStatus]);

  const selectedMeta = useMemo(
    () => modes.find((m) => m.mode_id === selected),
    [modes, selected],
  );

  return (
    <main className="mx-auto max-w-5xl p-6 space-y-6">
      <header className="flex items-baseline justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Waveshare 4.2&quot; e-Paper Cloud Module</h1>
          <p className="text-sm text-ink-light">
            通过 InkSight 后端渲染 30 个真实模式, 桥接到微雪 TCP 6868 协议推图。
          </p>
        </div>
        <div className="text-xs text-ink-light text-right">
          {status ? (
            <>
              <div>device: {status.device.host}:{status.device.port}</div>
              <div>screen: {status.screen.w}×{status.screen.h}</div>
              <div>
                latest: {status.latest_persona || "—"}{" "}
                {status.latest_bw_cached ? "✓ cached" : ""} ({status.latest_bw_len} B)
              </div>
            </>
          ) : (
            <Loader2 className="inline animate-spin" size={14} />
          )}
        </div>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Layers size={18} /> 模式列表 ({modes.length})
              <Button size="sm" variant="ghost" onClick={refreshModes} className="ml-auto">
                <RefreshCw size={14} />
              </Button>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-2 max-h-[480px] overflow-y-auto pr-1">
              {modes.map((m) => {
                const active = m.mode_id === selected;
                return (
                  <button
                    key={m.mode_id}
                    onClick={() => setSelected(m.mode_id)}
                    className={`text-left rounded-md border px-3 py-2 transition-colors ${
                      active
                        ? "border-ink bg-ink text-paper"
                        : "border-ink/15 hover:border-ink/40"
                    }`}
                  >
                    <div className="text-sm font-medium">{m.display_name}</div>
                    <div className={`text-[10px] ${active ? "text-paper/70" : "text-ink-light"}`}>
                      {m.mode_id}
                    </div>
                  </button>
                );
              })}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Eye size={18} /> 预览 / 推送
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="text-sm">
              当前: <span className="font-semibold">{selectedMeta?.display_name || "—"}</span>{" "}
              <span className="text-ink-light">({selected || "—"})</span>
            </div>
            <div className="flex gap-2">
              <Button onClick={doPreview} disabled={!selected || previewLoading}>
                {previewLoading ? <Loader2 className="animate-spin" size={14} /> : <Eye size={14} />}
                <span className="ml-1">预览</span>
              </Button>
              <Button onClick={doPush} disabled={!selected || pushing}>
                {pushing ? <Loader2 className="animate-spin" size={14} /> : <Send size={14} />}
                <span className="ml-1">推到设备</span>
              </Button>
              <Button onClick={doPushAll} disabled={pushingAll || modes.length === 0} variant="outline">
                {pushingAll ? <Loader2 className="animate-spin" size={14} /> : <CheckCircle2 size={14} />}
                <span className="ml-1">一键全推</span>
              </Button>
            </div>
            <div
              className="border border-ink/10 rounded-sm bg-white flex items-center justify-center overflow-hidden"
              style={{ aspectRatio: "4 / 3" }}
            >
              {previewLoading ? (
                <Loader2 className="animate-spin text-ink-light" size={28} />
              ) : previewUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={previewUrl} alt="preview" className="max-w-full max-h-full object-contain" />
              ) : (
                <span className="text-xs text-ink-light">点击 &quot;预览&quot; 生成当前模式图像</span>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">操作日志</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="text-xs font-mono space-y-1 max-h-40 overflow-y-auto">
            {log.length === 0 ? (
              <li className="text-ink-light">（无）</li>
            ) : (
              log.map((l, i) => (
                <li key={i} className="text-ink/80">
                  {l}
                </li>
              ))
            )}
          </ul>
        </CardContent>
      </Card>
    </main>
  );
}
