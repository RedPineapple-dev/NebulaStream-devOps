import * as pulumi from "@pulumi/pulumi";
import * as cloudflare from "@pulumi/cloudflare";
import * as command from "@pulumi/command";
import * as fs from "fs";
import * as path from "path";

const cfg     = new pulumi.Config();
const cfCfg   = new pulumi.Config("cloudflare");
const env     = cfg.get("env") ?? "dev";
const suffix  = cfg.get("workerNameSuffix") ?? "-dev";
const accountId = cfCfg.require("accountId");

interface RegionSpec {
  name: string;            // worker name (eg "nebula-us")
  region: string;          // label injected at runtime
  src: string;             // path to worker source
}

const regions: RegionSpec[] = [
  { name: "nebula-us",   region: "us-east",  src: "../../apps/edge-workers/us/index.js"   },
  { name: "nebula-eu",   region: "eu-west",  src: "../../apps/edge-workers/eu/index.js"   },
  { name: "nebula-apac", region: "ap-south", src: "../../apps/edge-workers/apac/index.js" },
];

// ── Cloudflare Workers ───────────────────────────────────────────────────────
export const workerUrls: Record<string, pulumi.Output<string>> = {};

for (const r of regions) {
  const workerName = `${r.name}${suffix}`;
  const srcPath    = path.resolve(__dirname, r.src);
  const content    = fs.readFileSync(srcPath, "utf8");

  const script = new cloudflare.WorkersScript(workerName, {
    accountId,
    scriptName: workerName,
    content,
    mainModule: "worker.js",
    bindings: [
      { type: "plain_text", name: "REGION", text: r.region },
      { type: "plain_text", name: "ENV",    text: env },
    ],
  });

  // Public *.workers.dev subdomain
  const sub = new cloudflare.WorkersDeployment(`${workerName}-deploy`, {
    accountId,
    scriptName: script.scriptName,
    strategy: "percentage",
    versions: [{ percentage: 100, versionId: script.id }],
  });

  workerUrls[r.region] = pulumi.interpolate`https://${script.scriptName}.${accountId}.workers.dev`;
}

// ── Fermyon Spin WASM app (built locally, deployed via spin CLI) ─────────────
// Pulumi command provider invokes `spin deploy` after the WASM is built.
const wasmApp = new command.local.Command("spin-deploy", {
  dir: path.resolve(__dirname, "../../apps/wasm-worker"),
  create: "spin build && spin deploy",
  update: "spin build && spin deploy",
  delete: "spin cloud apps delete nebulastream-worker || true",
  triggers: [
    fs.readFileSync(path.resolve(__dirname, "../../apps/wasm-worker/src/index.ts"), "utf8"),
  ],
});

export const wasmDeployStdout = wasmApp.stdout;

// ── Outputs ──────────────────────────────────────────────────────────────────
export const env_output = env;
export const accountId_output = accountId;
export const workers = pulumi.all(
  Object.fromEntries(Object.entries(workerUrls)),
);
