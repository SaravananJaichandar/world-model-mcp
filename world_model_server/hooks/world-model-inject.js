#!/usr/bin/env node
"use strict";
/**
 * World Model Inject Hook (PostCompact / UserPromptSubmit / SessionStart)
 *
 * Spawns the Python inject_helper as a subprocess, passing the hook payload
 * (annotated with the event name and project dir) and emitting whatever JSON
 * the helper returns. Fails open: on any error, emits {} so the agent
 * continues without injection.
 */
Object.defineProperty(exports, "__esModule", { value: true });
const child_process_1 = require("child_process");
async function readStdin() {
    return new Promise((resolve, reject) => {
        let data = '';
        process.stdin.on('data', (chunk) => (data += chunk));
        process.stdin.on('end', () => resolve(data));
        process.stdin.on('error', reject);
    });
}
function detectEvent(input, argvEvent) {
    const raw = (argvEvent || input.hook_event_name || '');
    if (raw === 'PostCompact' || raw === 'UserPromptSubmit' || raw === 'SessionStart') {
        return raw;
    }
    return null;
}
async function main() {
    try {
        const raw = await readStdin();
        if (!raw.trim()) {
            process.stdout.write('{}');
            return;
        }
        const input = JSON.parse(raw);
        // The event name comes either from the payload (per modern hook contract)
        // or from argv[2] (legacy). The Node wrapper accepts either.
        const argvEvent = process.argv[2];
        const event = detectEvent(input, argvEvent);
        if (!event) {
            process.stdout.write('{}');
            return;
        }
        const projectDir = process.env.CLAUDE_PROJECT_DIR || input.cwd || process.cwd();
        const payload = {
            event,
            project_dir: projectDir,
            session_id: input.session_id,
            user_prompt: input.prompt || '',
            pre_compact_tokens: input.pre_compact_tokens,
            post_compact_tokens: input.post_compact_tokens,
        };
        const child = (0, child_process_1.spawn)('python3', ['-m', 'world_model_server.inject_helper'], {
            stdio: ['pipe', 'pipe', 'inherit'],
        });
        let out = '';
        child.stdout.on('data', (chunk) => (out += chunk));
        child.on('close', () => {
            process.stdout.write(out.trim() || '{}');
        });
        child.on('error', () => {
            process.stdout.write('{}');
        });
        child.stdin.write(JSON.stringify(payload));
        child.stdin.end();
        // Give the helper up to 5s, then bail
        setTimeout(() => {
            try {
                child.kill();
            }
            catch { /* noop */ }
        }, 5000);
    }
    catch (err) {
        process.stdout.write('{}');
    }
}
main();
//# sourceMappingURL=world-model-inject.js.map