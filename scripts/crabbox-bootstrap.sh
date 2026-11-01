#!/usr/bin/env bash
# Lane setup for this project, seeded by devt init/sync; the project owns it: edit and commit it.
# The router's converge runs it in the lane folder before every gate. It keeps .venv (listed
# under sync.exclude in .crabbox.yaml) while pyproject.toml is unchanged and the venv's Python
# still meets [project].requires-python; otherwise it rebuilds .venv with the newest Python on
# PATH that meets it and has venv and ensurepip, and installs the project with its dev extras,
# ruff and pytest. A box without such a Python fails here, so the gate reads unavailable,
# never red: see "Lane boxes for Python projects" in dev-tools docs/remote-lane.md.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

# Converge runs this without a login shell; reach the user's Python installs.
for dir in "$HOME/.local/bin" "$HOME/.asdf/shims" "$HOME/.local/share/mise/shims"; do
  if [ -d "$dir" ]; then PATH="$dir:$PATH"; fi
done
export PATH

# Exit 0 and `<version>\x1f<spec>\x1f` when the interpreter meets [project].requires-python and has
# venv and ensurepip; 1 and `<version>\x1f<spec>\x1f<why>` when it does not; 2 and
# `refuse\x1f<reason>` when pyproject.toml cannot be read or its requirement cannot be evaluated.
# Any other output is an interpreter that could not run the check (a broken shim).
IFS= read -r -d '' check <<'PY' || true
import platform, re, sys

version = platform.python_version()
try:
    import tomllib
except ImportError:
    print(f"{version}\x1f\x1fhas no tomllib")
    sys.exit(1)

try:
    with open("pyproject.toml", "rb") as handle:
        data = tomllib.load(handle)
except OSError as error:
    print(f"refuse\x1fpyproject.toml is unreadable: {error.strerror}")
    sys.exit(2)
except tomllib.TOMLDecodeError as error:
    print(f"refuse\x1fpyproject.toml is malformed: {error}")
    sys.exit(2)

project = data.get("project", {})
spec = project.get("requires-python", "") if isinstance(project, dict) else None
if not isinstance(spec, str):
    print("refuse\x1f[project].requires-python is not a string")
    sys.exit(2)

current = tuple(sys.version_info[:3])


def padded(a, b):
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)), b + (0,) * (width - len(b))


def meets(op, release, star):
    if star:
        prefix = current[: len(release)] == release
        return prefix if op == "==" else not prefix
    have, want = padded(current, release)
    if op == "~=":
        return have >= want and current[: len(release) - 1] == release[:-1]
    return {
        "==": have == want,
        "!=": have != want,
        "<=": have <= want,
        ">=": have >= want,
        "<": have < want,
        ">": have > want,
    }[op]


clause = re.compile(r"(~=|==|!=|<=|>=|<|>)\s*(\d+(?:\.\d+)*)(\.\*)?")
ok = True
for text in filter(None, (part.strip() for part in spec.split(","))):
    match = clause.fullmatch(text)
    if not match or (match[3] and match[1] not in ("==", "!=")) or (
        match[1] == "~=" and "." not in match[2]
    ):
        print(f'refuse\x1frequires-python clause "{text}" is outside what this script evaluates')
        sys.exit(2)
    ok = ok and meets(match[1], tuple(int(n) for n in match[2].split(".")), bool(match[3]))

if not ok:
    print(f'{version}\x1f{spec}\x1fdoes not meet requires-python "{spec}"')
    sys.exit(1)

try:
    import ensurepip, venv  # noqa: F401
except ImportError:
    print(f"{version}\x1f{spec}\x1fhas no venv or ensurepip")
    sys.exit(1)

print(f"{version}\x1f{spec}\x1f")
PY

refuse() {
  echo "bootstrap: $1; edit pyproject.toml or scripts/crabbox-bootstrap.sh" >&2
  exit 1
}

# Runs the check with interpreter $1: sets `version`, `spec_seen` and `why` and returns 0 or 1
# as the check does; refuses on the check's refusal; returns 3 with `why` its first output line
# when the interpreter could not run the check.
probe() {
  local rc=0 result
  result="$("$1" -I -c "$check" 2>&1)" || rc=$?
  IFS=$'\x1f' read -r version spec_seen why <<<"$result" || true
  if [ "$rc" = 2 ] && [ "$version" = refuse ]; then refuse "$spec_seen"; fi
  if [ "$rc" -le 1 ] && [[ "$result" == *$'\x1f'* ]]; then return "$rc"; fi
  why="unusable: ${result%%$'\n'*}"
  return 3
}

marker=.venv/.bootstrap-pyproject
digest="$(sha256sum pyproject.toml 2>/dev/null | cut -d' ' -f1)" || digest=""

if [ -x .venv/bin/python ] && probe .venv/bin/python && [ -n "$digest" ] &&
  [ "$(cat "$marker" 2>/dev/null)" = "$digest" ]; then
  echo "bootstrap: .venv $version reused"
  exit 0
fi

# The interpreter for the rebuild comes from PATH only, never from .venv (about to go).
venv_dir="$root/.venv"
on_path() {
  local dir
  local IFS=:
  for dir in $PATH; do
    case "$(cd "$dir" 2>/dev/null && pwd -P)" in "$venv_dir" | "$venv_dir"/*) continue ;; esac
    if [ -x "$dir/$1" ] && [ -f "$dir/$1" ]; then
      echo "$dir/$1"
      return 0
    fi
  done
  return 1
}

chosen=""
spec=""
tried=()
candidates=()
for ((minor = 40; minor >= 0; minor--)); do candidates+=("python3.$minor"); done
candidates+=(python3)
for name in "${candidates[@]}"; do
  path="$(on_path "$name")" || continue
  rc=0
  probe "$path" || rc=$?
  if [ "$rc" = 0 ]; then
    chosen="$path"
    break
  fi
  if [ "$rc" = 1 ]; then
    if [ -n "$spec_seen" ]; then spec="$spec_seen"; fi
    tried+=("$name $version${why:+ ($why)}")
  else
    tried+=("$name ($why)")
  fi
done

if [ -z "$chosen" ]; then
  list="none"
  for entry in "${tried[@]}"; do
    if [ "$list" = none ]; then list="$entry"; else list="$list, $entry"; fi
  done
  echo "bootstrap: no Python on $(uname -n) meets requires-python \"$spec\" with venv and" \
    "ensurepip (tried: $list); see \"Lane boxes for Python projects\" in dev-tools" \
    "docs/remote-lane.md" >&2
  exit 1
fi

rm -rf .venv
"$chosen" -m venv .venv
.venv/bin/python -m pip install -e '.[dev]' ruff pytest
echo "$digest" >"$marker"
echo "bootstrap: .venv $version built"
