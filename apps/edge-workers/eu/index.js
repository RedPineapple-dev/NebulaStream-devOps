export default {
  async fetch(request) {
    const start = Date.now();
    const url = new URL(request.url);
    const region = "eu-west";
    const latency_ms = Date.now() - start;
    if (url.pathname === "/health") return Response.json({ region, latency_ms, status: "ok" });
    if (url.pathname === "/ping")   return Response.json({ region, latency_ms, pong: true });
    return new Response("Not found", { status: 404 });
  }
};