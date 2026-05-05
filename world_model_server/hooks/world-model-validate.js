#!/usr/bin/env node
"use strict";
/**
 * World Model Validate Hook (PreToolUse) — v0.6.0
 *
 * Spawns the Python hook_helper subprocess, which queries the world model
 * SQLite databases (read-only) and classifies the proposed change as
 * deny/ask/allow based on constraint violations and severity history.
 *
 * Fails open: any error returns empty {} (allow) so hook never blocks
 * legitimate edits when the world model is unavailable.
 */
const { spawn } = require("child_process");

async function readStdin() {
    return new Promise((resolve, reject) => {
        let data = "";
        process.stdin.on("data", (chunk) => (data += chunk));
        process.stdin.on("end", () => resolve(data));
        process.stdin.on("error", reject);
    });
}

async function callHookHelper(payload) {
    return new Promise((resolve) => {
        let stdoutBuf = "";
        let stderrBuf = "";
        const proc = spawn("python3", ["-m", "world_model_server.hook_helper"], {
            stdio: ["pipe", "pipe", "pipe"],
        });

        const timer = setTimeout(() => {
            proc.kill();
            resolve({});
        }, 5000);

        proc.stdout.on("data", (chunk) => (stdoutBuf += chunk));
        proc.stderr.on("data", (chunk) => (stderrBuf += chunk));
        proc.on("close", (code) => {
            clearTimeout(timer);
            if (code !== 0) {
                resolve({});
                return;
            }
            try {
                resolve(JSON.parse(stdoutBuf || "{}"));
            } catch {
                resolve({});
            }
        });
        proc.on("error", () => {
            clearTimeout(timer);
            resolve({});
        });

        try {
            proc.stdin.write(JSON.stringify(payload));
            proc.stdin.end();
        } catch {
            clearTimeout(timer);
            resolve({});
        }
    });
}

async function main() {
    try {
        const input = await readStdin();
        if (!input.trim()) {
            console.log("{}");
            return;
        }
        const data = JSON.parse(input);

        // Only validate Edit/Write
        const toolName = data.tool_name;
        if (toolName !== "Edit" && toolName !== "Write" && toolName !== "MultiEdit") {
            console.log("{}");
            return;
        }

        const payload = {
            tool_name: toolName,
            tool_input: data.tool_input || {},
            project_dir: process.env.CLAUDE_PROJECT_DIR || process.cwd(),
            session_id: process.env.CLAUDE_SESSION_ID || data.session_id || "unknown",
        };

        const result = await callHookHelper(payload);
        console.log(JSON.stringify(result));
    } catch (error) {
        // Fail open
        console.log("{}");
    }
}

main();
