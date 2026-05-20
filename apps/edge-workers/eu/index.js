export default {
  async fetch(request) {
    await new Promise(r => setTimeout(r, 400));
    const url = new URL(request.url);
    const region = "eu-west";
    const latency_ms = 400;
    if (url.pathname === "/health") return Response.json({ region, latency_ms, status: "degraded" });
    if (url.pathname === "/ping")   return Response.json({ region, latency_ms, pong: true });
    return new Response("Not found", { status: 404 });
  }
};