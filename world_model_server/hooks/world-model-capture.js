#!/usr/bin/env node
"use strict";
/**
 * World Model Capture Hook (PostToolUse)
 *
 * Captures file edits, test runs, and other tool calls to record in the world model.
 * Runs after Claude Code executes a tool.
 */
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
async function main() {
    try {
        // Read input from stdin
        const input = await readStdin();
        const data = JSON.parse(input);
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
    }
    catch (error) {
        console.error('Error in world-model-capture:', error);
        console.log(JSON.stringify({ success: false, error: String(error) }));
    }
}
function mapToolToEventType(toolName) {
    const mapping = {
        Edit: 'file_edit',
        Write: 'file_create',
        Bash: 'tool_call',
        Read: 'tool_call',
    };
    return mapping[toolName] || 'tool_call';
}
function extractEntities(data) {
    const entities = [];
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
function extractReasoning(data) {
    // Try to extract reasoning from tool input (if Claude provided it)
    if (data.tool_input.reasoning) {
        return String(data.tool_input.reasoning);
    }
    return undefined;
}
async function recordEvent(event, projectDir) {
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
async function readStdin() {
    return new Promise((resolve, reject) => {
        let data = '';
        process.stdin.on('data', (chunk) => (data += chunk));
        process.stdin.on('end', () => resolve(data));
        process.stdin.on('error', reject);
    });
}
main();
//# sourceMappingURL=world-model-capture.js.map