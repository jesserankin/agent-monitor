# Project Conventions

This file contains project-specific conventions and guidelines for Claude Code.

## Code Style

- Use type hints on all public functions and methods
- Follow PEP 8 with line length of 100 characters
- Use `ruff` for formatting and linting
- Prefer f-strings over `.format()` or `%` formatting
- Use pathlib.Path instead of os.path for file operations

## Naming Conventions

- `snake_case` for functions, variables, and modules
- `PascalCase` for classes
- `SCREAMING_SNAKE_CASE` for constants
- Prefix private methods/attributes with `_`
- Use descriptive names over abbreviations

## Testing

- Tests in `tests/` directory
- Test files named `test_*.py`
- Use pytest fixtures for common setup
- Aim for 80%+ coverage on new code
- Write tests before or alongside implementation (TDD when practical)

## Quality Commands

Run these before committing:

```bash
ruff format .              # Format code
ruff check .               # Lint check
mypy .                     # Type check (if configured)
pytest                     # Run tests
```

Or if a Makefile is available:
```bash
make check-all             # Run all checks
make test                  # Run tests only
```

## Error Handling

- Use specific exception types, not bare `except:`
- Log errors with appropriate levels (debug, info, warning, error)
- Return meaningful error messages to users
- Don't swallow exceptions silently

## Documentation

- Docstrings for public modules, classes, and functions
- Use Google-style or NumPy-style docstrings consistently
- Keep README.md updated with setup instructions
- Document non-obvious design decisions in code comments

## Dependencies

- Pin versions in requirements.txt or pyproject.toml
- Use virtual environments (.venv)
- Document why each dependency is needed
- Prefer stdlib solutions when reasonable

## Git Conventions

- Feature branches: `feature/description` or `feature/issue-123-description`
- Bugfix branches: `bugfix/description` or `bugfix/issue-456-description`
- Commit messages: imperative mood, ~50 char subject line
- One logical change per commit
- Do not add `Co-Authored-By` or other AI attribution to commit messages
- Prefer plain `git` commands over `git -C <path>` when already in the
  project directory — `-C` flags can trigger unnecessary approval prompts

## Security

- Never commit secrets or credentials
- Use environment variables for configuration
- Validate and sanitize all user input
- Keep dependencies updated for security patches

## Development Workflow

- Implement in small, focused steps — each should be reviewable in under 2 minutes
- Each step must be testable: write or update tests alongside the code
- Commit after each completed step so progress is visible in git history
- Never batch unrelated changes into one step
- When following implementation plans, break plan steps into sub-steps
  if needed to keep each unit small and reviewable


## Project Structure

```
project/
├── package_name/
│   ├── __init__.py        # Public API exports
│   ├── module.py
│   └── subpackage/
├── tests/
│   ├── conftest.py        # Shared fixtures
│   └── test_module.py
├── pyproject.toml
└── README.md
```

## Package Conventions

- Keep required dependencies minimal — prefer stdlib solutions
- Use optional dependency groups for non-core features
- Export public API explicitly in `__init__.py` with `__all__`
- Use `hatchling` as build backend
- Follow semantic versioning

<!-- dev-tools-task-subagents-start -->
## DevTools Task Skills

For the repo-local DevTools task workflow skills, subagents are allowed and expected when the skill calls for them.

- `task-setup` may use a focused clarifier subagent.
- `review-plan` may use reviewer subagents.
- `review-code` should use three parallel reviewer subagents for reuse, quality, and efficiency when available.
- `task-implement` may use reviewer subagents during its review loop.
- `task-complete` may use a final reviewer subagent for risky diffs.
- For `task-implement` cleanup, `simplify` should use agent-backed review when subagents are allowed and available; same-thread cleanup is fallback only when agent spawning is unavailable or fails twice.

Use subagents only for these workflow steps or other explicitly delegated work. Keep them scoped and read-only unless the workflow says otherwise.
<!-- dev-tools-task-subagents-end -->
