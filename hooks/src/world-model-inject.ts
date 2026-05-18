#!/usr/bin/env node
/**
 * World Model Inject Hook (PostCompact / UserPromptSubmit / SessionStart)
 *
 * Spawns the Python inject_helper as a subprocess, passing the hook payload
 * (annotated with the event name and project dir) and emitting whatever JSON
 * the helper returns. Fails open: on any error, emits {} so the agent
 * continues without injection.
 */

import { spawn } from 'child_process';

type EventName = 'PostCompact' | 'UserPromptSubmit' | 'SessionStart';

interface HookInput {
  hook_event_name?: string;
  session_id?: string;
  cwd?: string;
  prompt?: string;
  pre_compact_tokens?: number;
  post_compact_tokens?: number;
  // Older payload shapes may use different keys; we forward what we have.
  [k: string]: unknown;
}

async function readStdin(): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.on('data', (chunk) => (data += chunk));
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', reject);
  });
}

function detectEvent(input: HookInput, argvEvent?: string): EventName | null {
  const raw = (argvEvent || input.hook_event_name || '') as string;
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
    const input = JSON.parse(raw) as HookInput;

    // The event name comes either from the payload (per modern hook contract)
    // or from argv[2] (legacy). The Node wrapper accepts either.
    const argvEvent = process.argv[2];
    const event = detectEvent(input, argvEvent);
    if (!event) {
      process.stdout.write('{}');
      return;
    }

    const projectDir = process.env.CLAUDE_PROJECT_DIR || (input.cwd as string) || process.cwd();

    const payload = {
      event,
      project_dir: projectDir,
      session_id: input.session_id,
      user_prompt: input.prompt || '',
      pre_compact_tokens: input.pre_compact_tokens,
      post_compact_tokens: input.post_compact_tokens,
    };

    const child = spawn('python3', ['-m', 'world_model_server.inject_helper'], {
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
      try { child.kill(); } catch { /* noop */ }
    }, 5000);
  } catch (err) {
    process.stdout.write('{}');
  }
}

main();
