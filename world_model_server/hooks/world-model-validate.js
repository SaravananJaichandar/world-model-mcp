#!/usr/bin/env node
"use strict";
/**
 * World Model Validate Hook (PreToolUse)
 *
 * Validates proposed changes against the world model before execution.
 * Can block operations if they violate known constraints or would likely fail.
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
        // Only validate for Edit and Write tools
        if (data.tool_name !== 'Edit' && data.tool_name !== 'Write') {
            console.log(JSON.stringify({ success: true }));
            return;
        }
        const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
        const filePath = String(data.tool_input.file_path || '');
        const proposedContent = String(data.tool_input.new_string || data.tool_input.content || '');
        // Validate against world model
        const validation = await validateChange(filePath, proposedContent, projectDir);
        if (!validation.safe) {
            // Block the operation
            const output = {
                decision: 'block',
                reason: formatViolations(validation.violations, validation.suggestions),
            };
            console.log(JSON.stringify(output));
        }
        else {
            console.log(JSON.stringify({ success: true }));
        }
    }
    catch (error) {
        console.error('Error in world-model-validate:', error);
        // Don't block on errors
        console.log(JSON.stringify({ success: true }));
    }
}
async function validateChange(filePath, proposedContent, projectDir) {
    // Load constraints from world model
    const worldModelDir = path.join(projectDir, '.claude', 'world-model');
    const constraintsFile = path.join(worldModelDir, 'constraints.json');
    // Simple file-based constraints check
    // In production, this would call the MCP server
    if (!fs.existsSync(constraintsFile)) {
        return { safe: true, violations: [], suggestions: [] };
    }
    const constraintsData = fs.readFileSync(constraintsFile, 'utf-8');
    const constraints = JSON.parse(constraintsData);
    const violations = [];
    const suggestions = [];
    // Check each constraint
    for (const constraint of constraints) {
        if (fileMatchesPattern(filePath, constraint.file_pattern)) {
            if (violatesConstraint(proposedContent, constraint)) {
                violations.push({
                    rule: constraint.rule_name,
                    description: constraint.description,
                    severity: constraint.severity,
                });
                if (constraint.examples && constraint.examples.length > 0) {
                    const example = constraint.examples[0];
                    suggestions.push(`Use ${example.correct} instead of ${example.incorrect}`);
                }
            }
        }
    }
    return {
        safe: violations.length === 0,
        violations,
        suggestions,
    };
}
function fileMatchesPattern(filePath, pattern) {
    if (!pattern)
        return true;
    // Simple glob matching (can be enhanced with a proper library)
    const regex = pattern
        .replace(/\*\*/g, '.*')
        .replace(/\*/g, '[^/]*')
        .replace(/\./g, '\\.');
    return new RegExp(regex).test(filePath);
}
function violatesConstraint(content, constraint) {
    // Simple string matching
    if (constraint.rule_name === 'no-console' && content.includes('console.log')) {
        return true;
    }
    // Check examples for patterns
    if (constraint.examples) {
        for (const example of constraint.examples) {
            if (example.incorrect && content.includes(example.incorrect)) {
                return true;
            }
        }
    }
    return false;
}
function formatViolations(violations, suggestions) {
    let message = '⚠️  World Model: Constraint violations detected:\n\n';
    for (const v of violations) {
        message += `  - ${v.rule}: ${v.description}\n`;
    }
    if (suggestions.length > 0) {
        message += '\nSuggestions:\n';
        for (const s of suggestions) {
            message += `  • ${s}\n`;
        }
    }
    message += '\nPlease revise your code to comply with project constraints.';
    return message;
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
//# sourceMappingURL=world-model-validate.js.map