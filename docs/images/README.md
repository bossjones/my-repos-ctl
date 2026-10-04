# Screenshot provenance and reproduction

These PNGs are locally rendered, Carbon-like cards, not screenshots of a real
repository inventory. [showcase.html](showcase.html) is the self-contained source:
HTML/CSS, system fonts, no renderer dependencies, external resources or scripts.
No content was sent to Carbon or another rendering/upload service.

| Asset | Logical size | PNG pixels (2x) | Content |
| --- | --- | --- | --- |
| `config.png` | 1200 x 504 | 2400 x 1008 | The exact fictional YAML used by the demo. |
| `list-status.png` | 1200 x 606 | 2400 x 1212 | Captured `list` and `status` stdout. |
| `fetch.png` | 1200 x 636 | 2400 x 1272 | Captured dry-run and actual fetch stdout, including summaries. |
| `pull.png` | 1200 x 716 | 2400 x 1432 | Captured quiet pull table: updated, up-to-date and failed worktrees. |

## What was captured

The CLI ran from the source checkout using its existing locked environment:

```bash
uv run --locked my-repos-ctl list
uv run --locked my-repos-ctl status
uv run --locked my-repos-ctl fetch --repo demo/docs --dry-run
uv run --locked my-repos-ctl fetch --repo demo/docs
```

All four commands exited zero. Their complete stdout is preserved in the HTML,
apart from the path normalization below; there were no stderr diagnostics.
The `$` prompts, card titles, labels and window chrome are presentation, not CLI
output. Long output lines wrap visually without changing their text.

The demo used two newly created Git worktrees, `demo/app` and `demo/docs`, and two local
bare remotes under one temporary directory. Both worktrees started on `main`;
only `demo/app` had a modified tracked file. A seed checkout pushed `feature/guide`
to the docs remote **after** cloning docs. The dry-run left docs refs unchanged;
the actual fetch added `refs/remotes/origin/feature/guide`. No personal
configuration, managed repositories, private source or network remotes were used.

`HOME`, all four XDG directories, the global Git config and the uv cache were
isolated under the temporary directory. System Git configuration was disabled,
Git prompts were disabled, and Git identity was the fictional
`Demo Author <demo@example.invalid>`. uv ran offline using the checkout's existing
`.venv/bin/python`.

**Path normalization:** replace the exact, physically resolved temporary `HOME`
prefix with `~`. Thus `<temporary-root>/home/dev/demo/docs` becomes `~/dev/demo/docs`.
The fixture-root prefix may additionally be replaced with `/demo` when displaying
local remote paths; none appear in these captures. No branch names, statuses,
results, summaries or other output text were edited. The YAML already contains
literal `~/dev/demo/app` and `~/dev/demo/docs`, which expand inside the isolated HOME.

## Recreate the fixtures and capture stdout

From a checkout with its locked environment already available, run the following
in a subshell. All Git mutations target only newly created temporary fixtures.
The temporary directory is printed at the end for inspection and targeted cleanup.
Do not substitute real repositories or personal configuration.

```bash
(
  set -eu
  project="$PWD"
  demo="$(mktemp -d)"
  demo="$(cd "$demo" && pwd -P)"
  export HOME="$demo/home"
  export XDG_CONFIG_HOME="$demo/xdg-config"
  export XDG_CACHE_HOME="$demo/xdg-cache"
  export XDG_DATA_HOME="$demo/xdg-data"
  export XDG_STATE_HOME="$demo/xdg-state"
  export GIT_CONFIG_GLOBAL="$demo/global.gitconfig"
  export GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_NOSYSTEM=1
  export GIT_TERMINAL_PROMPT=0
  export GIT_AUTHOR_NAME="Demo Author" GIT_COMMITTER_NAME="Demo Author"
  export GIT_AUTHOR_EMAIL=demo@example.invalid
  export GIT_COMMITTER_EMAIL=demo@example.invalid
  export UV_PYTHON="$project/.venv/bin/python"
  export UV_CACHE_DIR="$demo/uv-cache" UV_OFFLINE=1 NO_COLOR=1
  mkdir -p "$HOME/dev/demo" "$demo/remotes" "$demo/seed"
  : > "$GIT_CONFIG_GLOBAL"

  for name in app docs; do
    git init --bare --initial-branch=main "$demo/remotes/$name.git"
    git init --initial-branch=main "$demo/seed/$name"
    printf '# Fictional %s fixture\n' "$name" > "$demo/seed/$name/README.md"
    git -C "$demo/seed/$name" add README.md
    git -C "$demo/seed/$name" commit -m "Initialize fictional fixture"
    git -C "$demo/seed/$name" remote add origin "$demo/remotes/$name.git"
    git -C "$demo/seed/$name" push -u origin main
    git clone "$demo/remotes/$name.git" "$HOME/dev/demo/$name"
  done

  printf 'repos:\n  demo/app: ~/dev/demo/app\n  demo/docs: ~/dev/demo/docs\n' > "$HOME/.my-repo-ctl.yml"
  printf '\nA local work-in-progress edit.\n' >> "$HOME/dev/demo/app/README.md"
  git -C "$demo/seed/docs" checkout -b feature/guide
  printf '# Fictional getting-started guide\n' > "$demo/seed/docs/guide.md"
  git -C "$demo/seed/docs" add guide.md
  git -C "$demo/seed/docs" commit -m "Add fictional guide"
  git -C "$demo/seed/docs" push origin feature/guide

  uv run --locked my-repos-ctl list > "$demo/list.txt"
  uv run --locked my-repos-ctl status > "$demo/status.txt"
  git -C "$HOME/dev/demo/docs" for-each-ref > "$demo/refs-before.txt"
  uv run --locked my-repos-ctl fetch --repo demo/docs --dry-run > "$demo/dry-run.txt"
  git -C "$HOME/dev/demo/docs" for-each-ref > "$demo/refs-dry-run.txt"
  cmp "$demo/refs-before.txt" "$demo/refs-dry-run.txt"
  uv run --locked my-repos-ctl fetch --repo demo/docs > "$demo/fetch.txt"
  git -C "$HOME/dev/demo/docs" show-ref --verify refs/remotes/origin/feature/guide
  printf 'Temporary fixture directory: %s\n' "$demo"
)
```

Normalize only the prefixes described above before comparing these text files
with the matching `.stdout` elements in `showcase.html`. Runtime changes may
legitimately change output; refresh the HTML from new captures, not invented
results.

## Capture the Rich pull table

`pull.png` was captured separately from fresh, clean app/docs fixtures with
`COLUMNS=108` and `NO_COLOR=1`. A new app commit was published to its local bare
remote; docs remained up to date. `demo/missing` points to a deliberately absent
worktree. The config selects app, missing, then docs: the table displays missing
first, without changing operation order. The command exited **1**, correctly
reporting one failure while pulling both valid worktrees. stderr was empty.
The updated app HEAD was checked against the seed commit after the pull.

Repeat the isolation and initial app/docs cloning setup above in a fresh
temporary directory. For matching column layout, use
`mktemp -d /tmp/mrc.XXXXXX` and resolve it physically as above. Do not add the
local edit, publish `feature/guide`, or run the four earlier capture commands.
Instead, within the same isolated subshell, use:

```bash
export COLUMNS=108 NO_COLOR=1
unset FORCE_COLOR
printf 'repos:\n  demo/app: ~/dev/demo/app\n  demo/missing: ~/dev/demo/missing\n  demo/docs: ~/dev/demo/docs\n' > "$HOME/.my-repo-ctl.yml"
printf '\nA newly published fixture change.\n' >> "$demo/seed/app/README.md"
git -C "$demo/seed/app" commit -am "Update fictional app"
git -C "$demo/seed/app" push origin main

set +e
uv run --locked my-repos-ctl pull --quiet > "$demo/pull.txt" 2> "$demo/pull-stderr.txt"
code=$?
set -e
test "$code" -eq 1
test ! -s "$demo/pull-stderr.txt"
test "$(git -C "$HOME/dev/demo/app" rev-parse HEAD)" = "$(git -C "$demo/seed/app" rev-parse HEAD)"
```

**Table normalization:** replace only the exact temporary HOME prefix with `~`
inside each captured cell, adding the removed character count as right-padding
to that cell so borders stay aligned. Strip end-of-line blank padding. This
changes presentation whitespace, not diagnostics, statuses, ordering or counts.
The HTML colors the literal `failed`/`pulled` words with CSS; the captured stdout
contains no ANSI escapes. Its preformatted text was compared against the
normalized capture before rendering:

```bash
python3 - "$demo/pull.txt" "$HOME" <<'PY'
import sys
from pathlib import Path

for line in Path(sys.argv[1]).read_text().splitlines():
    cells = line.split("│")
    replaced = [cell.replace(sys.argv[2], "~") for cell in cells]
    padded = [
        new + " " * (len(old) - len(new))
        for old, new in zip(cells, replaced, strict=True)
    ]
    print("│".join(padded).rstrip())
PY
```

`pull.png` is 382,392 bytes. The browser confirmed that the table did not
overflow its content width and the footer was inside the card; the resulting
PNG was also inspected for alignment and legibility. All earlier captures and
PNGs remain unchanged.

## Render the PNGs locally

Use an already available `playwright-cli` and Chromium/Chrome. Save this temporary
browser configuration outside the repository:

```json
{
  "browser": {
    "browserName": "chromium",
    "launchOptions": {"channel": "chrome"},
    "contextOptions": {
      "viewport": {"width": 1200, "height": 720},
      "deviceScaleFactor": 2
    }
  }
}
```

Open a dedicated session, not an existing user browser:

```bash
playwright-cli -s=repos-showcase open --config=/absolute/path/to/browser-config.json
```

Run the following function with `playwright-cli -s=repos-showcase run-code
--filename=/absolute/path/to/render.js`. Replace the illustrative checkout URL
and output directory with your local absolute paths. The page is loaded from
`file://`; no server is needed. Network requests are blocked before navigation.
The HTML also has a restrictive Content Security Policy.

```javascript
async page => {
  await page.context().route("**/*", route => {
    return route.request().url().startsWith("file:")
      ? route.continue()
      : route.abort("blockedbyclient");
  });
  await page.goto("file:///absolute/path/to/my-repos-ctl/docs/images/showcase.html");
  await page.evaluate(() => document.fonts.ready);
  for (const [id, filename] of [
    ["config", "config.png"],
    ["status", "list-status.png"],
    ["fetch", "fetch.png"],
    ["pull", "pull.png"],
  ]) {
    await page.locator(`#${id}`).screenshot({
      path: `/absolute/path/to/my-repos-ctl/docs/images/${filename}`,
      scale: "device",
    });
  }
}
```

Close only this session:

```bash
playwright-cli -s=repos-showcase close
```

Check pixel dimensions and file sizes after rendering (each PNG should remain
below 700 KB), and open all four images to inspect text contrast, wrapping and
clipping. Font metrics may vary across operating systems because no fonts are
downloaded or bundled.
