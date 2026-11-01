# pin-python-tool-versions — Pin the full Python version in .tool-versions so assurance can reuse a side-job green

Status: BRIEF (drafted by dev-tools' agent-monitor-onboarding, 2026-11-01). Class: small. Queue after: none.
Route: small
Start after: none
Other buses: none
Name: Pin the Python version
Problem: agent-monitor has no .tool-versions, so the bus cannot match a lane side-job green to an assurance run and assurance runs the gate again.

## Evidence

- No `.tool-versions` in the repository; `pyproject.toml` says `requires-python = ">=3.12"`.
- dev-tools `docs/bus-project-onboarding.md` § 7, "Python effort gates": "Pin the full Python version in
  `.tool-versions` (`python 3.13.1`) to let assurance reuse a Python side-job green."

## What to do

Add a tracked `.tool-versions` holding one line, `python <major>.<minor>.<patch>`: a version that meets
`requires-python >=3.12`, that this machine's asdf has installed and that the lanes' `crabbox` account can
run (`docs/remote-lane.md` § Lane boxes for Python projects in dev-tools). Pick it by reading what the
lanes have, never by installing a new one in this effort.

## Acceptance

- `.tool-versions` is tracked with one full `python` version, and `scripts/precommit` passes with it.
- The effort's lane gate passes, and assurance can reuse its side-job green (the bus's assurance
  record shows the reuse, or names why it did not).
