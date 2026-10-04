# Plan: Rich pull output and summary table

## Task Description

**Type:** enhancement. **Complexity:** medium.

Make `my-repos-ctl pull` reproduce the useful human-output experience of
`dp-repo-verify pull`: full per-repository progress, a Rich summary table,
failure-first ordering, concise details and a quiet table-only mode.
Implement with TDD, document the exact commands and capture a genuine pull-table
screenshot. Commit and push to the existing `docs/config-and-screenshots` branch
and update PR #1. Do not merge or push to main.

## Objective

Both installed and development commands show the Rich pull summary by default:

```bash
my-repos-ctl pull
my-repos-ctl pull --quiet
uv run --locked my-repos-ctl pull --quiet
my-repos-ctl pull --dry-run --quiet
```

JSON remains machine-readable; fetch, checkout and read-only command formatting
remain unchanged. No real managed repositories are pulled during development,
testing or screenshot generation.

## Problem Statement

The current `_report` prints plain lines and counts after all operations.
`_mutate_repo` discards successful Git stdout and reports only a generic completed
command. Consequently there is no pull table, no immediate per-repo feedback,
no useful successful Git detail and no `--quiet` option. Existing showcase images
demonstrate fetch, not the desired pull experience.

## Solution Approach

Add Rich as a runtime dependency with `uv add rich`, keeping argparse, the
single runtime module and the existing subprocess seam.

For human pull output only, create a Rich `Console` after configuration
validation. After each operation, print its name, path, status and full
diagnostic before starting the next repo, unless `--quiet`/`-q` is set.
Use literal `Text` renderables, never markup interpolation of repository names,
paths or Git messages.

After every selected repo has been processed, render a `Table` titled
**Pull summary**, with **Status**, **Repo** and **Detail** columns:

- Translate internal `ok` to human `pulled`; retain `failed` and `planned`.
- Use green/red/yellow status styles; status words remain intelligible without
  color. Do not force ANSI color on redirected output.
- Display each repo's configured name and full path.
- Display failures first, followed by successes and planned rows, preserving
  selection order within each status group. Never sort the operations themselves.
- Reduce details to the first actionable `error:`/`fatal:` line, otherwise the
  last nonempty line. Cap the excerpt at 160 characters, including the ellipsis.
  Full diagnostics remain available in default live output and JSON.
- Wrap table cells to the terminal width without omitting selected repositories.
- Print one counts line: `Summary: N total, X pulled, Y failed, Z planned`.
  A dry-run reports planned work, not completed pulls.

Keep the JSON object/field names, summary keys, result order, Git argv and exit
codes unchanged. For successful pull results, improve `message` to the captured
Git stdout when nonempty; retain the completed-command message as an explicit
success detail when Git emits no stdout. This is a deliberate diagnostic-content
improvement, not a new JSON field or altered operation status.

`--quiet`/`-q` is a pull-specific option after the subcommand, like `--dry-run`;
it suppresses live human output only. Combined with `--json`, JSON wins and
remains exactly one object. Other commands must reject this unsupported option.

## Relevant Files

- `src/my_repos_ctl/cli.py`: parser, result producer, per-repo loop and reporting.
- `tests/test_cli.py`: existing human-summary assertion must intentionally expect
  `pulled` for pull while preserving `ok` for fetch/checkout.
- `tests/test_git.py`: existing real local-remote pull, continuation, autostash
  and conflict tests prove Git behavior remains intact.
- `tests/conftest.py`: reuse isolated HOME/Git, config and local remote fixtures.
- `pyproject.toml`, `uv.lock`: add and lock the Rich runtime dependency using uv.
- `README.md`: exact default/quiet/JSON/dry-run commands and output behavior.
- `docs/images/showcase.html`, `docs/images/README.md`: add local pull capture and
  its provenance without modifying existing genuine capture contents.
- `AGENTS.md`, `.github/copilot-instructions.md`: note Rich's narrowly scoped
  presentation role; no architecture expansion or additional formatter framework.

### New Files

- `tests/test_pull_output.py`: focused output, streaming and rendering regressions.
- `docs/images/pull.png`: locally rendered retina image of actual pull output
  against temporary fictional worktrees and local remotes.
- `specs/rich-table-spec.md`: this implementation blueprint and final report.

The legacy formatter at `top-marks/src/dp_planning/cli/repo_verify.py:384-429`
is local prior art only. Adapt its useful table/excerpt behavior without importing
that package or publishing unrelated internal source.

## Implementation Phases

### Phase 1: Foundation

Write focused behavior tests before runtime implementation. Use fixed-width,
non-color Rich consoles for renderer assertions, real temporary Git fixtures for
public CLI tests, and a controlled subprocess boundary only for long diagnostics
and live-progress timing. Add the declared dependency with uv.

### Phase 2: Core Implementation

Implement small excerpt/table/progress helpers; retain useful pull stdout;
add the pull-only quiet option; print live results inside the existing loop and
render the final table in `_report`. Keep every non-pull reporting path intact.

### Phase 3: Integration & Polish

Update documentation, capture a real local pull table, verify packaging and
installed invocation, run the complete locked gate and push the same branch.

## Step by Step Tasks

IMPORTANT: Execute every step in order, top to bottom.

### 1. Establish red output tests

- Add CLI tests for a default `pull` table, quiet table-only mode, `-q`, human
  mixed-success continuation, failure-first table ordering and dry-run labeling.
- Add `--json --quiet` coverage with mixed results: one JSON object, unchanged
  field names/counts/order, no table or progress text, exit 1 for a failure.
- Prove a finished repo's live result is visible before the next repo starts.
- Prove successful Git stdout is displayed; failures are not reduced to generic
  completed-command text.
- Add long/multiline/error-priority excerpt boundaries, literal `[red]`/`[/]`
  name/message rendering, narrow-console completeness and non-TTY no-ANSI tests.
- Run selected tests and observe missing table/quiet/progress failures before
  editing runtime code; save red/green evidence in session artifacts, not Git.

### 2. Add the runtime dependency

- Run `uv add rich` and use the generated lockfile.
- Import Rich Console/Table/Text explicitly; do not duplicate dependency
  metadata or add a second package/tooling framework.

### 3. Implement pull-only presentation

- Add `_pull_excerpt(message: str, limit: int = 160) -> str`,
  `_build_pull_summary_table(results: list[dict[str, object]]) -> Table` and
  `_print_pull_result(result: dict[str, object], console: Console) -> None`.
- Preserve success output from `_git` for pull while preserving fetch/checkout
  messages and existing Git conflict checks.
- Add `--quiet`/`-q` to the pull parser.
- Extend `_report` with an optional pull console; leave JSON and non-pull
  formatting/return codes unchanged.
- In `main`, construct the pull console only for human pull output, print each
  completed result immediately unless quiet, then render the final table.
- Intentionally update the existing human-summary test's pull label from `ok`
  to `pulled`; keep its count, path and exit assertions.
- Run focused tests to green, then the complete suite.

### 4. Document and capture the actual pull table

- Document default progress plus table, `pull --quiet`/`-q`, JSON precedence,
  dry-run planned rows and adaptive terminal-width wrapping.
- Capture a quiet pull from temporary fictional repos with a mix of up-to-date,
  updated and missing/no-upstream results, so the screenshot genuinely shows
  failure-first rows and counts.
- Use a fixed console width and normalize only the exact temporary path prefix
  for display. Never pull real repos or send content to Carbon.
- Locally render the captured stdout in the existing visual style. Preserve
  box-drawing columns, use a monospace preformatted block, inspect for clipping
  and document commands, expected exit and path normalization.
- Embed `pull.png` in README and PR #1; retain the current other screenshots.

### 5. Validate, install and ship

- Run the full locked test/format/lint/type/build gate below.
- Inspect the wheel: runtime package and distribution metadata only.
- Smoke-test the built tool outside the checkout with temporary configuration
  and repos; verify quiet table, JSON, dry-run and failed-result exit status.
- Reinstall the user's existing tool snapshot with uv without force, verify its
  help and temporary-fixture output, and leave the private 69-worktree config
  unchanged.
- Inspect staged files for personal inventory, credentials and browser scratch.
- Commit implementation/spec/docs/assets and push only
  `docs/config-and-screenshots`; update PR #1 and verify its hosted checks.
- Append the factual completion report to this spec.

## Testing Strategy

Use TDD and observable output/effects rather than exact full-screen snapshots.
Renderer tests use a fixed width and color-disabled StringIO console; CLI tests
reuse real temporary local Git remotes. Verify failure-first *display* while
operations and JSON stay in selection order. Test long diagnostics, empty Git
stdout, markup-looking input, narrow widths, quiet and redirected output.

Existing rebase/autostash/dirty-state/conflict/timeout/branch-validation tests
remain mandatory. No public screenshot or test may contain the private starter
inventory or operate on the user's managed repos.

## Acceptance Criteria

- Default human pull prints live per-repo diagnostics and one Rich summary table.
- `pull --quiet` and `pull -q` print only the table and one counts line.
- Every selected repo gets one table row; failures are displayed first.
- Headers/title are Status/Repo/Detail and Pull summary; excerpts are <=160 chars.
- JSON stays parseable with unchanged field names, counts, ordering and exits.
- Dry-run emits planned rows and executes no mutating Git command.
- User-supplied bracket text renders literally; redirected output has no forced
  ANSI codes and narrow tables do not omit repos.
- Fetch/checkout/list/status/branches behavior and formatting remain unchanged.
- A real local `pull.png` is embedded and has documented capture provenance.
- Full tests pass with >=85% coverage; Ruff, ty, build and hosted CI pass.
- Changes are committed and pushed to the existing feature branch; main and the
  personal inventory remain untouched.

## Validation Commands

Run from the `my-repos-ctl` checkout:

```bash
uv sync --locked
uv run --locked pytest tests/test_pull_output.py --no-cov
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked ty check
uv run --locked pytest
uv build
uv run --locked my-repos-ctl pull --help
git diff --check
```

The documented `pull` usage examples are instructions, not permission to run
against the default personal config during development.

## Notes

Planning/source inspection was **UNINDEXED**: Scout retrieval tools were
unavailable during discovery and the standalone project was confirmed unattached.
Use the known paths and explicit tests; do not claim Scout impact analysis.
The user's explicit plan-then-build request authorizes execution after saving
this spec, without an additional planning approval loop.

## Report

Implemented live pull diagnostics and a failure-first Rich summary with full
wrapped paths, bounded details and pull-only `--quiet`/`-q`. JSON, operation order,
exit codes, Git safety and non-pull output remain intact.

The installed snapshot has been refreshed. Run `my-repos-ctl pull --quiet`, or
`uv run --locked my-repos-ctl pull --quiet` from the checkout.

**TDD/validation:** observed 17 initial failures before implementation, then
added a red/green full-path clipping regression. All **151 tests pass with
95.73% coverage**, including 19 new presentation cases. Locked sync, Ruff format
and lint, ty and build pass. Both the built wheel and installed command passed
standalone quiet/default/JSON/dry-run/failure-exit checks outside the checkout.

**Docs/demo:** README examples and `docs/images/pull.png` (2400 x 1432) show a
genuine local pull with updated, up-to-date and failed fixtures. Capture
provenance is documented; private configuration is unchanged and no managed
repositories were pulled.

**Delivery:** existing `docs/config-and-screenshots` feature branch,
[PR #1](https://github.com/bossjones/my-repos-ctl/pull/1).
