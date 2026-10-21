// Pi lifecycle adapter for agentic-scaffold.
//
// Source contracts:
// - Pi extension API: https://pi.dev/docs/extensions (retrieved 2026-10-05,
//   MIT repository documentation)
// - pi-subagents 0.76.1: https://github.com/nicobailon/pi-subagents
//   (retrieved 2026-10-05, MIT). It is configured separately in
//   .pi/settings.json; this adapter never selects a provider or model.
//
// The shell scripts under .agentic/hooks and .agentic/toolchain.sh remain the
// policy authority. This file only translates Pi lifecycle events into their
// existing payloads and result semantics.

import { spawn } from "node:child_process";
import { access, readFile } from "node:fs/promises";
import path from "node:path";

const MAX_OUTPUT = 1024 * 1024;
const SENSITIVE_PATH = /(^|\/)(\.env(?:\.[^/]*)?|[^/]+\.(?:pem|key))$/i;
const SENSITIVE_COMMAND = /(^|[\s"'])(?:\.\/?|[^\s"']*\/)?(?:\.env(?:\.[^\s"']*)?|[^\s"']+\.(?:pem|key))(?=$|[\s"';&|<>])/i;
const SENSITIVE_GREP_RESULT = /(^|\n)(?:\.\/)?(?:[^:\n]*\/)?(?:\.env(?:\.[^/:\n]*)?|[^/:\n]+\.(?:pem|key))(?=[:-]\d+[:-])/i;

function textContent(text) {
  return [{ type: "text", text }];
}

async function exists(file) {
  try {
    await access(file);
    return true;
  } catch {
    return false;
  }
}

async function projectRoot(cwd) {
  if (await exists(path.join(cwd, ".agentic"))) return cwd;
  const result = await run("git", ["rev-parse", "--show-toplevel"], cwd, "");
  return result.code === 0 && result.stdout.trim() ? result.stdout.trim() : cwd;
}

function run(command, args, cwd, input) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let truncated = false;

    const collect = (current, chunk) => {
      if (current.length >= MAX_OUTPUT) {
        truncated = true;
        return current;
      }
      const next = current + chunk.toString();
      if (next.length <= MAX_OUTPUT) return next;
      truncated = true;
      return next.slice(0, MAX_OUTPUT);
    };

    child.stdout.on("data", (chunk) => { stdout = collect(stdout, chunk); });
    child.stderr.on("data", (chunk) => { stderr = collect(stderr, chunk); });
    child.on("error", (error) => resolve({ code: 127, stdout, stderr: `${stderr}${error.message}` }));
    child.on("close", (code) => {
      const suffix = truncated ? "\n[agentic-scaffold: hook output truncated]\n" : "";
      resolve({ code: code ?? 1, stdout: stdout + suffix, stderr: stderr + suffix });
    });
    child.stdin.end(`${input}\n`);
  });
}

async function runHook(root, name, args = [], payload = {}) {
  const script = path.join(root, ".agentic", "hooks", name);
  if (!(await exists(script))) return { missing: true, code: 0, stdout: "", stderr: "" };
  const result = await run("bash", [script, ...args], root, JSON.stringify(payload));
  return { missing: false, ...result };
}

async function hookMode(root) {
  const state = path.join(root, ".agentic", "scaffold-state");
  try {
    const content = await readFile(state, "utf8");
    return {
      strict: /^STRICT_HOOKS=1$/m.test(content),
      noStop: /^NO_STOP_GATE=1$/m.test(content),
    };
  } catch {
    return { strict: false, noStop: false };
  }
}

function appendHookFailure(event, label, result) {
  const detail = [result.stderr.trim(), result.stdout.trim()].filter(Boolean).join("\n");
  const suffix = `\n\n[agentic-scaffold ${label} failed${detail ? `]\n${detail}` : "]"}`;
  const content = [...event.content];
  const last = content.at(-1);
  if (last?.type === "text") {
    content[content.length - 1] = { ...last, text: `${last.text}${suffix}` };
  } else {
    content.push(...textContent(suffix.trimStart()));
  }
  return { content, isError: true };
}

export function registerAgenticHooks(pi, { parentLifecycle = true } = {}) {
  let stopContinuationUsed = false;

  if (parentLifecycle) pi.on("session_start", async (_event, ctx) => {
    const root = await projectRoot(ctx.cwd);
    const result = await runHook(root, "branch-check.sh");
    const message = [result.stdout.trim(), result.stderr.trim()].filter(Boolean).join("\n");
    if (message && ctx.hasUI) ctx.ui.notify(message, result.code === 0 ? "warning" : "error");
  });

  if (parentLifecycle) pi.on("input", async (event) => {
    if (event.source === "interactive" || event.source === "rpc") {
      stopContinuationUsed = false;
    }
  });

  pi.on("user_bash", async (event, ctx) => {
    const root = await projectRoot(ctx.cwd);
    if (SENSITIVE_COMMAND.test(event.command)) {
      return {
        result: {
          output: "Sensitive-file shell access denied by agentic-scaffold.",
          exitCode: 2,
          cancelled: false,
          truncated: false,
        },
      };
    }
    const result = await runHook(root, "block-destructive.sh", [], {
      cwd: root,
      tool_name: "Bash",
      tool_input: { command: event.command },
    });
    if (result.code === 0) return undefined;
    return {
      result: {
        output: result.stderr.trim() || result.stdout.trim() || "Blocked by agentic-scaffold destructive-command guard.",
        exitCode: result.code === 2 ? 2 : 1,
        cancelled: false,
        truncated: false,
      },
    };
  });

  pi.on("tool_call", async (event, ctx) => {
    const root = await projectRoot(ctx.cwd);
    const candidatePath = typeof event.input?.path === "string" ? event.input.path : "";
    if (["read", "edit", "write", "grep"].includes(event.toolName) && SENSITIVE_PATH.test(candidatePath)) {
      return { block: true, reason: `Sensitive path denied by agentic-scaffold: ${candidatePath}` };
    }

    if (event.toolName !== "bash") return undefined;
    const command = typeof event.input?.command === "string" ? event.input.command : "";
    if (SENSITIVE_COMMAND.test(command)) {
      return { block: true, reason: "Sensitive-file shell access denied by agentic-scaffold." };
    }

    const result = await runHook(root, "block-destructive.sh", [], {
      cwd: root,
      tool_name: "Bash",
      tool_input: { command },
    });
    if (result.code !== 0) {
      return {
        block: true,
        reason: result.stderr.trim() || result.stdout.trim() ||
          (result.code === 2
            ? "Blocked by agentic-scaffold destructive-command guard."
            : "agentic-scaffold destructive-command guard failed closed."),
      };
    }
    return undefined;
  });

  pi.on("tool_result", async (event, ctx) => {
    if (event.isError) return undefined;
    if (event.toolName === "grep") {
      const leakedPath = event.content.some(
        (item) => item.type === "text" && SENSITIVE_GREP_RESULT.test(item.text),
      );
      if (leakedPath) {
        return {
          content: textContent("Sensitive-path grep result withheld by agentic-scaffold."),
          isError: true,
        };
      }
      return undefined;
    }
    if (!["edit", "write"].includes(event.toolName)) return undefined;
    const root = await projectRoot(ctx.cwd);
    const mode = await hookMode(root);
    const payload = {
      cwd: root,
      tool_name: event.toolName === "edit" ? "Edit" : "Write",
      tool_input: { file_path: event.input?.path ?? "" },
    };

    const formatArgs = mode.strict ? ["--strict"] : [];
    const format = await runHook(root, "format-after-edit.sh", formatArgs, payload);
    const status = await runHook(root, "specs-status.sh", ["--hook"], payload);
    if (format.code !== 0) return appendHookFailure(event, "format-after-edit", format);
    if (status.code !== 0) return appendHookFailure(event, "specs-status", status);
    return undefined;
  });

  if (parentLifecycle) pi.on("agent_before_settle", async (event, ctx) => {
    if (event.outcome !== "completed") return undefined;
    const root = await projectRoot(ctx.cwd);
    const mode = await hookMode(root);
    if (mode.noStop || !(await exists(path.join(root, ".agentic", "hooks", "gate-on-stop.sh")))) {
      return undefined;
    }

    // Pi has no stop_hook_active event field. Always ask the canonical hook
    // for the current gate decision, then enforce the one-retry limit here.
    const result = await runHook(root, "gate-on-stop.sh", [], {
      stop_hook_active: false,
    });
    const output = result.stdout.trim();
    let decision;
    if (!output && result.code === 0) return undefined;
    if (!output) {
      decision = {
        decision: "block",
        reason: result.stderr.trim() || "agentic-scaffold Stop hook failed; inspect .agentic/hooks/gate-on-stop.sh.",
      };
    } else {
      try {
        decision = JSON.parse(output);
      } catch {
        decision = {
          decision: "block",
          reason: `agentic-scaffold Stop hook returned invalid JSON: ${result.stderr.trim() || output}`,
        };
      }
    }
    if (decision?.decision !== "block" || !decision.reason) return undefined;

    if (stopContinuationUsed) {
      if (ctx.hasUI) ctx.ui.notify(decision.reason, "error");
      return undefined;
    }

    stopContinuationUsed = true;
    return {
      entries: [
        ...event.entries,
        {
          type: "custom_message",
          customType: "agentic-scaffold-gate",
          content: decision.reason,
          display: true,
        },
      ],
      continue: true,
    };
  });

  if (parentLifecycle) pi.on("turn_end", async (_event, ctx) => {
    if (ctx.mode !== "tui") return;
    const root = await projectRoot(ctx.cwd);
    const branch = await run("git", ["branch", "--show-current"], root, "");
    const model = ctx.model ? `${ctx.model.provider}/${ctx.model.id}` : "no model";
    const usage = ctx.getContextUsage();
    const context = usage?.percent == null ? "ctx -" : `ctx ${Math.round(usage.percent)}%`;
    ctx.ui.setStatus("agentic-scaffold", `${branch.stdout.trim() || "(no branch)"} · ${model} · ${context}`);
  });
}

export default function agenticScaffold(pi) {
  registerAgenticHooks(pi);
}
