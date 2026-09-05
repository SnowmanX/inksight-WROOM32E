"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Eye, Loader2, RefreshCw, Send, Layers, Zap } from "lucide-react";
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
  const [cacheBuster, setCacheBuster] = useState(0);
  const [log, setLog] = useState<string[]>([]);
  const [otaState, setOtaState] = useState<{
    armed: boolean;
    path: string | null;
    history: { ts?: number; result?: string; error?: string }[];
    default_bin: string;
  } | null>(null);
  const [otaBusy, setOtaBusy] = useState(false);

  const appendLog = useCallback((line: string) => {
    setLog((prev) => [`[${new Date().toLocaleTimeString()}] ${line}`, ...prev].slice(0, 40));
  }, []);

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

  const refreshOta = useCallback(async () => {
    try {
      const r = await fetch("/api/cloud-module/ota/status", { cache: "no-store" });
      const d = await r.json();
      setOtaState(d);
    } catch {
      /* keep last */
    }
  }, []);

  useEffect(() => {
    refreshModes();
    refreshStatus();
    refreshOta();
    const t = setInterval(() => {
      refreshStatus();
      refreshOta();
    }, 5000);
    return () => clearInterval(t);
  }, [refreshModes, refreshStatus, refreshOta]);

  const doPreview = useCallback(async () => {
    if (!selected) return;
    setPreviewLoading(true);
    const buster = Date.now();
    setCacheBuster(buster);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
    try {
      const r = await fetch(`/api/cloud-module/preview/${selected}?t=${buster}`, {
        cache: "no-store",
        headers: { "cache-control": "no-cache" },
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const blob = await r.blob();
      if (blob.size === 0) throw new Error("empty response");
      const url = URL.createObjectURL(blob);
      setPreviewUrl(url);
      appendLog(`preview ${selected} ok (${blob.size} bytes, ${blob.type || "?"})`);
    } catch (e) {
      appendLog(`preview ${selected} failed: ${e}`);
    } finally {
      setPreviewLoading(false);
    }
  }, [selected, previewUrl, appendLog]);

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

  const doOtaArm = useCallback(async () => {
    if (!otaState) return;
    const path = otaState.default_bin;
    if (
      !confirm(
        `⚠️ 危险操作 ⚠️\n\n即将武装 OTA 刷写:\n  ${path}\n\n设备下次连入 bridge 时, 就会开始推 .bin, 失败/中断 = 设备变砖.\n\n确认要继续?`,
      )
    ) {
      return;
    }
    setOtaBusy(true);
    try {
      const r = await fetch("/api/cloud-module/ota/arm", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ path }),
      });
      const d = await r.json();
      if (d.armed) {
        appendLog(`⚠️ OTA ARMED. 设备下次连入就刷 .bin: ${path} (${d.size} bytes)`);
      } else {
        appendLog(`ota arm failed: ${d.detail || JSON.stringify(d)}`);
      }
      refreshOta();
    } catch (e) {
      appendLog(`ota arm failed: ${e}`);
    } finally {
      setOtaBusy(false);
    }
  }, [otaState, refreshOta]);

  const doOtaCancel = useCallback(async () => {
    setOtaBusy(true);
    try {
      const r = await fetch("/api/cloud-module/ota/cancel", { method: "POST" });
      const d = await r.json();
      if (d.armed === false) {
        appendLog(`ota cancelled`);
      }
      refreshOta();
    } catch (e) {
      appendLog(`ota cancel failed: ${e}`);
    } finally {
      setOtaBusy(false);
    }
  }, [refreshOta]);

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
          <CardTitle className="flex items-center gap-2">
            <Zap size={18} className="text-amber-500" /> OTA 固件升级
            {otaState?.armed ? (
              <span className="ml-2 inline-flex items-center gap-1 rounded-sm bg-red-100 text-red-700 px-2 py-0.5 text-[10px] font-semibold">
                <AlertTriangle size={12} /> ARMED
              </span>
            ) : (
              <span className="ml-2 inline-flex items-center gap-1 rounded-sm bg-ink/5 text-ink-light px-2 py-0.5 text-[10px]">
                idle
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="text-xs text-ink-light space-y-1">
            <div>
              默认固件: <span className="font-mono text-ink/80">{otaState?.default_bin || "..."}</span>
            </div>
            {otaState?.armed && (
              <div className="text-red-700">
                等待设备连入: <span className="font-mono">{otaState.path}</span>
              </div>
            )}
            {otaState?.history && otaState.history.length > 0 && (
              <div className="pt-2 border-t border-ink/10">
                <div className="font-semibold text-ink/80">最近 OTA 记录:</div>
                {otaState.history.slice(-3).map((h, i) => (
                  <div key={i} className="font-mono text-[10px]">
                    {h.ts ? new Date(h.ts * 1000).toLocaleTimeString() : "—"} →{" "}
                    {h.result || h.error || "?"}
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="flex gap-2">
            {!otaState?.armed ? (
              <Button
                onClick={doOtaArm}
                disabled={!otaState || otaBusy}
                variant="destructive"
              >
                {otaBusy ? <Loader2 className="animate-spin" size={14} /> : <Zap size={14} />}
                <span className="ml-1">武装 OTA（危险）</span>
              </Button>
            ) : (
              <Button
                onClick={doOtaCancel}
                disabled={otaBusy}
                variant="outline"
                className="border-red-300 text-red-700 hover:bg-red-50"
              >
                取消武装
              </Button>
            )}
          </div>
          <p className="text-[10px] text-ink-light leading-relaxed">
            ⚠️ 固件刷写会通过微雪私有协议 <code>;O/</code> 推送 <code>firmware_merged.bin</code>。
            刷写期间不要断电/关电脑。失败/中断 = 设备变砖, 需用 USB-TTL + esptool 救砖。
          </p>
        </CardContent>
      </Card>

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
