#!/usr/bin/env node
"use strict";
/**
 * World Model Session Hook (SessionStart/SessionEnd)
 *
 * Manages session lifecycle in the world model.
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
        const action = process.argv[2]; // 'start' or 'end'
        if (!action) {
            console.error('Usage: world-model-session.ts [start|end]');
            process.exit(1);
        }
        // Read input from stdin
        const input = await readStdin();
        const data = JSON.parse(input);
        const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
        if (action === 'start') {
            await handleSessionStart(data, projectDir);
        }
        else if (action === 'end') {
            await handleSessionEnd(data, projectDir);
        }
        console.log(JSON.stringify({ success: true }));
    }
    catch (error) {
        console.error('Error in world-model-session:', error);
        console.log(JSON.stringify({ success: false, error: String(error) }));
    }
}
async function handleSessionStart(data, projectDir) {
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
async function handleSessionEnd(data, projectDir) {
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
async function readStdin() {
    return new Promise((resolve, reject) => {
        let data = '';
        process.stdin.on('data', (chunk) => (data += chunk));
        process.stdin.on('end', () => resolve(data));
        process.stdin.on('error', reject);
    });
}
main();
//# sourceMappingURL=world-model-session.js.map