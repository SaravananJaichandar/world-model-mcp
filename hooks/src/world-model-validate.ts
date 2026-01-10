#!/usr/bin/env node
/**
 * World Model Validate Hook (PreToolUse)
 *
 * Validates proposed changes against the world model before execution.
 * Can block operations if they violate known constraints or would likely fail.
 */

import * as fs from 'fs';
import * as path from 'path';

interface PreToolUseInput {
  tool_name: string;
  tool_input: Record<string, unknown>;
  session_id: string;
}

interface HookOutput {
  decision?: 'block';
  reason?: string;
}

async function main() {
  try {
    // Read input from stdin
    const input = await readStdin();
    const data: PreToolUseInput = JSON.parse(input);

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
      const output: HookOutput = {
        decision: 'block',
        reason: formatViolations(validation.violations, validation.suggestions),
      };
      console.log(JSON.stringify(output));
    } else {
      console.log(JSON.stringify({ success: true }));
    }
  } catch (error) {
    console.error('Error in world-model-validate:', error);
    // Don't block on errors
    console.log(JSON.stringify({ success: true }));
  }
}

async function validateChange(
  filePath: string,
  proposedContent: string,
  projectDir: string
): Promise<{ safe: boolean; violations: any[]; suggestions: string[] }> {
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

  const violations: any[] = [];
  const suggestions: string[] = [];

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

function fileMatchesPattern(filePath: string, pattern: string | null): boolean {
  if (!pattern) return true;

  // Simple glob matching (can be enhanced with a proper library)
  const regex = pattern
    .replace(/\*\*/g, '.*')
    .replace(/\*/g, '[^/]*')
    .replace(/\./g, '\\.');

  return new RegExp(regex).test(filePath);
}

function violatesConstraint(content: string, constraint: any): boolean {
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

function formatViolations(violations: any[], suggestions: string[]): string {
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

async function readStdin(): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.on('data', (chunk) => (data += chunk));
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', reject);
  });
}

main();
