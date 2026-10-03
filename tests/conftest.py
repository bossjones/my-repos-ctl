import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    home.mkdir()
    xdg.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("GIT_EDITOR", "true")
    monkeypatch.setenv("GIT_SEQUENCE_EDITOR", "true")
    monkeypatch.setenv("GIT_PAGER", "cat")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture
def git():
    def run(path, *args, check=True):
        return subprocess.run(
            ["git", "-C", str(path), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
            check=check,
        )

    return run


@pytest.fixture
def configure_git(git):
    def configure(path):
        git(path, "config", "--local", "user.name", "Test User")
        git(path, "config", "--local", "user.email", "test@example.invalid")
        git(path, "config", "--local", "commit.gpgsign", "false")
        git(path, "config", "--local", "tag.gpgsign", "false")
        git(path, "config", "--local", "core.autocrlf", "false")
        git(path, "config", "--local", "core.hooksPath", os.devnull)
        git(path, "config", "--local", "pull.rebase", "false")
        git(path, "config", "--local", "rebase.autoStash", "false")
        git(path, "config", "--local", "checkout.defaultRemote", "origin")

    return configure


@pytest.fixture
def repo_factory(tmp_path, git, configure_git):
    def create(name="repo"):
        path = tmp_path / name
        path.mkdir()
        git(path, "init", "--initial-branch=main")
        configure_git(path)
        (path / "README.txt").write_text("initial\n", encoding="utf-8")
        git(path, "add", "README.txt")
        git(path, "commit", "-m", "Initial commit")
        return path

    return create


@pytest.fixture
def config_factory(tmp_path):
    counter = 0

    def create(repos, path=None):
        nonlocal counter
        counter += 1
        path = path or tmp_path / f"config-{counter}.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"repos": {name: str(repo) for name, repo in repos.items()}}),
            encoding="utf-8",
        )
        return path

    return create


@pytest.fixture
def remote_pair(tmp_path, repo_factory, configure_git, git):
    bare = tmp_path / "remote.git"
    bare.mkdir()
    git(bare, "init", "--bare", "--initial-branch=main")
    seed = repo_factory("seed")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "--set-upstream", "origin", "main")
    local = tmp_path / "local"
    git(tmp_path, "clone", str(bare), str(local))
    configure_git(local)
    return SimpleNamespace(bare=bare, seed=seed, local=local)


@pytest.fixture
def advance_remote(git):
    def advance(pair, filename="remote.txt", content="remote update\n"):
        (pair.seed / filename).write_text(content, encoding="utf-8")
        git(pair.seed, "add", filename)
        git(pair.seed, "commit", "-m", "Remote update")
        git(pair.seed, "push", "origin", "main")
        return git(pair.seed, "rev-parse", "HEAD").stdout.strip()

    return advance


@pytest.fixture
def snapshot(git):
    def capture(path):
        files = {
            str(file.relative_to(path)): file.read_bytes()
            for file in path.rglob("*")
            if ".git" not in file.relative_to(path).parts and file.is_file()
        }
        return (
            git(path, "rev-parse", "HEAD").stdout,
            git(path, "symbolic-ref", "-q", "HEAD", check=False).stdout,
            git(path, "show-ref").stdout,
            git(path, "status", "--porcelain=v1", "--untracked-files=all").stdout,
            git(path, "ls-files", "--unmerged").stdout,
            files,
        )

    return capture


@pytest.fixture
def invoke_cli(capsys):
    def invoke(argv):
        from my_repos_ctl.cli import main

        exit_code = main(argv)
        captured = capsys.readouterr()
        assert type(exit_code) is int
        return exit_code, captured

    return invoke


@pytest.fixture
def assert_json():
    def validate(captured, command, *, total, ok=0, failed=0, planned=0):
        payload = json.loads(captured.out)
        assert set(payload) == {"command", "results", "summary"}
        assert payload["command"] == command
        assert payload["summary"] == {
            "total": total,
            "ok": ok,
            "failed": failed,
            "planned": planned,
        }
        assert all(type(value) is int for value in payload["summary"].values())
        assert total == ok + failed + planned
        assert isinstance(payload["results"], list)
        assert len(payload["results"]) == total
        counts = {"ok": 0, "failed": 0, "planned": 0}
        for result in payload["results"]:
            assert {"name", "path", "status", "message"} <= set(result)
            assert isinstance(result["name"], str) and result["name"]
            assert isinstance(result["path"], str)
            assert Path(result["path"]).is_absolute()
            assert isinstance(result["message"], str)
            assert result["status"] in counts
            counts[result["status"]] += 1
            if result["status"] == "failed":
                assert result["message"].strip()
        assert counts == {"ok": ok, "failed": failed, "planned": planned}
        return payload["results"]

    return validate
