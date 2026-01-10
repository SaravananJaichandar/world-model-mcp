#!/usr/bin/env node
/**
 * World Model Capture Hook (PostToolUse)
 *
 * Captures file edits, test runs, and other tool calls to record in the world model.
 * Runs after Claude Code executes a tool.
 */

import * as fs from 'fs';
import * as path from 'path';

interface PostToolUseInput {
  tool_name: string;
  tool_input: Record<string, unknown>;
  tool_response: {
    filePath?: string;
    file_path?: string;
    content?: string;
    [key: string]: unknown;
  };
}

async function main() {
  try {
    // Read input from stdin
    const input = await readStdin();
    const data: PostToolUseInput = JSON.parse(input);

    // Extract session ID from environment or generate one
    const sessionId = process.env.CLAUDE_SESSION_ID || 'unknown';
    const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();

    // Prepare event data
    const event = {
      event_type: mapToolToEventType(data.tool_name),
      session_id: sessionId,
      entities: extractEntities(data),
      description: `${data.tool_name} executed`,
      reasoning: extractReasoning(data),
      evidence: {
        tool_name: data.tool_name,
        tool_input: data.tool_input,
        tool_output: data.tool_response,
      },
      success: true,
    };

    // Call world model MCP server
    await recordEvent(event, projectDir);

    // Return success (no blocking)
    console.log(JSON.stringify({ success: true }));
  } catch (error) {
    console.error('Error in world-model-capture:', error);
    console.log(JSON.stringify({ success: false, error: String(error) }));
  }
}

function mapToolToEventType(toolName: string): string {
  const mapping: Record<string, string> = {
    Edit: 'file_edit',
    Write: 'file_create',
    Bash: 'tool_call',
    Read: 'tool_call',
  };
  return mapping[toolName] || 'tool_call';
}

function extractEntities(data: PostToolUseInput): string[] {
  const entities: string[] = [];

  // Extract file paths
  const filePath = data.tool_response.filePath || data.tool_response.file_path;
  if (filePath) {
    entities.push(String(filePath));
  }

  // Extract from tool input
  if (data.tool_input.file_path) {
    entities.push(String(data.tool_input.file_path));
  }

  return entities;
}

function extractReasoning(data: PostToolUseInput): string | undefined {
  // Try to extract reasoning from tool input (if Claude provided it)
  if (data.tool_input.reasoning) {
    return String(data.tool_input.reasoning);
  }
  return undefined;
}

async function recordEvent(event: any, projectDir: string) {
  // In a real implementation, this would call the MCP server
  // For now, we'll write to a temporary file that can be processed later

  const worldModelDir = path.join(projectDir, '.claude', 'world-model');
  const eventsQueue = path.join(worldModelDir, 'events-queue.jsonl');

  // Ensure directory exists
  if (!fs.existsSync(worldModelDir)) {
    fs.mkdirSync(worldModelDir, { recursive: true });
  }

  // Append event to queue
  fs.appendFileSync(eventsQueue, JSON.stringify(event) + '\n');
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
