import subprocess

import pytest


@pytest.mark.parametrize("kind", ["missing", "nonrepo", "nested", "bare"])
def test_status_rejects_paths_that_are_not_worktree_roots(
    tmp_path, repo_factory, git, config_factory, invoke_cli, assert_json, kind
):
    path = tmp_path / "invalid"
    if kind == "nonrepo":
        path.mkdir()
    elif kind == "nested":
        path = repo_factory() / "nested"
        path.mkdir()
    elif kind == "bare":
        path.mkdir()
        git(path, "init", "--bare", "--initial-branch=main")
    config = config_factory({"invalid": path})
    code, captured = invoke_cli(["--config", str(config), "status", "--json"])
    assert code == 1
    results = assert_json(captured, "status", total=1, failed=1)
    assert results[0]["path"] == str(path)


def test_status_supports_linked_worktrees(
    tmp_path, repo_factory, git, config_factory, invoke_cli, assert_json
):
    repo = repo_factory()
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-b", "linked-branch", str(linked))
    config = config_factory({"linked": linked})
    code, captured = invoke_cli(["--config", str(config), "status", "--json"])
    assert code == 0
    result = assert_json(captured, "status", total=1, ok=1)[0]
    assert result["path"] == str(linked)
    assert result["branch"] == "linked-branch"
    assert result["detached"] is False
    assert result["dirty"] is False


@pytest.mark.parametrize("state", ["tracked", "untracked", "detached"])
def test_status_reports_branch_short_commit_and_dirty_state(
    repo_factory, git, config_factory, invoke_cli, assert_json, state
):
    repo = repo_factory()
    if state == "tracked":
        (repo / "README.txt").write_text("edited\n", encoding="utf-8")
    elif state == "untracked":
        (repo / "untracked.txt").write_text("new\n", encoding="utf-8")
    elif state == "detached":
        git(repo, "switch", "--detach", "HEAD")
    commit = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    config = config_factory({"one": repo})
    code, captured = invoke_cli(["--config", str(config), "status", "--json"])
    assert code == 0
    result = assert_json(captured, "status", total=1, ok=1)[0]
    assert result["branch"] == (None if state == "detached" else "main")
    assert result["commit"] == commit
    assert result["detached"] is (state == "detached")
    assert result["dirty"] is (state in {"tracked", "untracked"})


@pytest.mark.parametrize("detached", [False, True], ids=["attached", "detached"])
def test_status_uses_actual_head_when_head_named_tag_points_elsewhere(
    repo_factory, git, config_factory, snapshot, invoke_cli, assert_json, detached
):
    repo = repo_factory()
    tag_commit = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "commit", "--allow-empty", "-m", "Actual HEAD")
    actual_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    short_head = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    assert actual_head != tag_commit
    if detached:
        git(repo, "switch", "--detach", actual_head)
    git(repo, "update-ref", "refs/tags/HEAD", tag_commit)
    before = snapshot(repo)
    config = config_factory({"one": repo})

    code, captured = invoke_cli(["--config", str(config), "status", "--json"])

    assert code == 0
    result = assert_json(captured, "status", total=1, ok=1)[0]
    assert result["branch"] == (None if detached else "main")
    assert result["detached"] is detached
    assert result["commit"] == short_head
    assert result["dirty"] is False
    assert snapshot(repo) == before


def test_branches_decode_failure_is_reported_and_later_repo_succeeds(
    repo_factory, git, config_factory, invoke_cli, assert_json
):
    bad = repo_factory("bad")
    good = repo_factory("good")
    commit = git(bad, "rev-parse", "HEAD").stdout.strip().encode("ascii")
    (bad / ".git" / "packed-refs").write_bytes(
        b"# pack-refs with: peeled fully-peeled sorted \n"
        + commit
        + b" refs/heads/raw-\xff\n"
    )
    raw_refs = subprocess.run(
        [
            "git",
            "-C",
            str(bad),
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads/",
        ],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=15,
        check=True,
    )
    assert b"raw-\xff\n" in raw_refs.stdout
    config = config_factory({"bad": bad, "good": good})

    code, captured = invoke_cli(["--config", str(config), "branches", "--json"])

    assert code == 1
    results = assert_json(captured, "branches", total=2, ok=1, failed=1)
    assert [(result["name"], result["status"]) for result in results] == [
        ("bad", "failed"),
        ("good", "ok"),
    ]
    assert set(results[0]) == {"name", "path", "status", "message"}
    assert results[0]["path"] == str(bad)
    diagnostic = results[0]["message"].lower()
    assert "decode" in diagnostic and "git" in diagnostic
    assert "rename" in diagnostic or "encoding" in diagnostic
    assert results[1]["branches"] == ["main"]
    assert not captured.err


def test_branches_lists_local_and_tracking_refs_without_fetching(
    remote_pair, git, config_factory, snapshot, invoke_cli, assert_json
):
    pair = remote_pair
    git(pair.local, "branch", "local-only")
    git(pair.seed, "switch", "-c", "not-yet-fetched")
    git(pair.seed, "push", "origin", "not-yet-fetched")
    before = snapshot(pair.local)
    config = config_factory({"one": pair.local})
    code, captured = invoke_cli(["--config", str(config), "branches", "--json"])
    assert code == 0
    result = assert_json(captured, "branches", total=1, ok=1)[0]
    assert isinstance(result["branches"], list)
    assert all(isinstance(branch, str) for branch in result["branches"])
    assert {"main", "local-only", "origin/main"} <= set(result["branches"])
    assert "origin/not-yet-fetched" not in result["branches"]
    assert snapshot(pair.local) == before


@pytest.mark.mutation
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("pull", ["pull", "--rebase", "--autostash"]),
        ("fetch", ["fetch", "--prune"]),
        ("checkout", ["switch", "feature"]),
    ],
)
def test_dry_run_reports_full_argv_without_changing_head_worktree_or_refs(
    remote_pair,
    git,
    advance_remote,
    config_factory,
    snapshot,
    monkeypatch,
    invoke_cli,
    assert_json,
    command,
    expected,
):
    pair = remote_pair
    advance_remote(pair)
    git(pair.local, "branch", "feature")
    if command != "checkout":
        (pair.local / "README.txt").write_text("local edit\n", encoding="utf-8")
    nested = pair.local / "nested"
    nested.mkdir()
    before = snapshot(pair.local)
    config = config_factory({"one": pair.local})
    args = [command, "feature"] if command == "checkout" else [command]
    original_run = subprocess.run

    def read_only_run(argv, **kwargs):
        mutating = {"pull", "fetch", "switch", "checkout", "reset", "stash", "clean"}
        assert not mutating.intersection(argv), (
            "dry-run must not execute a mutating Git subprocess"
        )
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", read_only_run)
    code, captured = invoke_cli(["--config", str(config), "--json", *args, "--dry-run"])
    assert code == 0
    result = assert_json(captured, command, total=1, planned=1)[0]
    argv = result["git_command"]
    assert isinstance(argv, list) and all(isinstance(arg, str) for arg in argv)
    assert argv[0] == "git"
    assert argv[argv.index(expected[0]) :] == expected
    if "-C" in argv:
        assert argv[argv.index("-C") + 1] == str(pair.local)
    assert snapshot(pair.local) == before
    invalid = config_factory({"nested": nested})
    code, captured = invoke_cli(
        ["--config", str(invalid), "--json", *args, "--dry-run"]
    )
    assert code == 1
    assert_json(captured, command, total=1, failed=1)
    assert snapshot(pair.local) == before


@pytest.mark.mutation
def test_fetch_updates_and_prunes_tracking_refs_without_moving_head(
    remote_pair,
    git,
    advance_remote,
    config_factory,
    invoke_cli,
    assert_json,
):
    pair = remote_pair
    git(pair.seed, "branch", "obsolete")
    git(pair.seed, "push", "origin", "obsolete")
    git(pair.local, "fetch")
    assert (
        git(
            pair.local, "rev-parse", "--verify", "refs/remotes/origin/obsolete"
        ).returncode
        == 0
    )
    git(pair.seed, "push", "origin", "--delete", "obsolete")
    old_head = git(pair.local, "rev-parse", "HEAD").stdout.strip()
    new_head = advance_remote(pair)
    config = config_factory({"one": pair.local})
    code, captured = invoke_cli(["--config", str(config), "fetch", "--json"])
    assert code == 0
    assert_json(captured, "fetch", total=1, ok=1)
    assert git(pair.local, "rev-parse", "origin/main").stdout.strip() == new_head
    assert git(pair.local, "rev-parse", "HEAD").stdout.strip() == old_head
    assert (
        git(
            pair.local,
            "rev-parse",
            "--verify",
            "refs/remotes/origin/obsolete",
            check=False,
        ).returncode
        != 0
    )
    assert not (pair.local / "remote.txt").exists()


@pytest.mark.mutation
def test_pull_rebases_local_commits_and_preserves_dirty_tracked_edits(
    remote_pair,
    git,
    advance_remote,
    config_factory,
    invoke_cli,
    assert_json,
):
    pair = remote_pair
    (pair.local / "local-commit.txt").write_text("local commit\n", encoding="utf-8")
    git(pair.local, "add", "local-commit.txt")
    git(pair.local, "commit", "-m", "Local commit")
    old_local_commit = git(pair.local, "rev-parse", "HEAD").stdout.strip()
    (pair.local / "README.txt").write_text("preserve this edit\n", encoding="utf-8")
    remote_commit = advance_remote(pair)
    config = config_factory({"one": pair.local})
    code, captured = invoke_cli(["--config", str(config), "pull", "--json"])
    assert code == 0
    assert_json(captured, "pull", total=1, ok=1)
    assert (pair.local / "remote.txt").read_text(encoding="utf-8") == "remote update\n"
    assert (pair.local / "local-commit.txt").read_text(
        encoding="utf-8"
    ) == "local commit\n"
    assert (pair.local / "README.txt").read_text(
        encoding="utf-8"
    ) == "preserve this edit\n"
    assert git(pair.local, "rev-parse", "HEAD^").stdout.strip() == remote_commit
    assert git(pair.local, "rev-parse", "HEAD").stdout.strip() != old_local_commit
    assert git(pair.local, "status", "--porcelain").stdout == " M README.txt\n"
    assert not git(pair.local, "ls-files", "--unmerged").stdout
    assert not git(pair.local, "stash", "list").stdout


@pytest.mark.mutation
@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote-tracking"])
def test_checkout_switches_local_or_unambiguous_tracking_branch(
    remote_pair,
    git,
    config_factory,
    invoke_cli,
    assert_json,
    remote,
):
    pair = remote_pair
    if remote:
        git(pair.seed, "switch", "-c", "feature")
        (pair.seed / "feature.txt").write_text("feature\n", encoding="utf-8")
        git(pair.seed, "add", "feature.txt")
        git(pair.seed, "commit", "-m", "Feature")
        git(pair.seed, "push", "origin", "feature")
        git(pair.local, "fetch")
        assert (
            git(
                pair.local, "show-ref", "--verify", "refs/heads/feature", check=False
            ).returncode
            != 0
        )
    else:
        git(pair.local, "switch", "-c", "feature")
        (pair.local / "feature.txt").write_text("feature\n", encoding="utf-8")
        git(pair.local, "add", "feature.txt")
        git(pair.local, "commit", "-m", "Feature")
        git(pair.local, "switch", "main")
    config = config_factory({"one": pair.local})
    code, captured = invoke_cli(
        ["--config", str(config), "checkout", "feature", "--json"]
    )
    assert code == 0
    assert_json(captured, "checkout", total=1, ok=1)
    assert git(pair.local, "branch", "--show-current").stdout.strip() == "feature"
    assert (pair.local / "feature.txt").read_text(encoding="utf-8") == "feature\n"
    if remote:
        assert (
            git(pair.local, "rev-parse", "--abbrev-ref", "@{upstream}").stdout.strip()
            == "origin/feature"
        )


@pytest.mark.mutation
@pytest.mark.parametrize("dirty", ["tracked", "untracked"])
def test_checkout_refuses_dirty_worktrees_even_when_git_could_preserve_changes(
    repo_factory, git, config_factory, snapshot, invoke_cli, assert_json, dirty
):
    repo = repo_factory()
    git(repo, "branch", "feature")
    filename = "README.txt" if dirty == "tracked" else "untracked.txt"
    (repo / filename).write_text("must not be discarded\n", encoding="utf-8")
    before = snapshot(repo)
    config = config_factory({"one": repo})
    code, captured = invoke_cli(
        ["--config", str(config), "checkout", "feature", "--json"]
    )
    assert code == 1
    assert_json(captured, "checkout", total=1, failed=1)
    assert snapshot(repo) == before


@pytest.mark.mutation
def test_missing_remote_branch_is_not_automatically_fetched(
    remote_pair, git, config_factory, snapshot, invoke_cli, assert_json
):
    pair = remote_pair
    git(pair.seed, "branch", "not-fetched")
    git(pair.seed, "push", "origin", "not-fetched")
    before = snapshot(pair.local)
    config = config_factory({"one": pair.local})
    code, captured = invoke_cli(
        ["--config", str(config), "checkout", "not-fetched", "--json"]
    )
    assert code == 1
    assert_json(captured, "checkout", total=1, failed=1)
    assert snapshot(pair.local) == before


@pytest.mark.mutation
def test_invalid_branch_is_cli_error_before_processing_repos(
    repo_factory, config_factory, snapshot, invoke_cli
):
    repo = repo_factory()
    before = snapshot(repo)
    config = config_factory({"one": repo})
    code, captured = invoke_cli(
        ["--config", str(config), "checkout", "bad..branch", "--json"]
    )
    assert code == 2
    assert not captured.out
    assert captured.err.strip()
    assert snapshot(repo) == before


@pytest.mark.mutation
@pytest.mark.parametrize("bad", ["missing", "nonrepo", "conflict"])
def test_pull_continues_after_repo_failure(
    tmp_path,
    remote_pair,
    configure_git,
    git,
    advance_remote,
    config_factory,
    invoke_cli,
    assert_json,
    bad,
):
    pair = remote_pair
    if bad == "conflict":
        good = tmp_path / "good"
        git(tmp_path, "clone", str(pair.bare), str(good))
        configure_git(good)
        (pair.local / "README.txt").write_text(
            "local committed change\n", encoding="utf-8"
        )
        git(pair.local, "add", "README.txt")
        git(pair.local, "commit", "-m", "Conflicting local change")
        advance_remote(pair, "README.txt", "remote committed change\n")
        failed_path = pair.local
        expected_head = git(pair.seed, "rev-parse", "HEAD").stdout.strip()
    else:
        failed_path = tmp_path / "bad"
        if bad == "nonrepo":
            failed_path.mkdir()
        good = pair.local
        expected_head = advance_remote(pair)
    config = config_factory({"bad": failed_path, "good": good})
    code, captured = invoke_cli(["--config", str(config), "pull", "--json"])
    assert code == 1
    results = assert_json(captured, "pull", total=2, ok=1, failed=1)
    assert [(result["name"], result["status"]) for result in results] == [
        ("bad", "failed"),
        ("good", "ok"),
    ]
    assert git(good, "rev-parse", "HEAD").stdout.strip() == expected_head
    if bad == "conflict":
        assert git(failed_path, "ls-files", "--unmerged").stdout.strip()


@pytest.mark.mutation
def test_zero_exit_pull_with_autostash_conflict_is_reported_as_failure(
    remote_pair,
    git,
    advance_remote,
    config_factory,
    monkeypatch,
    invoke_cli,
    assert_json,
):
    pair = remote_pair
    advance_remote(pair, "README.txt", "remote content\n")
    (pair.local / "README.txt").write_text("local content\n", encoding="utf-8")
    config = config_factory({"one": pair.local})
    original_run = subprocess.run
    pull_codes = []
    operations = []

    def record_real_pull(argv, **kwargs):
        completed = original_run(argv, **kwargs)
        operations.append(argv)
        if "pull" in argv:
            pull_codes.append(completed.returncode)
        return completed

    monkeypatch.setattr(subprocess, "run", record_real_pull)
    code, captured = invoke_cli(["--config", str(config), "pull", "--json"])
    monkeypatch.setattr(subprocess, "run", original_run)
    assert pull_codes == [0], "fixture must exercise Git's zero-exit autostash conflict"
    assert git(pair.local, "ls-files", "--unmerged").stdout.strip()
    assert git(pair.local, "stash", "list").stdout.strip()
    assert not any(
        recovery in argv
        for argv in operations
        for recovery in ("reset", "stash", "clean", "checkout", "switch")
    )
    assert code == 1
    result = assert_json(captured, "pull", total=1, failed=1)[0]
    assert (
        "conflict" in result["message"].lower()
        or "unmerged" in result["message"].lower()
    )
    assert "git status" in result["message"].lower()
    assert "git stash list" in result["message"].lower()


@pytest.mark.mutation
@pytest.mark.parametrize("dirty", ["tracked", "staged", "untracked"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["execute", "dry-run"])
def test_dirty_checkout_preflight_is_actionable_and_never_switches(
    repo_factory,
    git,
    config_factory,
    snapshot,
    monkeypatch,
    invoke_cli,
    assert_json,
    dirty,
    dry_run,
):
    repo = repo_factory()
    git(repo, "branch", "feature")
    filename = "untracked.txt" if dirty == "untracked" else "README.txt"
    (repo / filename).write_text("preserve this change\n", encoding="utf-8")
    if dirty == "staged":
        git(repo, "add", filename)
    before = snapshot(repo)
    config = config_factory({"one": repo})
    original_run = subprocess.run

    def forbid_mutations(argv, **kwargs):
        assert not {"switch", "checkout", "stash", "reset", "clean"}.intersection(argv)
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", forbid_mutations)
    args = ["checkout", "feature"]
    if dry_run:
        args.append("--dry-run")
    code, captured = invoke_cli(["--config", str(config), "--json", *args])
    monkeypatch.setattr(subprocess, "run", original_run)

    assert code == 1
    result = assert_json(captured, "checkout", total=1, failed=1)[0]
    assert "dirty" in result["message"].lower()
    assert "preserve" in result["message"].lower()
    assert snapshot(repo) == before


@pytest.mark.mutation
@pytest.mark.parametrize("dry_run", [False, True], ids=["execute", "dry-run"])
def test_checkout_refuses_ambiguous_tracking_refs_despite_default_remote(
    remote_pair,
    git,
    config_factory,
    snapshot,
    invoke_cli,
    assert_json,
    dry_run,
):
    pair = remote_pair
    git(pair.seed, "branch", "feature")
    git(pair.seed, "push", "origin", "feature")
    git(pair.local, "remote", "add", "backup", str(pair.bare))
    git(pair.local, "fetch", "origin")
    git(pair.local, "fetch", "backup")
    assert (
        git(pair.local, "config", "checkout.defaultRemote").stdout.strip() == "origin"
    )
    before = snapshot(pair.local)
    config = config_factory({"one": pair.local})
    args = ["checkout", "feature"]
    if dry_run:
        args.append("--dry-run")

    code, captured = invoke_cli(["--config", str(config), "--json", *args])

    assert code == 1
    result = assert_json(captured, "checkout", total=1, failed=1)[0]
    assert "ambiguous" in result["message"].lower()
    assert snapshot(pair.local) == before


@pytest.mark.mutation
def test_checkout_local_branch_takes_precedence_over_ambiguous_tracking_refs(
    remote_pair, git, config_factory, invoke_cli, assert_json
):
    pair = remote_pair
    git(pair.seed, "branch", "feature")
    git(pair.seed, "push", "origin", "feature")
    git(pair.local, "remote", "add", "backup", str(pair.bare))
    git(pair.local, "fetch", "origin")
    git(pair.local, "fetch", "backup")
    git(pair.local, "branch", "feature")
    config = config_factory({"one": pair.local})

    code, captured = invoke_cli(
        ["--config", str(config), "--json", "checkout", "feature"]
    )

    assert code == 0
    assert_json(captured, "checkout", total=1, ok=1)
    assert git(pair.local, "branch", "--show-current").stdout.strip() == "feature"


@pytest.mark.mutation
def test_checkout_dry_run_missing_tracking_branch_does_not_fetch(
    remote_pair, git, config_factory, snapshot, invoke_cli, assert_json
):
    pair = remote_pair
    git(pair.seed, "branch", "not-fetched")
    git(pair.seed, "push", "origin", "not-fetched")
    before = snapshot(pair.local)
    config = config_factory({"one": pair.local})

    code, captured = invoke_cli(
        [
            "--config",
            str(config),
            "--json",
            "checkout",
            "not-fetched",
            "--dry-run",
        ]
    )

    assert code == 1
    result = assert_json(captured, "checkout", total=1, failed=1)[0]
    assert "branch" in result["message"].lower()
    assert "fetch" in result["message"].lower()
    assert snapshot(pair.local) == before


@pytest.mark.mutation
@pytest.mark.parametrize("command", ["pull", "fetch", "checkout"])
@pytest.mark.parametrize("kind", ["missing", "nonrepo", "nested", "bare"])
def test_mutation_dry_run_validates_worktree_root_without_mutations(
    tmp_path,
    repo_factory,
    git,
    config_factory,
    monkeypatch,
    invoke_cli,
    assert_json,
    command,
    kind,
):
    path = tmp_path / "invalid"
    if kind == "nonrepo":
        path.mkdir()
    elif kind == "nested":
        repo = repo_factory()
        git(repo, "branch", "feature")
        path = repo / "nested"
        path.mkdir()
    elif kind == "bare":
        path.mkdir()
        git(path, "init", "--bare", "--initial-branch=main")
    config = config_factory({"invalid": path})
    original_run = subprocess.run
    operations = []

    def read_only_run(argv, **kwargs):
        assert not {
            "pull",
            "fetch",
            "switch",
            "checkout",
            "reset",
            "stash",
        }.intersection(argv)
        operations.append(argv)
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", read_only_run)
    args = [command, "feature"] if command == "checkout" else [command]
    code, captured = invoke_cli(["--config", str(config), "--json", *args, "--dry-run"])

    assert code == 1
    assert_json(captured, command, total=1, failed=1)
    assert any("rev-parse" in argv for argv in operations)


@pytest.mark.mutation
def test_checkout_supports_linked_worktrees(
    tmp_path, repo_factory, git, config_factory, invoke_cli, assert_json
):
    repo = repo_factory()
    linked = tmp_path / "linked"
    git(repo, "branch", "feature")
    git(repo, "worktree", "add", "-b", "linked-branch", str(linked))
    config = config_factory({"linked": linked})

    code, captured = invoke_cli(
        ["--config", str(config), "--json", "checkout", "feature"]
    )

    assert code == 0
    assert_json(captured, "checkout", total=1, ok=1)
    assert git(linked, "branch", "--show-current").stdout.strip() == "feature"
    assert git(repo, "branch", "--show-current").stdout.strip() == "main"


@pytest.mark.mutation
def test_checkout_uses_literal_branch_name_not_shell_syntax(
    repo_factory, git, config_factory, invoke_cli, assert_json
):
    repo = repo_factory()
    branch = "feature;not-a-command"
    git(repo, "branch", branch)
    config = config_factory({"one": repo})

    code, captured = invoke_cli(["--config", str(config), "--json", "checkout", branch])

    assert code == 0
    assert_json(captured, "checkout", total=1, ok=1)
    assert git(repo, "branch", "--show-current").stdout.strip() == branch


@pytest.mark.mutation
@pytest.mark.parametrize("command", ["pull", "fetch", "checkout"])
def test_mutation_timeout_preserves_diagnostics_and_continues(
    tmp_path,
    remote_pair,
    configure_git,
    git,
    config_factory,
    monkeypatch,
    invoke_cli,
    assert_json,
    command,
):
    pair = remote_pair
    good = tmp_path / "good"
    git(tmp_path, "clone", str(pair.bare), str(good))
    configure_git(good)
    git(pair.local, "branch", "feature")
    git(good, "branch", "feature")
    config = config_factory({"bad": pair.local, "good": good})
    original_run = subprocess.run
    operation = "switch" if command == "checkout" else command

    def timeout_bad_mutation(argv, **kwargs):
        if str(pair.local) in argv and operation in argv:
            raise subprocess.TimeoutExpired(
                cmd=argv,
                timeout=kwargs["timeout"],
                output=b"partial output",
                stderr=b"timeout diagnostic",
            )
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", timeout_bad_mutation)
    args = [command, "feature"] if command == "checkout" else [command]
    code, captured = invoke_cli(["--config", str(config), "--json", *args])

    assert code == 1
    results = assert_json(captured, command, total=2, ok=1, failed=1)
    assert [(result["name"], result["status"]) for result in results] == [
        ("bad", "failed"),
        ("good", "ok"),
    ]
    assert "timeout diagnostic" in results[0]["message"]
    assert "partial output" in results[0]["message"]


@pytest.mark.mutation
@pytest.mark.parametrize("shadow", ["local-branch", "tag"])
def test_checkout_tracking_ref_cannot_be_shadowed_by_local_branch_or_tag(
    remote_pair, git, config_factory, invoke_cli, assert_json, shadow
):
    pair = remote_pair
    git(pair.seed, "branch", "feature")
    git(pair.seed, "push", "origin", "feature")
    git(pair.local, "fetch")
    remote_head = git(
        pair.local, "rev-parse", "refs/remotes/origin/feature"
    ).stdout.strip()
    git(pair.local, "commit", "--allow-empty", "-m", "Different local commit")
    local_head = git(pair.local, "rev-parse", "HEAD").stdout.strip()
    assert local_head != remote_head
    git(pair.local, "branch" if shadow == "local-branch" else "tag", "origin/feature")
    config = config_factory({"one": pair.local})

    code, captured = invoke_cli(
        ["--config", str(config), "--json", "checkout", "feature"]
    )

    assert code == 0
    assert_json(captured, "checkout", total=1, ok=1)
    assert git(pair.local, "rev-parse", "HEAD").stdout.strip() == remote_head
    assert (
        git(
            pair.local, "rev-parse", "--symbolic-full-name", "@{upstream}"
        ).stdout.strip()
        == "refs/remotes/origin/feature"
    )
