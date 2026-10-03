# Agent guidance

This file is the canonical contributor guidance; `CLAUDE.md` references it.

## Architecture and scope

- Python 3.12+ `src/my_repos_ctl` package, built by `uv_build`, with entry point
  `my-repos-ctl = my_repos_ctl.cli:main`.
- Keep initial runtime behavior in `src/my_repos_ctl/cli.py`: argparse, safe
  PyYAML configuration and subprocess Git operations. `__init__.py` is docstring
  only. No framework, plugins, extra layers, justfile, Docker or PEP 723 duplicate.
- Six commands: `list`, `status`, `branches`, `pull`, `fetch`, `checkout`.
  Follow `docs/design.md` and README for options, JSON, exits and Git safety.
- Runtime is portable; do not introduce Adobe, Scout or checkout dependencies.
  Default user config is `~/.my-repo-ctl.yml`; track fictional example paths only.

## TDD and checks

Write failing behavior tests before runtime changes, implement the smallest
correct change, then run targeted tests before the complete gate:

```bash
uv sync --locked
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked ty check
uv run --locked pytest
uv build
```

pytest discovers `tests/` and requires >=85% runtime coverage. Register mutation
tests with the `mutation` marker; they are included in the default gate.
Ruff uses Python 3.12, line length 88 and `E,F,I,UP,B`; ty alone checks runtime
`src/`. Do not add another type checker.

## Safety and collaboration

- Never test against real managed repositories, personal config or network
  remotes. Isolate HOME/config/XDG/global and system Git settings; use temporary
  repositories and local bare remotes.
- Run Git via argument arrays, never shell strings; retain diagnostics and
  bounded timeouts. Continue after per-repo errors, not configuration failures.
- Preserve dry-run nonmutation, dirty-checkout refusal and post-pull unmerged
  checks. No force operations, resets or automatic conflict recovery.
- Never overwrite user config or unrelated launchers, alter global Git settings,
  or commit secrets, personal paths, environments, caches or build artifacts.
- Respect assigned file ownership and concurrent changes. Do not overwrite
  another contributor's work or change dependencies/lockfiles without need.
- Malcolm explicitly authorized `main` for this initial project only. Subsequent
  work uses branches and PRs unless separately authorized. Never add coauthor
  trailers to commits.
- Before release, inspect wheel contents and smoke-test the installed command
  outside the checkout with isolated temporary config/repos.
