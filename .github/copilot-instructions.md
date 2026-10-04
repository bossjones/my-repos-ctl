# Copilot instructions for my-repos-ctl

## Build, test and lint commands

Use the committed uv lockfile for all local checks:

```bash
uv sync --locked
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked ty check
uv run --locked pytest
uv build
```

Run a single test with pytest node ids, for example:

```bash
uv run --locked pytest tests/test_cli.py::test_load_config_resolves_paths_from_config_not_cwd
```

Useful targeted variants:

```bash
uv run --locked pytest tests/test_cli.py tests/test_git.py -k status
uv run --locked my-repos-ctl --help
```

CI runs the same gate on Python 3.12 for Linux/macOS and Python 3.13 on Linux.
pytest discovers `tests/`, includes tests marked `mutation` by default, and
requires at least 85% runtime-package coverage. Ruff targets Python 3.12, line
length 88, and rules `E,F,I,UP,B`; `ty` is the only type checker and checks
`src/`.

## High-level architecture

This is a Python 3.12+ `uv_build` package with the console entry point
`my-repos-ctl = my_repos_ctl.cli:main`. The runtime intentionally lives in
`src/my_repos_ctl/cli.py`: argparse setup, safe PyYAML config loading,
repository selection, Git subprocess execution, result reporting and exit-code
mapping are all kept together. `src/my_repos_ctl/__init__.py` is docstring-only.
Rich handles only human pull presentation: immediate completed-repo results and
a failure-first summary table, with pull-only `--quiet`/`-q`. Use literal `Text`
for names/paths/diagnostics and fold cells without clipping. JSON creates no
console and preserves selection order; other commands keep their plain output.

The CLI contract is the README plus `docs/design.md`: six commands (`list`,
`status`, `branches`, `pull`, `fetch`, `checkout`), common options accepted
before or after the subcommand, JSON output shaped as
`{"command", "results", "summary"}`, and exit codes `0` for all-success, `1`
for per-repo Git failures after continuing, and `2` for argument/configuration
failures before operations start.

Configuration defaults to `~/.my-repo-ctl.yml`. `load_config()` uses a custom
safe YAML loader that rejects duplicate keys, requires exactly a nonempty
`repos` mapping, expands `~`, resolves relative repo paths relative to the
config file, and rejects duplicate resolved paths. `select_repos()` preserves
YAML order for the default selection, deduplicates repeated explicit selectors,
and rejects unknown selectors before any mutation.

Git access goes through the `_git(repo, args, timeout)` seam. Keep Git calls as
argument arrays with `shell=False`, disabled pagers, noninteractive stdin,
bounded per-subprocess timeouts and preserved diagnostics. `_validate_worktree()`
must verify the configured path is the actual non-bare Git worktree root while
allowing linked worktrees.

Tests encode the integration boundaries. `tests/conftest.py` isolates
`HOME`, XDG config, ambient `GIT_*` settings and Git global/system config, then
provides temporary repo, local bare remote, config, snapshot and CLI invocation
fixtures. `tests/test_cli.py` covers config, parser, selection, JSON and
subprocess-seam behavior; `tests/test_git.py` covers real temporary Git
worktrees/remotes and mutation safety.
`tests/test_pull_output.py` covers Rich rendering, quiet/JSON precedence, full
diagnostics, per-repo progress timing and narrow-table path preservation.

## Key conventions

- Treat `docs/design.md` as the product contract and `AGENTS.md` as canonical
  contributor guidance; `CLAUDE.md` only points back to `AGENTS.md`.
- Do not introduce frameworks, plugins, command-module layers, Docker,
  justfiles, PEP 723 duplicate scripts, Adobe/Scout dependencies, another type
  checker, or runtime dependencies beyond what the task requires.
- Keep tests away from real managed repositories, personal config and network
  remotes. Use temporary repositories and local bare remotes only.
- Preserve Git safety behavior: `list` performs no Git calls; `branches` does
  not fetch; `checkout` validates branch names and refuses dirty worktrees,
  including untracked files; never force checkout, reset, clean or automatically
  recover conflicts.
- `pull` is defined as `git pull --rebase --autostash`; after a successful Git
  exit, inspect for unmerged entries and report autostash conflicts as failure.
  `fetch` is `git fetch --prune`.
- `--dry-run` on `pull`, `fetch` and `checkout` is read-only preflight that
  reports planned Git argv and must not execute mutating Git commands.
- Keep JSON mode parseable on stdout. Configuration and argument failures go to
  stderr; repository failures belong in per-repo result objects and should not
  stop later selected repos from running.
- Use `ConfigError` for invalid config/selection and `RepoError` for per-repo or
  Git failures. Avoid broad catches and success-shaped fallbacks.
- Track only fictional example paths such as `config.example.yml`; never commit
  personal `~/.my-repo-ctl.yml`, credentials, environment files, caches, virtual
  environments or build artifacts.
- Before release work, inspect wheel contents and smoke-test the installed
  command outside the checkout with isolated temporary config/repos.
