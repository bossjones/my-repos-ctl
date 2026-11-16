"""Command-line interface for explicitly configured local repositories."""

import argparse
import json
import math
import os
import shlex
import subprocess
import sys
from collections import deque
from collections.abc import Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table
from rich.text import Text
from yaml.constructor import ConstructorError

_PULL_LABELS = {
    "ok": ("pulled", "green"),
    "failed": ("failed", "red"),
    "planned": ("planned", "yellow"),
}
_MAX_PULL_JOBS = 8


class ConfigError(Exception):
    """Invalid or unreadable repository configuration."""


class RepoError(Exception):
    """A repository operation could not be completed."""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


@dataclass(frozen=True)
class Repo:
    name: str
    path: Path


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(
        self, node: yaml.nodes.MappingNode, deep: bool = False
    ) -> dict[object, object]:
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ConstructorError(
                    None, None, "Mapping keys must be hashable", key_node.start_mark
                ) from exc
            if duplicate:
                raise ConstructorError(
                    None, None, f"Duplicate YAML key: {key!r}", key_node.start_mark
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def load_config(path: Path) -> list[Repo]:
    try:
        config_path = path.expanduser().resolve()
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        raise ConfigError(f"Cannot load configuration {path}: {exc}") from exc
    try:
        document = yaml.load(text, Loader=_UniqueKeyLoader)
    except (yaml.YAMLError, ValueError, OverflowError) as exc:
        raise ConfigError(f"Invalid YAML in configuration {path}: {exc}") from exc
    if not isinstance(document, dict) or set(document) != {"repos"}:
        raise ConfigError("Configuration must contain only the 'repos' field")
    entries = document["repos"]
    if not isinstance(entries, dict) or not entries:
        raise ConfigError("'repos' must be a nonempty mapping of names to paths")
    repos: list[Repo] = []
    paths: set[Path] = set()
    for name, value in entries.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("Repository names must be nonempty strings")
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"Repository {name!r} must have a nonempty string path")
        try:
            repo_path = Path(value).expanduser()
            if not repo_path.is_absolute():
                repo_path = config_path.parent / repo_path
            repo_path = repo_path.resolve()
        except (OSError, ValueError, RuntimeError) as exc:
            raise ConfigError(
                f"Cannot resolve path for repository {name!r}: {exc}"
            ) from exc
        if repo_path in paths:
            raise ConfigError(f"Duplicate resolved repository path: {repo_path}")
        paths.add(repo_path)
        repos.append(Repo(name=name, path=repo_path))
    return repos


def select_repos(repos: list[Repo], names: list[str]) -> list[Repo]:
    if not names:
        return list(repos)
    by_name = {repo.name: repo for repo in repos}
    selected: list[Repo] = []
    for name in dict.fromkeys(names):
        if name not in by_name:
            raise ConfigError(f"Unknown repository name: {name!r}")
        selected.append(by_name[name])
    return selected


def _git_command(repo: Repo | None, args: list[str]) -> list[str]:
    argv = ["git", "--no-pager"]
    if repo is not None:
        argv.extend(["-C", str(repo.path)])
    return [*argv, *args]


def _git(repo: Repo | None, args: list[str], timeout: float) -> str:
    argv = _git_command(repo, args)
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            shell=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
        )
    except subprocess.TimeoutExpired as exc:
        detail = _diagnostics(exc.stdout, exc.stderr)
        raise RepoError(f"Git timed out after {timeout:g} seconds: {detail}") from exc
    except UnicodeDecodeError as exc:
        raise RepoError(
            f"Cannot decode Git output as {exc.encoding}: "
            f"{exc.reason} at byte {exc.start}. "
            f"Rename affected Git refs or paths to names valid in {exc.encoding}."
        ) from exc
    except OSError as exc:
        raise RepoError(f"Cannot run Git: {exc}") from exc
    if completed.returncode != 0:
        detail = _diagnostics(completed.stdout, completed.stderr)
        raise RepoError(
            f"Git exited {completed.returncode}: {detail or 'no diagnostics'}",
            returncode=completed.returncode,
        )
    return completed.stdout


def _diagnostics(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    parts: list[str] = []
    for value in (stderr, stdout):
        if isinstance(value, bytes):
            value = value.decode(errors="replace")
        if value and value.strip():
            parts.append(value.strip())
    return "\n".join(parts)


def _timeout(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Timeout must be a positive finite number"
        ) from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("Timeout must be a positive finite number")
    return seconds


def _jobs(value: str) -> int:
    try:
        jobs = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Jobs must be an integer between 1 and {_MAX_PULL_JOBS}"
        ) from exc
    if not 1 <= jobs <= _MAX_PULL_JOBS:
        raise argparse.ArgumentTypeError(
            f"Jobs must be an integer between 1 and {_MAX_PULL_JOBS}"
        )
    return jobs


def _auto_jobs() -> int:
    cpu_count = os.cpu_count()
    process_cpu_count = getattr(os, "process_cpu_count", None)
    if callable(process_cpu_count):
        cpu_count = process_cpu_count() or cpu_count
    return min(_MAX_PULL_JOBS, max(2, cpu_count or 1))


def _add_options(parser: argparse.ArgumentParser, prefix: str) -> None:
    parser.add_argument("--config", type=Path, dest=f"{prefix}_config")
    parser.add_argument(
        "--repo", action="append", metavar="NAME", dest=f"{prefix}_repos"
    )
    parser.add_argument("--json", action="store_true", dest=f"{prefix}_json")
    parser.add_argument(
        "--timeout", type=_timeout, metavar="SECONDS", dest=f"{prefix}_timeout"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="my-repos-ctl",
        description="Manage explicitly configured local repositories",
    )
    _add_options(parser, "before")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("list", "status", "branches", "pull", "fetch", "checkout"):
        subparser = commands.add_parser(command)
        _add_options(subparser, "after")
        if command in ("pull", "fetch", "checkout"):
            subparser.add_argument("--dry-run", action="store_true")
        if command == "pull":
            subparser.add_argument(
                "--quiet",
                "-q",
                action="store_true",
                help="Suppress live per-repo output; show only the pull summary table.",
            )
            subparser.add_argument(
                "--jobs",
                "-j",
                type=_jobs,
                metavar="N",
                help=(
                    "Concurrent repository pulls (1-8; default: auto based "
                    "on available CPUs)."
                ),
            )
        if command == "checkout":
            subparser.add_argument("branch")
    return parser


def _validate_worktree(repo: Repo, timeout: float) -> None:
    if _git(repo, ["rev-parse", "--is-bare-repository"], timeout).strip() != "false":
        raise RepoError("Repository must be a non-bare Git worktree")
    root = _git(repo, ["rev-parse", "--show-toplevel"], timeout).removesuffix("\n")
    if not root:
        raise RepoError("Git did not report a worktree root")
    try:
        actual_root = Path(root).resolve()
        configured_root = repo.path.resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise RepoError(f"Cannot resolve worktree root: {exc}") from exc
    if actual_root != configured_root:
        raise RepoError(f"Configured path is not the Git worktree root: {actual_root}")


def _validate_branch(branch: str, timeout: float) -> None:
    if branch.startswith(("-", "@{")) or "\x00" in branch:
        raise ConfigError(
            f"Invalid branch name {branch!r}: "
            "use a literal branch name, not an option or history shorthand"
        )
    try:
        _git(None, ["check-ref-format", "--branch", branch], timeout)
    except RepoError as exc:
        if exc.returncode is None or exc.returncode < 0:
            raise
        raise ConfigError(f"Cannot validate branch name {branch!r}: {exc}") from exc


def _checkout_args(repo: Repo, branch: str, timeout: float) -> list[str]:
    if _git(repo, ["status", "--porcelain=v1", "--untracked-files=all"], timeout):
        raise RepoError(
            "Refusing to switch a dirty worktree, including untracked files. "
            "Commit or otherwise preserve your changes before retrying."
        )
    refs = set(
        _git(
            repo,
            ["for-each-ref", "--format=%(refname)", "refs/heads/", "refs/remotes/"],
            timeout,
        ).splitlines()
    )
    if f"refs/heads/{branch}" in refs:
        return ["switch", branch]
    tracking = [
        f"{remote}/{branch}"
        for remote in _git(repo, ["remote"], timeout).splitlines()
        if f"refs/remotes/{remote}/{branch}" in refs
    ]
    if not tracking:
        raise RepoError(
            f"Branch {branch!r} does not exist locally or as a remote-tracking "
            "branch. Fetch explicitly before retrying if the branch is remote."
        )
    if len(tracking) != 1:
        raise RepoError(
            f"Branch {branch!r} is ambiguous across remote-tracking branches: "
            f"{', '.join(tracking)}. Create the intended local branch explicitly."
        )
    return ["switch", "--track", "-c", branch, "--", f"refs/remotes/{tracking[0]}"]


def _mutate_repo(
    repo: Repo, command: str, branch: str | None, dry_run: bool, timeout: float
) -> dict[str, object]:
    _validate_worktree(repo, timeout)
    if command == "checkout":
        assert branch is not None
        args = _checkout_args(repo, branch, timeout)
    elif command == "pull":
        args = ["pull", "--rebase", "--autostash"]
    else:
        args = ["fetch", "--prune"]
    git_command = _git_command(repo, args)
    if dry_run:
        message = (
            f"Planned {shlex.join(git_command)}. Read-only preflight does not "
            "predict remote availability or merge success."
        )
    else:
        output = _git(repo, args, timeout)
        if command == "pull" and _git(repo, ["ls-files", "--unmerged"], timeout):
            raise RepoError(
                "Pull left unmerged index entries (possible autostash conflict). "
                "Inspect 'git status' and 'git stash list', preserve your changes, "
                "and resolve conflicts manually. No recovery was attempted."
            )
        message = (
            output.strip()
            if command == "pull" and output.strip()
            else f"Completed {shlex.join(git_command)}"
        )
    return {
        "name": repo.name,
        "path": str(repo.path),
        "status": "planned" if dry_run else "ok",
        "message": message,
        "git_command": git_command,
    }


def _read_repo(repo: Repo, command: str, timeout: float) -> dict[str, object]:
    result: dict[str, object] = {
        "name": repo.name,
        "path": str(repo.path),
        "status": "ok",
        "message": "Configured repository",
    }
    if command == "list":
        return result
    if command not in ("status", "branches"):
        raise RepoError(f"{command} is not implemented in the read-only runtime")
    _validate_worktree(repo, timeout)
    if command == "branches":
        branches = _git(
            repo,
            [
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads/",
                "refs/remotes/",
            ],
            timeout,
        ).splitlines()
        result.update(branches=branches, message=", ".join(branches) or "No branches")
    else:
        branch = _git(repo, ["branch", "--show-current"], timeout).strip() or None
        detached = branch is None
        commit = _git(repo, ["rev-parse", "--short", "HEAD"], timeout).strip()
        dirty = bool(
            _git(repo, ["status", "--porcelain=v1", "--untracked-files=all"], timeout)
        )
        state = "dirty" if dirty else "clean"
        label = f"detached at {commit}" if detached else branch
        result.update(
            branch=branch,
            commit=commit,
            dirty=dirty,
            detached=detached,
            message=f"{label}, {state}",
        )
    return result


def _process_repo(
    repo: Repo,
    command: str,
    branch: str | None,
    dry_run: bool,
    timeout: float,
    validation_error: RepoError | None,
) -> dict[str, object]:
    try:
        if validation_error is not None:
            raise validation_error
        if command in ("pull", "fetch", "checkout"):
            return _mutate_repo(repo, command, branch, dry_run, timeout)
        return _read_repo(repo, command, timeout)
    except RepoError as exc:
        return {
            "name": repo.name,
            "path": str(repo.path),
            "status": "failed",
            "message": str(exc),
        }


def _pull_group_key(repo: Repo, index: int, timeout: float) -> tuple[str, str]:
    try:
        common_dir = _git(repo, ["rev-parse", "--git-common-dir"], timeout).strip()
        if not common_dir:
            return ("repo", str(index))
        path = Path(common_dir)
        if not path.is_absolute():
            path = repo.path / path
        return ("git", str(path.resolve()))
    except RepoError:
        return ("repo", str(index))
    except (OSError, ValueError, RuntimeError):
        return ("repo", str(index))


def _run_parallel_pulls(
    repos: list[Repo],
    jobs: int,
    dry_run: bool,
    timeout: float,
    quiet: bool,
    pull_console: Console | None,
) -> list[dict[str, object]]:
    groups: dict[tuple[str, str], deque[tuple[int, Repo]]] = {}
    for index, repo in enumerate(repos):
        key = _pull_group_key(repo, index, timeout)
        groups.setdefault(key, deque()).append((index, repo))

    max_workers = min(jobs, len(groups))
    results: dict[int, dict[str, object]] = {}
    active: dict[Future[dict[str, object]], tuple[int, tuple[str, str]]] = {}
    active_groups: set[tuple[str, str]] = set()
    executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit_ready() -> None:
        while len(active) < max_workers:
            ready = [
                (pending[0][0], key)
                for key, pending in groups.items()
                if pending and key not in active_groups
            ]
            if not ready:
                return
            index, key = min(ready)
            repo_index, repo = groups[key].popleft()
            future = executor.submit(
                _process_repo, repo, "pull", None, dry_run, timeout, None
            )
            active[future] = (repo_index, key)
            active_groups.add(key)

    try:
        submit_ready()
        while active:
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda item: active[item][0]):
                index, key = active.pop(future)
                active_groups.remove(key)
                result = future.result()
                results[index] = result
                if pull_console is not None and not quiet:
                    _print_pull_result(result, pull_console)
            submit_ready()
    except BaseException:
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
    return [results[index] for index in range(len(repos))]


def _pull_excerpt(message: str, limit: int = 160) -> str:
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        return ""
    detail = next(
        (line for line in lines if line.lower().startswith(("error:", "fatal:"))),
        lines[-1],
    )
    return detail if len(detail) <= limit else detail[: limit - 1] + "\u2026"


def _build_pull_summary_table(results: list[dict[str, object]]) -> Table:
    order = {"failed": 0, "ok": 1, "planned": 2}
    table = Table(title="Pull summary")
    table.add_column("Status", no_wrap=True)
    table.add_column("Repo", overflow="fold")
    table.add_column("Detail", overflow="fold")
    for result in sorted(results, key=lambda item: order[str(item["status"])]):
        label, style = _PULL_LABELS[str(result["status"])]
        table.add_row(
            Text(label, style=style),
            Text(f"{result['name']}\n{result['path']}"),
            Text(_pull_excerpt(str(result["message"]))),
        )
    return table


def _print_pull_result(result: dict[str, object], console: Console) -> None:
    label, style = _PULL_LABELS[str(result["status"])]
    line = Text(f"{label} ", style=style)
    line.append(f"{result['name']} ({result['path']}): {result['message']}", style="")
    console.print(line, soft_wrap=True)


def _report(
    command: str,
    results: list[dict[str, object]],
    json_output: bool,
    pull_console: Console | None = None,
) -> int:
    summary = {
        "total": len(results),
        "ok": sum(result["status"] == "ok" for result in results),
        "failed": sum(result["status"] == "failed" for result in results),
        "planned": sum(result["status"] == "planned" for result in results),
    }
    if json_output:
        print(json.dumps({"command": command, "results": results, "summary": summary}))
    elif command == "pull":
        console = pull_console if pull_console is not None else Console(highlight=False)
        console.print(_build_pull_summary_table(results))
        console.print(
            Text(
                f"Summary: {summary['total']} total, {summary['ok']} pulled, "
                f"{summary['failed']} failed, {summary['planned']} planned"
            )
        )
    else:
        for result in results:
            print(
                f"{result['name']} ({result['path']}): "
                f"{result['status']} - {result['message']}"
            )
        if command in ("pull", "fetch", "checkout"):
            print(
                f"Summary: {summary['total']} total, {summary['ok']} ok, "
                f"{summary['failed']} failed, {summary['planned']} planned"
            )
    return 1 if summary["failed"] else 0


def main(argv: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(argv)
    jobs = (
        (options.jobs if options.jobs is not None else _auto_jobs())
        if options.command == "pull"
        else None
    )
    config = options.after_config or options.before_config
    names = (options.before_repos or []) + (options.after_repos or [])
    timeout = options.after_timeout
    if timeout is None:
        timeout = options.before_timeout
    if timeout is None:
        timeout = 300.0
    dry_run = (
        options.dry_run if options.command in ("pull", "fetch", "checkout") else False
    )
    branch = options.branch if options.command == "checkout" else None
    validation_error: RepoError | None = None
    try:
        if config is None:
            try:
                config = Path.home() / ".my-repo-ctl.yml"
            except RuntimeError as exc:
                raise ConfigError(
                    f"Cannot determine the home configuration: {exc}"
                ) from exc
        repos = select_repos(load_config(config), names)
        if branch is not None:
            try:
                _validate_branch(branch, timeout)
            except RepoError as exc:
                validation_error = exc
    except ConfigError as exc:
        print(f"my-repos-ctl: {exc}", file=sys.stderr)
        return 2
    json_output = options.before_json or options.after_json
    pull_console = (
        Console(highlight=False)
        if options.command == "pull" and not json_output
        else None
    )
    if options.command == "pull" and jobs is not None and jobs > 1 and len(repos) > 1:
        results = _run_parallel_pulls(
            repos, jobs, dry_run, timeout, options.quiet, pull_console
        )
    else:
        results = []
        for repo in repos:
            result = _process_repo(
                repo,
                options.command,
                branch,
                dry_run,
                timeout,
                validation_error,
            )
            results.append(result)
            if pull_console is not None and not options.quiet:
                _print_pull_result(result, pull_console)
    return _report(options.command, results, json_output, pull_console)
