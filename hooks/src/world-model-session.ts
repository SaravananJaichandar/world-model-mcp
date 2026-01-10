#!/usr/bin/env node
/**
 * World Model Session Hook (SessionStart/SessionEnd)
 *
 * Manages session lifecycle in the world model.
 */

import * as fs from 'fs';
import * as path from 'path';

interface SessionInput {
  session_id: string;
  source: 'startup' | 'resume' | 'clear' | 'compact';
  transcript_path?: string;
  cwd?: string;
}

async function main() {
  try {
    const action = process.argv[2]; // 'start' or 'end'

    if (!action) {
      console.error('Usage: world-model-session.ts [start|end]');
      process.exit(1);
    }

    // Read input from stdin
    const input = await readStdin();
    const data: SessionInput = JSON.parse(input);

    const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();

    if (action === 'start') {
      await handleSessionStart(data, projectDir);
    } else if (action === 'end') {
      await handleSessionEnd(data, projectDir);
    }

    console.log(JSON.stringify({ success: true }));
  } catch (error) {
    console.error('Error in world-model-session:', error);
    console.log(JSON.stringify({ success: false, error: String(error) }));
  }
}

async function handleSessionStart(data: SessionInput, projectDir: string) {
  const worldModelDir = path.join(projectDir, '.claude', 'world-model');

  // Ensure directory exists
  if (!fs.existsSync(worldModelDir)) {
    fs.mkdirSync(worldModelDir, { recursive: true });
  }

  // Create session metadata file
  const sessionMeta = {
    session_id: data.session_id,
    started_at: new Date().toISOString(),
    source: data.source,
    transcript_path: data.transcript_path,
  };

  const sessionFile = path.join(worldModelDir, `session-${data.session_id}.json`);
  fs.writeFileSync(sessionFile, JSON.stringify(sessionMeta, null, 2));
}

async function handleSessionEnd(data: SessionInput, projectDir: string) {
  const worldModelDir = path.join(projectDir, '.claude', 'world-model');
  const sessionFile = path.join(worldModelDir, `session-${data.session_id}.json`);

  if (fs.existsSync(sessionFile)) {
    const sessionData = JSON.parse(fs.readFileSync(sessionFile, 'utf-8'));
    sessionData.ended_at = new Date().toISOString();

    // Process queued events
    const eventsQueue = path.join(worldModelDir, 'events-queue.jsonl');
    if (fs.existsSync(eventsQueue)) {
      // Read all events
      const events = fs
        .readFileSync(eventsQueue, 'utf-8')
        .split('\n')
        .filter((line) => line.trim())
        .map((line) => JSON.parse(line));

      sessionData.events_count = events.length;
      sessionData.outcome = 'success'; // Can be enhanced with failure detection

      // Clear queue
      fs.unlinkSync(eventsQueue);
    }

    fs.writeFileSync(sessionFile, JSON.stringify(sessionData, null, 2));
  }
}

async function readStdin(): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.on('data', (chunk) => (data += chunk));
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', reject);
  });
}

main();
