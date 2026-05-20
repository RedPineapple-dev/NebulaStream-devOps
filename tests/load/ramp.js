import http from "k6/http";
import { check, sleep } from "k6";

const WORKERS = [
  __ENV.WORKER_US || "https://nebula-us.nebulastream.workers.dev",
  __ENV.WORKER_EU || "https://nebula-eu.nebulastream.workers.dev",
  __ENV.WORKER_AP || "https://nebula-apac.nebulastream.workers.dev",
];

export const options = {
  scenarios: {
    ramp: {
      executor: "ramping-vus",
      startVUs: 5,
      stages: [
        { duration: "1m",  target: 25 },
        { duration: "2m",  target: 100 },
        { duration: "2m",  target: 100 },
        { duration: "1m",  target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_failed:   ["rate<0.02"],
    http_req_duration: ["p(95)<500", "p(99)<1000"],
  },
};

export default function () {
  const u = WORKERS[Math.floor(Math.random() * WORKERS.length)];
  const res = http.get(`${u}/ping`);
  check(res, { "status 200": (r) => r.status === 200 });
  sleep(Math.random() * 0.5);
}
