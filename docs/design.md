# my-repos-ctl design

## Intent and scope

A personal, portable CLI for managing an explicitly configured set of local Git
repositories, independent of top-marks, dp-repo-verify, Scout and Adobe tooling.
Malcolm selected an installable uv package instead of the originally proposed
PEP 723 script. Develop in `~/dev/bossjones/my-repos-ctl` on `main`; publish a
public `bossjones/my-repos-ctl` GitHub repository. Keep the existing
dp-repo-verify implementation unchanged.

Keep the initial implementation in one module. Use Python 3.12+, standard-library
argparse/subprocess and PyYAML. No framework, plugins, arbitrary shell execution,
automatic cloning, force checkout, reset or automatic conflict recovery.

## Package and installation

Use a standard `src/my_repos_ctl` package and a `my-repos-ctl` console entry point.
Manage the package and development dependencies with uv; commit `uv.lock`. Use
uv_build as the build backend, following adguardctl.

```text
my-repos-ctl/
  pyproject.toml
  uv.lock
  .python-version
  .gitignore
  README.md
  AGENTS.md
  CLAUDE.md
  config.example.yml
  src/my_repos_ctl/
    __init__.py
    cli.py
  tests/
    conftest.py
    test_cli.py
    test_git.py
  docs/
    design.md
    implementation-plan.md
  .github/workflows/
    ci.yml
```

Install with `uv tool install /path/to/my-repos-ctl`, or
`uv tool install git+https://github.com/bossjones/my-repos-ctl`. Run
`my-repos-ctl` from any directory. uv manages the isolated tool environment and
the executable launcher; it normally links that launcher into `~/.local/bin`.
Do not manually copy tests or development files into the executable directory.
The installed wheel must contain only the runtime package and distribution
metadata, not tests or personal configuration.

## Configuration

Default: `~/.my-repo-ctl.yml`. Support `--config PATH` as an explicit override.

```yaml
repos:
  project-one: ~/dev/project-one
  project-two: ~/dev/project-two
```

Use safe YAML loading. Require a nonempty `repos` mapping of nonempty string
names to nonempty string paths. Reject duplicate YAML keys, duplicate resolved
repository paths, invalid types and unknown top-level fields. Expand `~`;
resolve relative repo paths relative to the configuration file, not the
invoking directory. Missing or malformed configuration is a clear CLI error,
not an empty successful run.

Track only an example with fictional paths. Never commit the personal config,
credentials, environment files, virtual environments, caches or build output.

## CLI contract

Global options: `--config PATH`, repeated `--repo NAME` selectors, `--json`, and
a positive `--timeout SECONDS` (default 300, per Git subprocess). Accept common
options before or after the subcommand for convenient everyday use.

| Command | Behavior |
| --- | --- |
| `list` | List configured names and resolved paths; no Git or network calls. |
| `status` | Report branch or detached commit, dirty state and local path. |
| `branches` | List local and remote-tracking branches; no automatic fetch. |
| `pull` | Run `git pull --rebase --autostash`, preserving the useful existing behavior. |
| `fetch` | Run `git fetch --prune` using Git's configured default remote. |
| `checkout BRANCH` | Switch to a local branch, or let Git track an unambiguous existing remote branch. |

The default selection is every configured repo in YAML order. Explicit selectors
are exact names; reject unknown names before running any operation. Deduplicate
repeated selectors without processing a repository twice.

Support `--dry-run` on pull, fetch and checkout: perform read-only preflight,
report the planned Git command and never execute a mutating subprocess. Label
results as planned rather than completed. Do not imply dry-run predicts remote
availability or merge success.

Human output identifies every repository and includes a concise summary for
mutations. JSON output is one object with `command`, `results` and `summary`;
each result includes `name`, `path`, `status` and `message`, plus command-specific
data. Status values are explicit, for example `ok`, `failed` or `planned`.
Keep stdout parseable in JSON mode; diagnostics are either structured per-repo
results or stderr errors for configuration/argument failures.

Exit 0 only when the requested operation succeeds for every selected repo.
Exit 1 for repo/Git failures, after processing the remaining repos. Exit 2 for
invalid configuration, selection or arguments. Interruption stops further work.

## Git safety and errors

Run Git with argument arrays, never a shell; disable pagers. Verify each
configured path is the actual root of a non-bare Git worktree, rather than an
arbitrary directory nested inside a parent repository. Support linked worktrees.
Missing paths, non-repos and command failures remain visible as failed results.

Checkout validates the requested branch name and refuses dirty worktrees,
including untracked files. Never force checkout, discard changes or silently
stash them. Checkout does not automatically fetch.

Pull deliberately permits dirty worktrees through Git's autostash behavior.
Report conflicts and leave recovery to the user. In particular, inspect for
unmerged entries after a successful pull: an autostash application conflict can
occur even if Git reports a zero process exit code. Treat that as failure.

Git subprocesses have bounded timeouts and noninteractive stdin. Preserve Git
diagnostics. Handle expected filesystem, YAML, executable-not-found and timeout
errors specifically; do not hide unexpected exceptions with broad catches.
Do not modify global Git configuration or switch branches in the user's managed
repositories as part of development or testing.

## TDD and development checks

Write failing behavior tests before product code. Use pytest and pytest-cov,
Ruff for lint/format, and ty as the sole type checker, borrowing adguardctl's
tool choices without its multiple-checker setup or command-module hierarchy.
Require at least 85% runtime-package coverage.

Autouse test isolation redirects HOME/config/XDG configuration and disables
ambient global/system Git settings. Unit tests use monkeypatch for error and
timeout seams. Integration tests create temporary repositories and bare local
remotes; never use the laptop's managed repos, personal YAML or GitHub network.

Cover configuration validation, relative paths, selectors, JSON shape, exit
codes, root validation, detached HEAD, dirty checkout refusal, missing branches,
local/remote branch switching, dry-run nonmutation, pull/fetch, local changes
preserved by autostash, conflicts and processing after a repo fails.

Use a locked uv development environment. CI mirrors the local gate:

```bash
uv sync --locked
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked ty check
uv run --locked pytest
uv build
```

Smoke-test the built/installed command from outside the checkout with a temporary
configuration. Inspect the wheel contents and staged files before committing.

Initialize repository agent guidance as requested: AGENTS.md documents the
actual architecture, commands, TDD workflow and safety rules; CLAUDE.md points to
that canonical guidance. Initialize Copilot instructions via `/init` where the
installed CLI supports noninteractive invocation, or disclose the equivalent
manual initialization if it cannot be invoked as a tool.

## Research and trade-offs

- [adguardctl pyproject](https://github.com/bossjones/adguardctl/blob/main/pyproject.toml):
  Python 3.12+, uv_build, committed lockfile, pytest, Ruff and ty.
- [adguardctl test isolation](https://github.com/bossjones/adguardctl/blob/main/tests/conftest.py):
  temporary configuration and environment isolation rather than personal config.
- [adguardctl CI](https://github.com/bossjones/adguardctl/blob/main/.github/workflows/ci.yml):
  matching local/CI quality gates.
- [uv tools guide](https://docs.astral.sh/uv/guides/tools/):
  tool installation operates on a package and exposes its entry points.
- [uv tool environments](https://docs.astral.sh/uv/concepts/tools/):
  isolated persistent environment; executable launchers managed by uv.
- [uv scripts guide](https://docs.astral.sh/uv/guides/scripts/):
  PEP 723 would suit a standalone artifact, but is not the selected distribution
  model. Avoid maintaining redundant script and package dependency metadata.

Use one CLI module rather than adguardctl's API/client/model/render layers and
many command modules. Keep tests and tooling in the development project; runtime
installation remains small. No justfile or additional abstractions until useful.
