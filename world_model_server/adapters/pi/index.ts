/**
 * world-model-pi
 *
 * A pi-package extension that wires world-model-mcp into pi's extension
 * lifecycle. Spawns the existing Python helpers (hook_helper, inject_helper)
 * as subprocesses, so the constraint enforcement, auto-injection, and
 * compaction-audit logic stay in one place across Claude Code, Cursor, and pi.
 *
 * Pi's extension API exposes the events we need 1:1:
 *   tool_call       -> PreToolUse-equivalent. block: true + reason to deny.
 *   context         -> fires before every LLM call; replace messages to inject.
 *   session_compact -> fires after compaction; observe + record audit.
 *
 * Storage: writes to ~/.pi/agent/world-model/ by default. Override with
 * WORLD_MODEL_PI_DB to use a shared graph alongside Claude Code's .claude/
 * world-model/ directory.
 *
 * Pre-reqs in the host: world-model-mcp installed (`pip install
 * world-model-mcp`) and `python3` on PATH.
 */

import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

// pi exports these types from @earendil-works/pi-coding-agent at runtime;
// declare them as opaque here so the file compiles standalone.
type ExtensionAPI = {
  on: (event: string, handler: (event: any, ctx: any) => any) => void;
  registerCommand?: (
    name: string,
    spec: { description: string; handler: (args: any, ctx: any) => any }
  ) => void;
};

const DEFAULT_DB =
  process.env.WORLD_MODEL_PI_DB ?? join(homedir(), ".pi", "agent", "world-model");

try {
  mkdirSync(DEFAULT_DB, { recursive: true });
} catch {
  // dir creation is best-effort; helpers will surface real errors later
}

type HookOutput = {
  hookSpecificOutput?: {
    permissionDecision?: "deny" | "ask" | "defer" | "allow";
    permissionDecisionReason?: string;
  };
};

async function callPython(module: string, payload: object): Promise<any> {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    const proc = spawn("python3", ["-m", module], {
      env: {
        ...process.env,
        WORLD_MODEL_DB_PATH: DEFAULT_DB,
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    proc.stdout.on("data", (chunk) => (stdout += chunk));
    proc.stderr.on("data", (chunk) => (stderr += chunk));
    proc.on("error", () => resolve({}));
    proc.on("close", (code) => {
      if (code !== 0) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(stdout || "{}"));
      } catch {
        resolve({});
      }
    });
    proc.stdin.write(JSON.stringify(payload));
    proc.stdin.end();
    setTimeout(() => {
      try {
        proc.kill();
      } catch {
        // noop
      }
    }, 5000);
  });
}

export default async function (pi: ExtensionAPI): Promise<void> {
  // 1. PreToolUse-style enforcement via tool_call.
  pi.on("tool_call", async (event: any) => {
    const input = event?.input;
    if (!input) return;
    const filePath = input.path ?? input.file_path ?? input.target;
    const content =
      input.new_string ?? input.content ?? input.text ?? input.command;
    if (!filePath || !content) return;

    const out: HookOutput = await callPython("world_model_server.hook_helper", {
      tool_name: event.tool_name ?? event.tool ?? "Edit",
      tool_input: { file_path: filePath, new_string: content },
      project_dir: process.cwd(),
      supports_defer: true,
    });
    const decision = out?.hookSpecificOutput?.permissionDecision;
    const reason =
      out?.hookSpecificOutput?.permissionDecisionReason ?? "world-model constraint";
    if (decision === "deny") {
      return { block: true, reason };
    }
    if (decision === "defer" || decision === "ask") {
      // pi has no defer tier; surface as an advisory block with a clear reason.
      return { block: true, reason: `[review] ${reason}` };
    }
    return undefined;
  });

  // 2. Auto-injection on every LLM call via the `context` event.
  pi.on("context", async (event: any) => {
    if (!event?.messages) return;
    const lastUser = [...event.messages]
      .reverse()
      .find((m: any) => m?.role === "user");
    const userPrompt =
      typeof lastUser?.content === "string"
        ? lastUser.content
        : Array.isArray(lastUser?.content)
        ? lastUser.content
            .map((c: any) => c?.text ?? "")
            .join(" ")
        : "";

    const inj = await callPython("world_model_server.inject_helper", {
      event: "UserPromptSubmit",
      project_dir: process.cwd(),
      user_prompt: userPrompt,
      max_constraints: 8,
      max_facts: 8,
    });
    const additional = inj?.hookSpecificOutput?.additionalContext;
    if (!additional) return;
    return {
      messages: [
        {
          role: "system",
          content: [
            {
              type: "text",
              text: `## Memory (from world-model-mcp)\n${additional}`,
            },
          ],
          timestamp: Date.now(),
        },
        ...event.messages,
      ],
    };
  });

  // 3. Compaction audit on session_compact.
  pi.on("session_compact", async (event: any) => {
    const entry = event?.compactionEntry ?? {};
    await callPython("world_model_server.inject_helper", {
      event: "PostCompact",
      project_dir: process.cwd(),
      session_id: entry.id ?? null,
      pre_compact_tokens: entry.tokensBefore ?? null,
      post_compact_tokens: null,
    });
  });

  // Optional: surface a slash command for status.
  if (pi.registerCommand) {
    pi.registerCommand("wm-status", {
      description: "Show world-model status (db path + opt-in telemetry state)",
      handler: async (_args: any, ctx: any) => {
        ctx?.ui?.notify?.(`world-model db: ${DEFAULT_DB}`, "info");
      },
    });
  }
}
