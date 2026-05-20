import { Variables } from "@fermyon/spin-sdk";

const encoder = new TextEncoder();

function getRegion(): string {
  try {
    return Variables.get("region") ?? "unknown";
  } catch {
    return "unknown";
  }
}

export default {
  async fetch(request: Request): Promise<Response> {
    const start = Date.now();
    const url = new URL(request.url);
    const region = getRegion();

    if (url.pathname === "/health") {
      const latency_ms = Date.now() - start;
      return new Response(
        JSON.stringify({ region, latency_ms, status: "ok" }),
        { headers: { "content-type": "application/json" } }
      );
    }

    if (url.pathname === "/ping") {
      const latency_ms = Date.now() - start;
      return new Response(
        JSON.stringify({ region, latency_ms, pong: true }),
        { headers: { "content-type": "application/json" } }
      );
    }

    return new Response("Not found", { status: 404 });
  },
};
