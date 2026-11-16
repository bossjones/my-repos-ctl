import json
import subprocess
import threading
from pathlib import Path

import pytest


@pytest.mark.mutation
def test_default_pull_runs_independent_repos_concurrently_in_json_order(
    remote_pair, tmp_path, git, configure_git, config_factory, monkeypatch, invoke_cli
):
    from my_repos_ctl import cli

    second = tmp_path / "second"
    git(tmp_path, "clone", str(remote_pair.bare), str(second))
    configure_git(second)
    config = config_factory({"first": remote_pair.local, "second": second})
    original_run = subprocess.run
    rendezvous = threading.Barrier(2)
    second_finished = threading.Event()
    completion_order = []
    completion_lock = threading.Lock()

    monkeypatch.setattr(cli.os, "cpu_count", lambda: 1)
    monkeypatch.setattr(cli.os, "process_cpu_count", lambda: 1, raising=False)

    def track_pull(argv, **kwargs):
        if "pull" not in argv:
            return original_run(argv, **kwargs)
        path = Path(argv[argv.index("-C") + 1])
        completed = original_run(argv, **kwargs)
        rendezvous.wait(timeout=3)
        if path == remote_pair.local:
            assert second_finished.wait(timeout=3)
        else:
            second_finished.set()
        with completion_lock:
            completion_order.append(path)
        return completed

    monkeypatch.setattr(subprocess, "run", track_pull)
    code, captured = invoke_cli(["--config", str(config), "pull", "--json"])

    assert code == 0
    results = json.loads(captured.out)["results"]
    assert [result["name"] for result in results] == ["first", "second"]
    assert completion_order == [second, remote_pair.local]


@pytest.mark.mutation
def test_parallel_pull_serializes_linked_worktrees(
    remote_pair,
    tmp_path,
    git,
    config_factory,
    monkeypatch,
    capsys,
):
    from my_repos_ctl import cli

    linked = tmp_path / "linked"
    git(
        remote_pair.local,
        "worktree",
        "add",
        "--track",
        "-b",
        "linked",
        str(linked),
        "origin/main",
    )
    config = config_factory({"main": remote_pair.local, "linked": linked})
    main_started = threading.Event()
    linked_started = threading.Event()
    release_main = threading.Event()
    original_run = subprocess.run
    monkeypatch.setattr(cli.os, "cpu_count", lambda: 2)
    monkeypatch.setattr(cli.os, "process_cpu_count", lambda: 2, raising=False)

    def serialize_linked_pulls(argv, **kwargs):
        if "pull" in argv:
            path = Path(argv[argv.index("-C") + 1])
            if path == remote_pair.local:
                main_started.set()
                assert release_main.wait(timeout=5)
            elif path == linked:
                linked_started.set()
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", serialize_linked_pulls)
    exit_codes = []
    errors = []

    def run_cli():
        try:
            exit_codes.append(
                cli.main(["--config", str(config), "pull", "--jobs", "2", "--json"])
            )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_cli)
    worker.start()
    try:
        assert main_started.wait(timeout=3)
        linked_started_while_main_active = linked_started.wait(timeout=0.25)
    finally:
        release_main.set()
        worker.join(timeout=10)

    assert not worker.is_alive()
    assert not errors
    assert not linked_started_while_main_active
    assert exit_codes == [0]
    results = json.loads(capsys.readouterr().out)["results"]
    assert [result["name"] for result in results] == ["main", "linked"]


def test_default_parallel_dry_run_does_not_mutate_repositories(
    remote_pair,
    tmp_path,
    git,
    configure_git,
    config_factory,
    snapshot,
    monkeypatch,
    invoke_cli,
    assert_json,
):
    second = tmp_path / "second"
    git(tmp_path, "clone", str(remote_pair.bare), str(second))
    configure_git(second)
    repos = {"first": remote_pair.local, "second": second}
    config = config_factory(repos)
    before = {path: snapshot(path) for path in repos.values()}
    original_run = subprocess.run

    def forbid_mutations(argv, **kwargs):
        assert not any(
            operation in argv for operation in ("pull", "fetch", "switch", "reset")
        )
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", forbid_mutations)
    code, captured = invoke_cli(
        ["--config", str(config), "pull", "--dry-run", "--json"]
    )

    assert code == 0
    results = assert_json(captured, "pull", total=2, planned=2)
    assert [result["name"] for result in results] == ["first", "second"]
    assert {path: snapshot(path) for path in repos.values()} == before


@pytest.mark.mutation
def test_unexpected_worker_error_does_not_start_queued_pulls(
    remote_pair,
    tmp_path,
    git,
    configure_git,
    config_factory,
    monkeypatch,
):
    from my_repos_ctl import cli

    second = tmp_path / "second"
    third = tmp_path / "third"
    for path in (second, third):
        git(tmp_path, "clone", str(remote_pair.bare), str(path))
        configure_git(path)
    config = config_factory(
        {"first": remote_pair.local, "second": second, "third": third}
    )
    first_started = threading.Event()
    second_started = threading.Event()
    third_started = threading.Event()
    release_second = threading.Event()
    original_run = subprocess.run
    monkeypatch.setattr(cli.os, "cpu_count", lambda: 2)
    monkeypatch.setattr(cli.os, "process_cpu_count", lambda: 2, raising=False)

    def fail_first_pull(argv, **kwargs):
        if "pull" in argv:
            path = Path(argv[argv.index("-C") + 1])
            if path == remote_pair.local:
                first_started.set()
                raise RuntimeError("unexpected worker failure")
            if path == second:
                second_started.set()
                assert release_second.wait(timeout=5)
            elif path == third:
                third_started.set()
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_first_pull)
    errors = []

    def run_cli():
        try:
            cli.main(["--config", str(config), "pull", "--jobs", "2", "--json"])
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_cli)
    worker.start()
    try:
        assert first_started.wait(timeout=3)
        assert second_started.wait(timeout=3)
        third_started_while_second_active = third_started.wait(timeout=0.25)
    finally:
        release_second.set()
        worker.join(timeout=10)

    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
    assert not third_started_while_second_active
    assert not third_started.is_set()
