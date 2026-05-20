import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";

const usLatency  = new Trend("us_latency_ms",  true);
const euLatency  = new Trend("eu_latency_ms",  true);
const apLatency  = new Trend("ap_latency_ms",  true);

const WORKERS = {
  "us": __ENV.WORKER_US || "https://nebula-us.nebulastream.workers.dev",
  "eu": __ENV.WORKER_EU || "https://nebula-eu.nebulastream.workers.dev",
  "ap": __ENV.WORKER_AP || "https://nebula-apac.nebulastream.workers.dev",
};

export const options = {
  scenarios: {
    smoke: {
      executor: "constant-vus",
      vus: 2,
      duration: "30s",
    },
  },
  thresholds: {
    http_req_failed:   ["rate<0.01"],
    http_req_duration: ["p(95)<400"],
    us_latency_ms:     ["p(95)<400"],
    eu_latency_ms:     ["p(95)<400"],
    ap_latency_ms:     ["p(95)<400"],
  },
};

export default function () {
  for (const [region, url] of Object.entries(WORKERS)) {
    const res = http.get(`${url}/health`, { tags: { region } });
    check(res, {
      "status is 200": (r) => r.status === 200,
      "has latency_ms": (r) => r.json("latency_ms") !== undefined,
    });
    const t = res.timings.duration;
    if (region === "us") usLatency.add(t);
    if (region === "eu") euLatency.add(t);
    if (region === "ap") apLatency.add(t);
  }
  sleep(1);
}
