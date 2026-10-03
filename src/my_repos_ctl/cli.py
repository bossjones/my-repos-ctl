"""Command-line interface for explicitly configured local repositories."""

import argparse
import json
import math
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from yaml.constructor import ConstructorError


class ConfigError(Exception):
    """Invalid or unreadable repository configuration."""


class RepoError(Exception):
    """A repository operation could not be completed."""


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
    except yaml.YAMLError as exc:
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


def _git(repo: Repo, args: list[str], timeout: float) -> str:
    argv = ["git", "--no-pager", "-C", str(repo.path), *args]
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
            f"Git exited {completed.returncode}: {detail or 'no diagnostics'}"
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


def _report(command: str, results: list[dict[str, object]], json_output: bool) -> int:
    summary = {
        "total": len(results),
        "ok": sum(result["status"] == "ok" for result in results),
        "failed": sum(result["status"] == "failed" for result in results),
        "planned": sum(result["status"] == "planned" for result in results),
    }
    if json_output:
        print(json.dumps({"command": command, "results": results, "summary": summary}))
    else:
        for result in results:
            print(
                f"{result['name']} ({result['path']}): "
                f"{result['status']} - {result['message']}"
            )
    return 1 if summary["failed"] else 0


def main(argv: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(argv)
    config = options.after_config or options.before_config
    names = (options.before_repos or []) + (options.after_repos or [])
    timeout = options.after_timeout
    if timeout is None:
        timeout = options.before_timeout
    if timeout is None:
        timeout = 300.0
    try:
        if config is None:
            try:
                config = Path.home() / ".my-repo-ctl.yml"
            except RuntimeError as exc:
                raise ConfigError(
                    f"Cannot determine the home configuration: {exc}"
                ) from exc
        repos = select_repos(load_config(config), names)
    except ConfigError as exc:
        print(f"my-repos-ctl: {exc}", file=sys.stderr)
        return 2
    results: list[dict[str, object]] = []
    for repo in repos:
        result: dict[str, object]
        try:
            result = _read_repo(repo, options.command, timeout)
        except RepoError as exc:
            result = {
                "name": repo.name,
                "path": str(repo.path),
                "status": "failed",
                "message": str(exc),
            }
        results.append(result)
    return _report(options.command, results, options.before_json or options.after_json)
