# Agent Monitor

TUI dashboard for monitoring Claude Code instances across Hyprland workspaces.

## Running Tests

**ALWAYS use `scripts/test` to run tests. NEVER use `.venv/bin/python -m pytest` directly.**

```bash
# Run all tests
scripts/test

# Run specific test file
scripts/test tests/test_statusline.py

# With flags (passed through to pytest)
scripts/test tests/test_statusline.py -v --tb=short
```

## Review Scripts

Review scripts are at `.dev_tools/scripts/`, NOT `scripts/`. Use these paths:

```bash
.dev_tools/scripts/review-code --plan <plan-file>
.dev_tools/scripts/review-plan --plan <plan-file>
.dev_tools/scripts/review-questions --plan <plan-file> --message "..."
```
