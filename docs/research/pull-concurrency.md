# Research: speeding up `my-repos-ctl pull` with concurrency

Date: 2026-11-16. Scope: source-backed design analysis for concurrent pull
behavior and its implementation rationale. Runtime code lives in
`src/my_repos_ctl/cli.py`; this note records the research and chosen trade-offs.

Convention used: **[S]** marks a sourced fact with a citation. **[R]** marks a
recommendation or inference of this report, not a sourced fact. **[M]** marks a
measurement in this repo's isolated local benchmark. **[U]** marks something
that could not be verified. Python docs were read at
`https://docs.python.org/3/...`, which rendered as the "3.15.2 documentation"
on the fetch date. Only behavior with an explicit "Changed in/Added in" note
is attributed to a version. Git docs were read at `https://git-scm.com/docs/...`
(latest manual; the installed Git version was not compared).

## 1. Bottom line

- **[R]** Use a bounded `concurrent.futures.ThreadPoolExecutor` around the
  existing synchronous per-repository function. Do not use asyncio or
  multiprocessing.
- **[R]** Default to resource-based auto concurrency: at least 2 workers, at
  most 8, based on available CPUs and further bounded by selected independent
  repositories. `--jobs N` accepts 1 through 8; `--jobs 1` retains the exact
  sequential path.
- **[R]** In parallel mode, print results as repos finish but preserve `results`
  and JSON order as selection order. Cross-repo operation order necessarily
  changes; each repo's own Git steps remain ordered.
- **[R]** Serialize repos that share a Git common directory, such as linked
  worktrees. Do not retry automatically in the first version; add a retry only
  if a later design can identify a safe pre-mutation failure boundary.

## 2. Source accessibility

| Requested source | Result |
| --- | --- |
| https://realpython.com/python-concurrency/ | Accessible. Read fully ("Speed Up Your Python Program With Concurrency", Jim Anderson, "Updated Oct 22, 2026"). |
| https://www.reddit.com/r/Python/comments/1szaytm/learn_concurrency_a_deep_dive_into_multithreading/ | Direct web fetch returned an anti-bot challenge and the `.json` endpoint returned HTTP 403. Later read the post and visible comments through the user-provided cmux browser surface. |
| Geekuni article linked from the Reddit post | https://blog.geekuni.com/2026/04/python-concurrency.html. The post links to this article; it is a secondary source, not a primary Python reference. |

## 3. Project contract that constrains the design

All from this repository at the commit inspected (`5bbf124`).

- **[S]** Docs layout: `docs/design.md`, `docs/implementation-plan.md` and
  `docs/images/`. There is no research-note folder. `design.md` has a short
  "Research and trade-offs" bullet list (`docs/design.md`, section "Research and
  trade-offs"). This note therefore lives in the new `docs/research/` directory
  and is not linked from `design.md`, to avoid editing contract docs.
- **[S]** `AGENTS.md`, "Architecture and scope": Python 3.12+, runtime kept in
  `src/my_repos_ctl/cli.py` with argparse and subprocess. No framework,
  plugins or extra layers. The Rich console is presentation-only, and operation
  and JSON order must be preserved.
- **[S]** `AGENTS.md`, "Safety and collaboration": Git runs via argument
  arrays, never shell strings. Keep diagnostics and bounded timeouts, and
  continue after per-repo errors. Preserve dry-run nonmutation, dirty-checkout
  refusal and post-pull unmerged checks. No force operations, resets or
  automatic conflict recovery. Never test against real repos, personal config or
  network remotes.
- **[S]** `docs/design.md`, "CLI contract": `pull` runs
  `git pull --rebase --autostash`. Human pull prints each completed repo's
  result immediately, then a failure-first Rich table. "actual operations and
  JSON remain in selection order". Exit 1 for repo failures after processing the
  remaining repos. "Interruption stops further work." `--timeout` is per Git
  subprocess, default 300.
- **[S]** `docs/design.md`, "Git safety and errors": after a successful pull,
  inspect for unmerged entries, because an autostash conflict can leave exit
  code 0. Treat that as failure. Handle expected filesystem, executable-not-found
  and timeout errors specifically, and do not hide unexpected exceptions with
  broad catches.
- **[S]** Current implementation (`src/my_repos_ctl/cli.py`):
  - `_git()` (lines ~123-152) calls `subprocess.run(..., stdin=DEVNULL,
    timeout=..., shell=False)` with `GIT_TERMINAL_PROMPT=0` and
    `GIT_OPTIONAL_LOCKS=0`.
  - `main()` (lines ~427-483) loops over repos sequentially and prints each
    result when it completes.
  - `_mutate_repo()` (lines ~274-310) issues several sequential Git
    subprocesses per repo: worktree validation, the pull, and
    `ls-files --unmerged`.
  - It is entirely synchronous code, and `RepoError` is the per-repo failure
    channel.
- **[R]** Consequence: the cheapest change that satisfies this contract is
  to run `_mutate_repo()` per repo concurrently without rewriting `_git()`.

## 4. What the linked reading says

### Real Python, ["Speed Up Your Python Program With Concurrency"](https://realpython.com/python-concurrency/)

- **[S]** Section "Exploring Concurrency in Python": threads and asyncio tasks
  run on one processor and take turns. Only separate processes run Python code
  at literally the same time. A note links free threading as experimental in
  3.13 and "officially supported since Python 3.14".
- **[S]** Section "What Is Concurrency?": threads use preemptive multitasking
  (the OS can switch at any time), while asyncio uses cooperative multitasking.
- **[S]** Section "When Is Concurrency Useful?": I/O-bound programs spend time
  waiting on the file system and network, and speeding them up means
  overlapping the waits. CPU-bound programs spend time computing.
- **[S]** Section "Multi-Threaded Version": `ThreadPoolExecutor(max_workers=5)`
  with `executor.map()`. The author notes that for I/O-bound work you are "not
  limited to the number of CPU cores", but that "at some point" there are
  diminishing returns, and recommends experimenting. Output order "seemed
  random".
- **[S]** Same section: shared state needs thread-safety, and `requests.Session`
  is not thread-safe. The illustrative timings were about 14.3 s sequential and
  about 3.2 s with threads.
- **[S]** Section "Asynchronous Version": asyncio is the fastest for network
  I/O in their benchmark (about 0.49 s) but needs async-aware libraries. A task
  that does not yield blocks the loop. Their code uses
  `asyncio.gather(..., return_exceptions=True)`. The article's rule of thumb is
  "Use asyncio when you can, threading or concurrent.futures when you must."
- **[S]** Section "Process-Based Version": `ProcessPoolExecutor` defaults to
  the CPU count. "For an I/O-bound problem, increasing the number of processes
  won't make things faster" because setup and teardown cost more than the
  benefit. Inter-process data needs IPC and pickling.
- **[S]** Section "Deciding When to Use Concurrency": "Hold out on adding
  concurrency until you have a known performance issue." CPU-bound work benefits
  only from processes.
- **[R]** Applicability and limits: the article's I/O-bound benchmark is HTTP
  GETs in-process. `my-repos-ctl pull` is different. Python only waits on
  external `git` child processes, which do the network and disk work. The
  article's thread-vs-asyncio-vs-process ranking is therefore suggestive, not
  evidence about subprocess-heavy Git pulls. Its advice to measure first
  applies directly. Time a sequential `pull` over the real set of repos (outside
  the test suite) and look at the slowest repo before choosing a pool size.

### [Reddit thread](https://www.reddit.com/r/Python/comments/1szaytm/learn_concurrency_a_deep_dive_into_multithreading/)

The post links to the [Geekuni tutorial](https://blog.geekuni.com/2026/04/python-concurrency.html).

- **[S]** The Reddit post summarizes a tutorial on concurrency, multithreading,
  multiprocessing, race conditions, locks, and free-threaded Python. It links
  to the Geekuni article above. The post itself provides no implementation
  evidence for this CLI.
- **[S]** In a visible comment, one reader suggests `asyncio` as the default for
  I/O-heavy HTTP, database, and file work, citing many concurrent operations and
  less locking. This is general advice, not a comparison of Python thread pools
  against async subprocesses.
- **[S]** In another visible exchange, a reader asks why the tutorial uses
  `ThreadPoolExecutor` instead of manually managed threads; the post author
  answers that it is a modern way to manage threads and collect return values.
- **[S]** Other visible comments discuss GIL/free-threading caveats and races
  on shared state. These are user comments, not authoritative documentation;
  claims such as free threading being unavailable are stale for current CPython
  releases. See the official free-threading documentation in section 5.5.
- **[R]** The `asyncio` suggestion does not change the repo-specific choice:
  the program already uses synchronous `subprocess.run()` to launch Git, and
  Python has no other async event-loop work to coordinate. A thread pool can
  overlap these existing blocking calls without converting the subprocess seam
  or introducing shared mutable worker state. If the CLI later becomes an
  async application, reassess that trade-off.
- **[R]** The Geekuni tutorial is useful for introductory terminology and
  examples, but its broad statements about disabling the GIL and threading
  should not be used as the compatibility contract. Use the official Python
  docs for current free-threaded-build status and runtime behavior.

## 5. Python 3.12+ primary-source findings

### 5.1 `concurrent.futures` (https://docs.python.org/3/library/concurrent.futures.html)

- **[S]** Section "ThreadPoolExecutor": the default `max_workers` is
  `min(32, (os.process_cpu_count() or 1) + 4)` as of 3.13. It was
  `min(32, os.cpu_count() + 4)` from 3.8 onward, and the doc says this
  default "preserves at least 5 workers for I/O bound tasks" (Changed in
  3.8 and 3.13 notes).
- **[S]** Same section: an exit handler joins pool threads before the
  interpreter exits and runs before `atexit` handlers. "This means exceptions in
  the main thread must be caught and handled in order to signal threads to exit
  gracefully." The doc therefore recommends not using `ThreadPoolExecutor` for
  long-running tasks.
- **[S]** Section "Executor Objects", `shutdown(wait=True, *,
  cancel_futures=False)` (cancel_futures added in 3.9): with
  `cancel_futures=True`, pending futures "that the executor has not started
  running" are cancelled. "Any futures that are completed or running won't be
  cancelled."
- **[S]** Section "Future Objects", `Future.cancel()`: returns False if the
  call is running or finished.
- **[S]** Section "Executor Objects", `map()`: "If a fn call raises an
  exception, then that exception will be raised when its value is retrieved from
  the iterator." The `buffersize` parameter was added in 3.14.
- **[S]** Section "Module Functions", `as_completed(fs, timeout=None)`: yields
  futures as they complete.
- **[S]** Section "ProcessPoolExecutor": the default `max_workers` is
  `os.process_cpu_count()`. On Windows it must be at most 61. If a worker
  terminates abruptly, `BrokenProcessPool` is raised (Changed in 3.3). If a pool
  `initializer` raises, all pending jobs raise `BrokenProcessPool`.
- **[R]** Design implications:
  - Prefer per-repository `submit()` futures plus `as_completed()` over
    `map()`. With `map()`, the first retrieved exception propagates out of the
    iterator, which fights continuing after per-repo errors.
  - Associate each future with its selection index and fill the corresponding
    result slot when it completes. Do not expose unfinished placeholders as
    results. Call `as_completed()` without a timeout; `--timeout` continues to
    apply to each `_git()` subprocess, not to the full batch.
  - Because the coordinator adds group successors dynamically, use
    `wait(active_futures, return_when=FIRST_COMPLETED)` in a replenishing loop,
    or rebuild the `as_completed()` iterator over the current active set.
    Do not assume an existing iterator observes futures submitted after
    iteration began.
  - `RepoError` should be caught inside the worker function so that it becomes a
    `failed` result. Only unexpected exceptions should surface. The design says
    not to hide those with broad catches. On an unexpected exception, stop
    scheduling, cancel queued futures, and re-raise; otherwise an executor
    context manager can wait while already-queued pulls continue mutating repos.

### 5.2 `subprocess` (https://docs.python.org/3/library/subprocess.html)

- **[S]** Section "Using the subprocess Module", `subprocess.run()`: if the
  timeout expires, "the child process will be killed and waited for" and
  `TimeoutExpired` is re-raised after the child has terminated.
- **[S]** Constants: `subprocess.DEVNULL` is accepted for stdin, and
  `communicate()` reads stdout/stderr until EOF.
- **[R]** The existing `_git()` already relies on these semantics, so it can be
  called from worker threads unchanged. **[U]** The docs specify killing and
  waiting for the direct child, but do not say whether descendants of `git`
  (for example `ssh` or a remote helper) are also killed. Do not claim that a
  per-process timeout guarantees cleanup of the whole process tree.

### 5.3 `asyncio` subprocess and thread executor

Sources: https://docs.python.org/3/library/asyncio-subprocess.html, `asyncio-task.html`,
`asyncio-sync.html`, `asyncio-eventloop.html`.

- **[S]** "Subprocesses" / "Creating Subprocesses":
  `asyncio.create_subprocess_exec()` returns a `Process`. "If the process
  object is garbage collected while the process is still running, the child
  process will be killed."
- **[S]** "Interacting with Subprocesses": `Process.wait()` "can deadlock when
  using stdout=PIPE or stderr=PIPE" with large output. Use `communicate()`.
  `kill()` sends SIGKILL on POSIX and is an alias of `terminate()` on Windows.
  `Process` "is not thread safe".
- **[S]** "Subprocess and Threads": on Windows only `ProactorEventLoop` (the
  default) supports subprocesses.
- **[S]** "Synchronization Primitives", `asyncio.Semaphore`: "Not
  thread-safe". It is the normal bounding primitive for async concurrency.
- **[S]** "Task Groups", `asyncio.TaskGroup` (3.11+): when one task fails with a
  non-cancellation exception, the remaining tasks are cancelled. Failures are
  raised together in an `ExceptionGroup`.
- **[S]** "Running in threads", `asyncio.to_thread()`: runs a blocking function
  in a separate thread, "primarily intended" for I/O-bound blocking functions.
- **[S]** "Task" docs, "Important: Save a reference ...": the event loop keeps
  only weak references to tasks.
- **[S]** `asyncio.timeout()` cancels the current task and turns the resulting
  `CancelledError` into `TimeoutError`.
- **[R]** asyncio would work (`create_subprocess_exec` plus `Semaphore` plus
  `communicate()` plus `wait_for` or `timeout`). It would also mean rewriting
  `_git()`, the timeout and decoding error handling and the monkeypatch-based
  tests around `subprocess.run`, plus an event loop and `asyncio.run()`. Cancelling
  a task does not by itself terminate the child process. `TaskGroup`'s
  fail-fast cancellation contradicts "continue after per-repo errors" unless
  every task swallows `RepoError`. The gain over threads is small, because both
  spend their time waiting on `git`. This contradicts the contract's "smallest
  correct change" TDD approach and the single-module simplicity.

### 5.4 `multiprocessing` (https://docs.python.org/3/library/multiprocessing.html)

- **[S]** Section "Contexts and start methods": since 3.14 the default changed
  from fork to forkserver on POSIX platforms that support it (such as Linux);
  macOS defaults to spawn since 3.8. The forkserver change was made "to retain
  the performance but avoid common multithreaded process incompatibilities".
  Spawn and forkserver start a resource-tracker process.
- **[S]** "Programming guidelines", "Safe importing of main module": with spawn
  or forkserver, the main module must be importable without side effects, so
  guard with `if __name__ == '__main__':`.
- **[S]** Warnings under `Process.terminate()`/Queue: terminating a process that
  is using a queue may corrupt the queue, and a child with buffered queue items
  may not exit until they are flushed (deadlock risk when joining).
- **[R]** Processes add pickling of arguments and results, a start method
  surface, `BrokenProcessPool` handling, and harder coverage and mutation
  testing under the 85% gate. They buy nothing here, because the CPU-heavy work
  happens in separate `git` processes already. Rejected.

### 5.5 GIL and threads (https://docs.python.org/3/library/threading.html)

- **[S]** Section "GIL and performance considerations": the GIL limits threads
  for CPU-bound Python code, but "threading is still an appropriate model if you
  want to run multiple I/O-bound tasks simultaneously".
- **[R]** Threads blocked in `subprocess.run()` do no Python CPU work, so the
  GIL is not the bottleneck. Free-threaded builds are irrelevant to this choice.

### 5.6 Interrupts (https://docs.python.org/3/library/signal.html)

- **[S]** Section "General rules": Python translates SIGINT into
  `KeyboardInterrupt` by default.
- **[S]** `Future.cancel()` and `shutdown(cancel_futures=True)` cancel work that
  has not started, not running calls. `ThreadPoolExecutor` threads are joined
  before interpreter exit; `shutdown(wait=False)` does not guarantee prompt
  process exit.
- **[R]** Preserve the current interruption contract: do not synthesize a
  `"not run"` result or emit a partial summary/JSON payload. On
  `KeyboardInterrupt`, stop scheduling, cancel queued futures, and propagate
  the interruption. The coordinator must not enqueue another repository from
  a worktree group after interruption or an unexpected worker exception.
  Running work cannot be cancelled by `Future.cancel()`. A terminal Ctrl-C may
  also reach Git children in the foreground process group; do not promise that
  they either continue until timeout or are always terminated as a process
  tree. `subprocess.run(timeout=...)` documents the direct child, while cleanup
  of descendant helpers remains unverified (section 5.2). Executor shutdown
  behavior and the partial repository state left by interruption need tests.

## 6. Git primary-source findings

- **[S]** `git pull` (https://git-scm.com/docs/git-pull), option `--autostash`:
  creates a temporary stash and applies it after integration; the docs warn
  that application after a successful merge may conflict. This CLI runs
  `git pull --rebase --autostash`, so do not assume merge-specific
  `MERGE_AUTOSTASH` bookkeeping. `git rebase --autostash`
  (https://git-scm.com/docs/git-rebase) documents saving an autostash that
  cannot be cleanly reapplied; the project separately checks for unmerged index
  entries after a successful pull.
- **[S]** `git pull`, option `--rebase[=...]`: `true` rebases the current branch
  on top of the upstream branch after fetching. In the description, conflicts
  can be aborted with `git merge --abort` or `git rebase --abort`.
- **[S]** `git rebase` (https://git-scm.com/docs/git-rebase), option
  `--autostash`: a temporary stash entry that cannot be applied cleanly is saved
  to the stash list.
- **[S]** `git fetch` (https://git-scm.com/docs/git-fetch), option
  `-j <n>`/`--jobs=<n>`: parallelizes fetches "if the `--multiple` option was
  specified, the different remotes will be fetched in parallel. If multiple
  submodules are fetched, they will be fetched in parallel." "By default
  fetches are performed sequentially." `git config` (https://git-scm.com/docs/git-config),
  `fetch.parallel`: the maximal number of fetch operations in parallel
  (submodules, or remotes with `--multiple`), default 1.
- **[R]** Git's own parallelism works within a single repository (remotes and
  submodules). It does not parallelize across independent repositories, so
  `--jobs` does not replace application-level concurrency here. Also, the
  project forbids altering global Git config, so `fetch.parallel` should not be
  set by the tool.
- **[S]** `git` (https://git-scm.com/docs/git), "ENVIRONMENT VARIABLES":
  `GIT_TERMINAL_PROMPT=false` stops Git's own terminal prompts, and the
  `--no-optional-locks` option and `GIT_OPTIONAL_LOCKS=0` skip optional
  operations that require locks. `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` can
  redirect config (the project's tests rely on this kind of isolation).
- **[S]** `git worktree` (https://git-scm.com/docs/git-worktree),
  "DESCRIPTION": a linked worktree shares everything with the main repository
  "except per-worktree files such as HEAD, index, etc.". In particular, do not
  treat an `index.lock` as shared across linked worktrees; their indexes are
  per-worktree. Refs and the object database are shared.
  `gitrepository-layout` ("worktrees"): linked-worktree admin data lives under
  `worktrees/`.
- **[R]** Consequence: two configured paths that are linked worktrees of one
  repository share refs and objects. Serialize their pulls by a normalized
  common-Git-directory key (for example, `git rev-parse --git-common-dir`);
  normalize a relative result against the worktree root. If the probe fails,
  treat that repo as its own group and let normal validation produce its
  per-repo result. Skip the grouping probe for `--jobs 1` so the existing
  sequential path adds no Git calls.
- **[R]** Use the common directory only as a serialization key, never as a
  deduplication key: distinct linked worktrees are distinct selected
  repositories and each must get its own pull and result. The exact behavior of
  simultaneous ref-lock or object-maintenance contention should be tested with
  temporary linked worktrees and local bare remotes; do not infer `index.lock`
  collisions from linked-worktree sharing.
- **[R]** `--jobs > 1` can also expose concurrent SSH/GPG prompts on a shared
  terminal: `GIT_TERMINAL_PROMPT=0` does not configure external SSH or pinentry
  behavior. Document that concurrent mode assumes usable agent-backed or
  otherwise non-interactive authentication; do not silently set OpenSSH
  `BatchMode` or change Git settings.

## 7. Platform and remote limits

- **[S]** GitHub Docs, "Repository limits" (https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits),
  Git read operations: "recommended maximum limit is 15 operations per second
  per repository". Large amounts of read operations can throttle a repository,
  and automated processes can degrade performance.
- **[R]** That limit is per repository and aimed at heavy automation. A handful
  of workers fetching distinct repositories is far below it. It does not bind
  your pool size, but it is a reason to keep retries backed off and capped.
- **[S]** OpenBSD `sshd_config(5)`, `MaxStartups` (https://man.openbsd.org/sshd_config):
  default `10:30:100`, which means connection attempts are randomly refused once
  10 unauthenticated connections exist. This is the OpenSSH server default and
  **[U]** GitHub's own SSH server configuration was not verified. Treat it only
  as a generic reason to keep worker counts low when many repos share one SSH
  host.
- **[U]** Per-user OS limits (file descriptors, process counts) and laptop
  CPU/disk contention from rebases were not sourced. Cap workers conservatively
  and measure instead.

## 8. Comparison for independent Git pulls

| Criterion | ThreadPoolExecutor | asyncio subprocess | ProcessPoolExecutor |
| --- | --- | --- | --- |
| Overlaps waiting on `git` children | Yes (GIL released while blocked, per threading docs 5.5) | Yes | Yes, with heavy overhead |
| Reuses existing `_git()`, `RepoError`, tests | **Yes, unchanged** | No, needs async rewrite | Mostly, but needs pickling and `__main__` guard |
| Bounding | `max_workers` | `Semaphore` | `max_workers` (Windows <= 61) |
| Per-repo error isolation | Exceptions stay in each Future | `gather(return_exceptions=True)`; `TaskGroup` is fail-fast | `BrokenProcessPool` can fail all pending |
| Cancellation | Only unstarted futures; running ones finish (docs 5.1) | `Task.cancel()` needs explicit child kill | Same limits plus pool breakage |
| Timeout | Existing `subprocess.run(timeout=)` | `asyncio.timeout` + manual kill | Existing, inside worker |
| Fit with "smallest correct change" | **Best** | Poor | Poor |

**[R]** Recommendation: ThreadPoolExecutor.

## 9. Selected implementation design

1. **Option**: `pull --jobs N`/`-j N`, as a pull-subparser-only option like
   `--quiet`. **[R]** When omitted, choose `min(8, max(2, available CPUs))`;
   use `os.process_cpu_count()` when available and fall back to
   `os.cpu_count()` for Python 3.12. The minimum of 2 allows overlap for
   I/O-bound pulls even on a one-CPU process. Limit the effective workers to
   selected repos and distinct Git common directories. Reject explicit values
   outside `1..8` with an argparse error and exit 2 rather than silently
   clamping. This cap is a conservative product choice, not a Python or Git
   limit.
2. **Scheduling**:
   - With `jobs == 1`, retain the current `for repo in repos` loop exactly.
   - For `jobs > 1`, determine a normalized common-Git-directory key for each
     valid worktree. Run the preflight probe before submitting mutations; a
     failed probe assigns that repo a unique group so `_mutate_repo()` still
     returns its normal per-repo failure. Do not deduplicate distinct worktrees
     that share a group.
   - Use one future per repo, with at most one active future per common-dir
     group. The coordinator submits the next repo in a group only after the
     previous future in that group completes. This serializes linked worktrees
     without delaying per-repo completion reporting until a whole group ends.
   - Replenish the active-future set with
     `wait(..., return_when=FIRST_COMPLETED)` or a new `as_completed()` iterator
     after each completion; do not assume an iterator created before later
     submissions will watch those new futures.
3. **Dry-run**: keep `--dry-run` read-only and preserve each repo's planned
   result. Parallel scheduling may overlap preflight, but must never execute a
   mutating Git command.
4. **Ordering**: in parallel mode, cross-repository operations and live result
   lines may start/finish out of selection order. Preserve each repo's internal
   operation order. Print each completed repo from the main thread as its
   future completes; keep JSON results and counts in selection order and retain
   the existing failure-first summary-table presentation. For `jobs == 1`,
   retain existing operation and progress order.
5. **Failures**: workers catch only expected `RepoError` and return a failed
   result. If `future.result()` raises an unexpected exception, the coordinator
   must stop scheduling, cancel queued futures, and propagate rather than hide
   the error or let queued pulls continue without reported results. Ensure
   exception unwinding cannot schedule group successors; executor cleanup still
   waits for already-running futures.
6. **Interruption**: on `KeyboardInterrupt`, stop scheduling and cancel queued
   futures, then propagate the interruption as the current CLI does. Do not
   invent a `"not run"` status or emit partial JSON/summary output. Running
   futures cannot be cancelled; executor shutdown waits for them, and
   `shutdown(wait=False)` does not make the interpreter exit before them.
   Terminal Ctrl-C may also interrupt active Git children, while other forms of
   interruption may not. Do not promise prompt whole-process-tree termination;
   see the unverified timeout cleanup limitation in sections 5.2 and 10.
7. **Retry and backoff** (**[R]**, no primary source defines transient Git
   errors):
   - Do not add retries in the first version. A `git pull` combines fetch with
     local integration; `_git()` does not reliably identify whether a failure
     happened before local mutation.
   - Never blindly retry conflicts, unmerged entries, dirty-state errors,
     authentication failures, missing repos or refs, or a timeout from the
     whole `git pull`. A timed-out `git pull --rebase --autostash` may leave
     rebase/autostash state behind, conflicting with "no automatic conflict
     recovery".
   - A future retry design could split fetch from integration and retry only a
     clearly pre-integration fetch phase with capped exponential backoff and
     jitter. That changes the documented `pull` operation and requires a
     deliberate design decision and tests; stderr alone is not a reliable
     classifier.
8. **Testing** (per `AGENTS.md`): failing tests first. Use temporary repos and
   local bare remotes, with isolated HOME/XDG/global/system Git config. Never
   use real repos or network remotes. Cover: selection-order JSON under reversed
   completion timing; per-repo progress while another repo in the same group is
   blocked; one failure among successes; timeout behavior; two distinct linked
   worktrees both pulled but serialized; invalid-path group fallback; no group
   probe and unchanged progress behavior for `--jobs 1`; `--jobs` validation;
   JSON and `--quiet`; interruption and unexpected worker exceptions. Use
   `threading.Event`/`Barrier` at the subprocess seam for deterministic
   scheduling, not sleeps.
9. **Docs**: document `--jobs`, the auto default, the `jobs == 1`
   compatibility behavior, parallel operation ordering, and live output in
   `README.md`, `docs/design.md`, and `AGENTS.md`.

## 10. Local benchmark and open questions

- **[M]** On macOS with Python 3.12.8, 12 temporary clones of one local bare
  remote were pulled five times each, alternating `--jobs 1` with default auto
  mode. Median wall time was 1.594 seconds sequential and 0.569 seconds auto,
  a 2.80x speedup. Every pull succeeded. This measures local no-op fetches and
  CLI/process overhead, not GitHub or SSH network latency, large transfers, or
  nontrivial rebases. No managed repos or network remotes were used.

- The cmux surface exposed the post and visible comments; hidden or collapsed
  comments were not evaluated.
- Speedup depends on remotes and local storage. Real Python's timings are for
  HTTP downloads and do not predict Git subprocess performance.
- Cleanup of descendant processes after timeout/interruption, precise shared
  ref-lock contention, and GitHub's SSH-server limits were not verified from
  primary sources.
- Python docs were read at the moving `/3/` URL (3.15.2 at fetch time). The
  behavior cited with "Changed in" notes applies to 3.12+, but small differences
  from 3.12 may exist in unmarked text.

## Sources

- Real Python, "Speed Up Your Python Program With Concurrency" (https://realpython.com/python-concurrency/)
- Reddit, "Learn concurrency - a deep dive into multithreading with Python"
  (https://www.reddit.com/r/Python/comments/1szaytm/learn_concurrency_a_deep_dive_into_multithreading/)
- Geekuni blog, linked from the Reddit post
  (https://blog.geekuni.com/2026/04/python-concurrency.html), secondary source
- Python docs: `concurrent.futures`, `subprocess`, `asyncio-subprocess`, `asyncio-task`, `asyncio-sync`, `asyncio-eventloop`, `multiprocessing`, `threading`, `signal` (all under https://docs.python.org/3/library/)
- Git docs: `git-pull`, `git-fetch`, `git-rebase`, `git-config`, `git`, `git-worktree`, `git-rev-parse`, `gitrepository-layout` (https://git-scm.com/docs/)
- GitHub Docs, "Repository limits" (https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits)
- OpenBSD `sshd_config(5)` (https://man.openbsd.org/sshd_config)
- OpenBSD `ssh_config(5)`, `BatchMode` (https://man.openbsd.org/ssh_config#BatchMode)
- Repository: `AGENTS.md`, `docs/design.md`, `src/my_repos_ctl/cli.py`
