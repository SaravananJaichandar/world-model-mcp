# Contributing to World Model MCP

Thank you for your interest in contributing to World Model MCP! This document provides guidelines and instructions for contributing.

## 🌟 Ways to Contribute

- **Report Bugs**: Open an issue describing the bug and how to reproduce it
- **Suggest Features**: Share ideas for new features or improvements
- **Improve Documentation**: Help us make the docs clearer and more comprehensive
- **Write Code**: Fix bugs, implement features, or improve performance
- **Add Language Support**: Implement extraction patterns for new programming languages
- **Share Use Cases**: Tell us how you're using the world model

## 🚀 Getting Started

### 1. Fork and Clone

```bash
# Fork the repository on GitHub, then clone your fork
git clone https://github.com/SaravananJaichandar/world-model-mcp.git
cd world-model-mcp

# Add upstream remote
git remote add upstream https://github.com/anthropics/world-model-mcp.git
```

### 2. Set Up Development Environment

```bash
# Install Python dependencies (including dev tools)
pip install -e ".[dev]"

# Install pre-commit hooks (optional but recommended)
pip install pre-commit
pre-commit install

# Build TypeScript hooks
cd hooks
npm install
npm run build
cd ..
```

### 3. Create a Branch

```bash
git checkout -b feature/your-feature-name
# or
git checkout -b fix/your-bug-fix
```

## 📝 Development Workflow

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=world_model_server --cov-report=html

# Run specific test file
pytest tests/test_knowledge_graph.py

# Run tests in watch mode
pytest-watch
```

### Code Quality

We use several tools to maintain code quality:

```bash
# Format code with Black
black world_model_server tests

# Lint with Ruff
ruff check world_model_server tests

# Type check with mypy
mypy world_model_server

# Run all quality checks
black . && ruff check . && mypy world_model_server
```

### Building TypeScript Hooks

```bash
cd hooks
npm run build    # Compile TypeScript
npm run watch    # Watch mode for development
```

## 🎯 Coding Standards

### Python Code

- **Style**: Follow [PEP 8](https://pep8.org/), enforced by Black (line length: 100)
- **Type Hints**: Use type hints for all function signatures
- **Docstrings**: Use Google-style docstrings for all public functions
- **Imports**: Use absolute imports, sorted with isort

Example:
```python
from typing import List, Optional
from .models import Entity, Fact

async def extract_entities(
    file_path: str,
    content: str,
    reasoning: Optional[str] = None
) -> List[Entity]:
    """
    Extract entities from code content.

    Args:
        file_path: Path to the file
        content: File content to analyze
        reasoning: Optional reasoning for the change

    Returns:
        List of extracted entities
    """
    # Implementation here
    pass
```

### TypeScript Code

- **Style**: Follow project `.prettierrc` and `eslint.config.js`
- **Types**: Use explicit types, avoid `any` unless absolutely necessary
- **Async**: Prefer `async/await` over `.then()` chains

Example:
```typescript
async function validateChange(
  filePath: string,
  content: string
): Promise<ValidationResult> {
  // Implementation here
}
```

## 🧪 Writing Tests

### Test Structure

- Place tests in `tests/` directory
- Name test files `test_*.py`
- Use descriptive test function names: `test_should_extract_api_entities()`

### Test Example

```python
import pytest
from world_model_server.extraction import EntityExtractor

@pytest.mark.asyncio
async def test_should_extract_typescript_functions():
    """Test extraction of TypeScript function declarations."""
    extractor = EntityExtractor(config)

    content = '''
    export function getUserById(id: string): Promise<User> {
        return db.findOne({id});
    }
    '''

    entities, facts = await extractor.extract_from_file_edit(
        file_path="src/api/users.ts",
        old_content="",
        new_content=content
    )

    assert len(entities) > 0
    assert entities[0].entity_type == "function"
    assert entities[0].name == "getUserById"
```

## 📚 Adding Language Support

To add support for a new programming language:

1. **Add detection** in `extraction.py`:
```python
def _detect_language(self, file_path: str) -> str:
    if file_path.endswith(".go"):
        return "go"
    # ... existing code
```

2. **Add extraction patterns**:
```python
def _extract_go_entities(self, file_path: str, content: str, diff: str) -> List[Entity]:
    # Function pattern
    func_pattern = r"func\s+(\w+)\s*\((.*?)\)"
    for match in re.finditer(func_pattern, diff):
        entities.append(Entity(...))

    return entities
```

3. **Add tests** in `tests/test_extraction.py`:
```python
async def test_extract_go_entities():
    # Test your Go extraction patterns
    pass
```

4. **Update documentation** in README.md

## 🐛 Reporting Bugs

### Before Reporting

- Check if the bug has already been reported
- Try to reproduce with the latest version
- Gather system information (Python version, OS, Claude Code version)

### Bug Report Template

```markdown
**Describe the bug**
A clear description of what the bug is.

**To Reproduce**
Steps to reproduce the behavior:
1. Set up world model in project X
2. Edit file Y
3. Observe error Z

**Expected behavior**
What you expected to happen.

**Actual behavior**
What actually happened.

**Environment**
- OS: [e.g., macOS 14.1]
- Python version: [e.g., 3.11.5]
- World Model MCP version: [e.g., 0.1.0]
- Claude Code version: [e.g., 2.1.4]

**Logs**
```
Paste relevant logs here
```

**Additional context**
Any other context about the problem.
```

## 💡 Suggesting Features

### Feature Request Template

```markdown
**Feature Description**
A clear description of the feature you'd like to see.

**Use Case**
Why would this feature be useful? What problem does it solve?

**Proposed Solution**
How do you think this should work?

**Alternatives Considered**
What alternatives have you considered?

**Additional Context**
Any other relevant information.
```

## 🔄 Pull Request Process

### Before Submitting

1. **Update from upstream**:
```bash
git fetch upstream
git rebase upstream/main
```

2. **Run all checks**:
```bash
pytest
black .
ruff check .
mypy world_model_server
```

3. **Update documentation** if needed

4. **Add tests** for new features

### PR Template

```markdown
**Description**
Brief description of what this PR does.

**Motivation**
Why is this change needed?

**Changes**
- Added: ...
- Changed: ...
- Fixed: ...

**Testing**
- [ ] Added tests for new functionality
- [ ] All existing tests pass
- [ ] Manually tested in Claude Code

**Checklist**
- [ ] Code follows project style guidelines
- [ ] Tests added and passing
- [ ] Documentation updated
- [ ] Commit messages are descriptive
```

### Review Process

1. A maintainer will review your PR within 3-5 business days
2. Address any feedback or requested changes
3. Once approved, a maintainer will merge your PR

## 📖 Documentation

### Updating Documentation

- **README.md**: Overview, features, installation
- **QUICKSTART.md**: Quick setup guide
- **API.md**: MCP tool documentation
- **Code comments**: For complex logic

### Documentation Style

- Use clear, concise language
- Include code examples
- Keep examples up-to-date
- Test all commands/examples before committing

## 🎨 Design Principles

When contributing, please keep these principles in mind:

1. **Local-First**: Data stays on the user's machine
2. **Privacy-Preserving**: Never send user code to external servers (except Anthropic for extraction if API key provided)
3. **Non-Intrusive**: Minimal impact on Claude Code performance
4. **Evidence-Based**: Every fact must have an evidence chain
5. **Fail-Safe**: Errors should not block Claude Code operations
6. **Incremental**: Features should work with partial data

## 🏗️ Architecture Guidelines

### Adding a New MCP Tool

1. Add tool definition in `server.py::list_tools()`
2. Implement handler in `server.py::call_tool()`
3. Add implementation in `tools.py`
4. Add tests in `tests/test_tools.py`
5. Update documentation

### Modifying Database Schema

1. **Never** change existing columns (breaks existing installations)
2. Add new tables/columns only
3. Provide migration script in `scripts/migrate.py`
4. Update models in `models.py`
5. Test with existing data

### Adding External Dependencies

- Minimize dependencies
- Use well-maintained packages
- Document why the dependency is needed
- Update `pyproject.toml`

## 🤝 Community

- **Discussions**: Use GitHub Discussions for questions and ideas
- **Issues**: Use GitHub Issues for bugs and feature requests
- **Discord**: Join our Discord for real-time chat (link in README)

## 📜 License

By contributing, you agree that your contributions will be licensed under the MIT License.

## ❓ Questions?

If you have questions about contributing, feel free to:
- Open a Discussion on GitHub
- Ask in our Discord
- Email: contribute@world-model-mcp.dev

---

**Thank you for contributing to World Model MCP!** 🎉

Your contributions help make AI coding assistants smarter and more reliable for everyone.
