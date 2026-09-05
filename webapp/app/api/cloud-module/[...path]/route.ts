import { NextRequest, NextResponse } from "next/server";

const BRIDGE_BASE = (process.env.WAVESHARE_BRIDGE_BASE || "http://127.0.0.1:9000").replace(/\/$/, "");

/**
 * Catch-all proxy from webapp → waveshare_bridge (FastAPI on 9000).
 *
 *  GET  /api/cloud-module/modes                       → bridge /modes
 *  GET  /api/cloud-module/preview/DAILY               → bridge /preview/DAILY  (image/png)
 *  GET  /api/cloud-module/status                      → bridge /status
 *  POST /api/cloud-module/push        body {persona}  → bridge /push
 *  POST /api/cloud-module/push_all    body {delay,personas?} → bridge /push_all
 */
async function proxy(req: NextRequest, params: Promise<{ path: string[] }>, method: string) {
  const { path } = await params;
  const url = `${BRIDGE_BASE}/${(path || []).join("/")}${req.nextUrl.search || ""}`;

  const headers: Record<string, string> = {};
  const ct = req.headers.get("content-type");
  if (ct) headers["content-type"] = ct;

  let body: BodyInit | undefined;
  if (method !== "GET" && method !== "HEAD") {
    body = await req.arrayBuffer();
  }

  try {
    const res = await fetch(url, { method, headers, body, cache: "no-store" });
    const resCt = res.headers.get("content-type") || "";
    if (resCt.includes("image/")) {
      const buf = await res.arrayBuffer();
      return new NextResponse(buf, { status: res.status, headers: { "content-type": resCt } });
    }
    if (resCt.includes("application/json")) {
      return NextResponse.json(await res.json(), { status: res.status });
    }
    return new NextResponse(await res.text(), {
      status: res.status,
      headers: { "content-type": resCt || "text/plain" },
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "bridge unreachable";
    return NextResponse.json(
      { error: "bridge_unreachable", message: msg, bridge: BRIDGE_BASE },
      { status: 503 },
    );
  }
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(req, ctx.params, "GET");
}
export async function POST(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(req, ctx.params, "POST");
}
