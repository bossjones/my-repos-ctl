# my-repos-ctl Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship and install a small, tested uv package for YAML-configured local
Git repository management.

**Architecture:** One `src/my_repos_ctl/cli.py` runtime module owns argparse,
validated YAML, Git subprocesses and reporting. Standard console entry-point
packaging exposes `my-repos-ctl`; tests and development tooling stay in the
checkout. Parallel work must have disjoint file ownership.

**Tech Stack:** Python 3.12+, uv/uv_build, PyYAML, pytest/pytest-cov, Ruff, ty.

**Spec:** `docs/design.md`.

## Global Constraints

- Work in `~/dev/bossjones/my-repos-ctl` on `main`, under GitHub user bossjones.
- Publish a public `bossjones/my-repos-ctl` repository.
- Package distribution, not a PEP 723 script; one initial runtime module.
- Default config is `~/.my-repo-ctl.yml`; only a fictional example is tracked.
- Git uses argument arrays, disabled pagers, noninteractive stdin and a positive
  per-subprocess timeout of 300 seconds by default.
- No force operations, automatic conflict recovery or arbitrary shell execution.
- Continue after per-repo errors; exit codes are 0 success, 1 repo failures,
  2 argument/configuration errors.
- Local checks and CI use the committed uv lockfile; coverage floor is 85%.
- Never run development tests against real managed repos or personal config.
- Leave top-marks and dp-repo-verify unchanged.

## Review Focus

- An existing directory inside a parent Git repo must not operate on that parent.
- Relative paths and linked worktrees must resolve independently of cwd.
- Global options on both sides of a subcommand must not override one another.
- Git can exit zero after an autostash application conflict; report failure.
- Building/installing a wheel must not include tests, real config or dev tools.

## Files and work ownership

- Runtime implementer: `src/my_repos_ctl/cli.py`, `tests/test_cli.py`.
- Git test worker: `tests/conftest.py`, `tests/test_git.py`; establish fixture
  names first, then only temporary local repos/remotes.
- Packaging/documentation worker: `pyproject.toml`, `.python-version`,
  `.gitignore`, `README.md`, `config.example.yml`, package `__init__.py`,
  `.github/workflows/ci.yml`, `AGENTS.md`, `CLAUDE.md`.
- Coordinator: resolve uv dependencies/lockfile, run checks, review integration,
  invoke Copilot init, inspect artifacts, commit, publish and install.
- Do not have two workers modify the same file concurrently. Runtime-dependent
  tests can be authored against the interfaces below but implementation waits
  until the owning tests have demonstrated red.

### Task 1: Validated configuration and read-only CLI

**Files:** Create runtime, package metadata, `.gitignore`, `tests/conftest.py`,
`tests/test_cli.py`; Git test worker creates initial read-only cases.

**Interfaces:**

- `Repo`: frozen dataclass with `name: str` and `path: pathlib.Path`.
- `ConfigError` and `RepoError`: specific exception types.
- `load_config(path: Path) -> list[Repo]`: validates/resolves the YAML contract.
- `select_repos(repos: list[Repo], names: list[str]) -> list[Repo]`: exact-name
  selection, duplicate selectors collapse, unknown names raise ConfigError.
- `main(argv: Sequence[str] | None = None) -> int`: console entry point, callable
  in tests with capsys; argparse usage failures may raise SystemExit(2).
- A single patchable `_git(repo: Repo, args: list[str], timeout: float) -> str`
  subprocess seam: returns stdout on success; raises RepoError with diagnostics
  on Git errors, missing executable or timeout.

- [x] Write failing pytest cases for missing/malformed YAML, duplicate keys,
  missing/empty/wrong-type repos, duplicate resolved paths, unknown fields,
  relative/tilde paths and unknown/duplicate selectors.
- [x] Write failing cases for list/status/branches, JSON top-level keys
  `command`, `results`, `summary`, default config and options before/after the
  command. List performs no Git calls; missing repos in status exit 1.
- [x] Run the selected tests before implementation; record their meaningful
  failures, not just a missing dependency.
- [x] Initialize uv packaging with `requires-python = ">=3.12"`, uv_build,
  `[project.scripts] my-repos-ctl = "my_repos_ctl.cli:main"` and PyYAML runtime
  dependency. Add pytest, pytest-cov, Ruff and ty using uv's dependency commands.
- [x] Implement the declared interfaces and list/status/branches. Verify actual
  non-bare worktree root via Git; detached HEAD reports its short commit.
  Branch queries list both local and remote-tracking refs without fetching.
- [x] Test nested-directory refusal, linked worktrees, detached HEAD, Git failure
  diagnostics and timeouts using temporary repos or a patched subprocess seam.
- [x] Run `uv run --locked pytest tests/test_cli.py tests/test_git.py`
  (read-only cases only while mutations remain unimplemented); expect PASS.
- [x] Commit this independently functioning read-only deliverable.

### Task 2: Safe mutations and failure reporting

**Files:** Modify `src/my_repos_ctl/cli.py`, `tests/test_cli.py`;
Git worker owns mutation cases in `tests/test_git.py`.

**Consumes:** Task 1 interfaces and JSON/exit-code contract.

**Produces:** pull/fetch/checkout, mutation `--dry-run`, per-repo continued
processing and mutation summary. Git command choices are exactly those in
`docs/design.md`; stdout remains JSON-only when requested.

- [x] Write failing command/seam tests asserting pull includes
  `--rebase --autostash`, fetch includes `--prune`, checkout uses `git switch`
  without force, and dry-run never calls those mutating commands.
- [x] Write failing local-remote tests for pull bringing in a new commit,
  autostash preserving tracked edits, fetch obtaining remote refs, local and
  unambiguous remote checkout, invalid/missing branches, and refusal to switch
  a worktree with tracked or untracked changes.
- [x] Write failing tests that one missing/non-Git/conflicting repo does not
  prevent a later repo succeeding; assert exit 1 and correct result counts.
- [x] Write a failing test where Git pull succeeds but unmerged entries remain;
  assert the result is failed with actionable conflict text.
- [x] Run these selected tests and observe expected behavioral failures.
- [x] Implement mutations and dry-run with read-only preflight. Validate branch
  names before processing; never silently discard or automatically recover.
  After a successful pull, inspect unmerged index entries.
- [x] Run `uv run --locked pytest`; expect every CLI and temporary Git test to
  pass and runtime-package coverage >=85%.
- [x] Commit this independently functioning mutation deliverable.

### Task 3: Installation, instructions and public release

**Files:** Finish README, example config, agent guidance, CI and lockfile;
update this plan's checkboxes to reflect actual completed work.

**Consumes:** All six commands and the finalized runtime contract.

**Produces:** Public repository, documented uv install/update/uninstall workflow,
locked quality gates, initialized agent instructions and installed smoke-tested
executable.

- [x] Mirror adguardctl's useful conventions: uv_build, uv.lock, isolated pytest
  config, Ruff, a single ty gate, and CI matching documented local commands.
  Do not copy API layers, Docker, multiple type checkers or a justfile.
- [x] Document six commands, selectors, JSON, dry-run, timeouts, configuration,
  nonzero exits, autostash caveats and safe manual conflict recovery.
- [x] Ignore `.venv`, Python/test/lint/type caches, build artifacts, environment
  files and personal/local YAML; retain the fictional example and CI YAML.
- [x] Initialize Copilot instructions using the installed CLI's init command;
  generate concise canonical AGENTS.md and referencing CLAUDE.md. Inspect any
  generated instructions for accurate commands and unwanted personal data.
- [x] Run `uv sync --locked`, Ruff format check, Ruff lint, ty and pytest.
  Expect clean output and coverage >=85%; fix only task-related failures.
- [x] Run `uv build`; inspect the wheel to assert no tests, development tooling
  or personal configuration is installed.
- [x] Install the built artifact in a temporary uv tools directory and invoke
  its executable from outside the checkout with an isolated temporary YAML
  and local Git repo. Assert JSON, exit status and unchanged dirty files.
- [x] Perform an independent whole-project review, then resolve concrete defects
  with regression tests. Re-run the narrow affected check and final gate.
- [x] Inspect `git diff --check`, staged filenames and staged content for secrets,
  generated files and personal paths. Commit intentional project files only.
- [x] Reconfirm `gh api user` is bossjones; use `gh repo create` to publish public
  `bossjones/my-repos-ctl` from local `main`, then verify GitHub's default branch.
- [x] Install using `uv tool install` without overwriting unrelated executables;
  verify the installed command is responsive from outside the checkout.
  Do not silently overwrite an existing personal configuration.

## Execution handoff

Malcolm explicitly requested subagents and parallel work. Preserve that method:
independent Git tests and packaging/docs run alongside the TDD runtime work with
disjoint file ownership. Review the integrated result before publishing.
The written plan must be reviewed before implementation begins.

## Execution record

The written specification was explicitly approved. At the final plan-review
handoff the user was unavailable and instructed autonomous completion; execution
therefore followed this preserved plan and the approved specification.
Parallel test/tooling workers were followed by sequential runtime milestones.

Final reviewed local validation: 132 passing tests, 95.12% coverage, clean Ruff
and ty, and successful distributions build. Installed-wheel smoke tests exercised
all six commands outside the checkout using temporary uv tool directories,
configuration and local Git remotes. No personal config or managed repository
was used. Final review findings were corrected with failing regression tests and
received a clean scoped re-review.

Published the public `bossjones/my-repos-ctl` repository with `main` as its first
and default branch. Installed the normal non-editable package with uv; verified
the managed launcher from outside the checkout. Personal configuration remains
untouched. Git transport uses the already-authorized bossjones SSH identity via
repository-local configuration, because the active gh OAuth token lacks workflow
scope and the generic github.com SSH identity belongs to the other account.
