import os
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


def cli_module():
    from my_repos_ctl import cli

    return cli


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("repos: [", id="malformed-yaml"),
        pytest.param(
            "repos: {one: ./one}\nrepos: {two: ./two}", id="duplicate-root-key"
        ),
        pytest.param("repos:\n  one: ./one\n  one: ./two", id="duplicate-repo-key"),
        pytest.param("- repos", id="root-list"),
        pytest.param("null", id="root-null"),
        pytest.param("{}", id="missing-repos"),
        pytest.param("repos: {}", id="empty-repos"),
        pytest.param("repos: null", id="null-repos"),
        pytest.param("repos: [./one]", id="list-repos"),
        pytest.param("repos: {1: ./one}", id="nonstring-name"),
        pytest.param('repos: {"": ./one}', id="empty-name"),
        pytest.param('repos: {"   ": ./one}', id="blank-name"),
        pytest.param("repos: {one: 42}", id="nonstring-path"),
        pytest.param("repos: {one: {path: ./one}}", id="nested-path-mapping"),
        pytest.param('repos: {one: ""}', id="empty-path"),
        pytest.param('repos: {one: "   "}', id="blank-path"),
        pytest.param("repos: {one: ./one}\nextra: true", id="unknown-root-field"),
        pytest.param(
            "repos: {one: ./same, two: ./sub/../same}", id="duplicate-resolved-path"
        ),
    ],
)
def test_load_config_rejects_invalid_documents(tmp_path, document):
    cli = cli_module()
    path = tmp_path / "invalid.yml"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(cli.ConfigError):
        cli.load_config(path)


def test_load_config_reports_missing_file(tmp_path):
    cli = cli_module()
    with pytest.raises(cli.ConfigError):
        cli.load_config(tmp_path / "missing.yml")


def test_load_config_resolves_paths_from_config_not_cwd(tmp_path, monkeypatch):
    cli = cli_module()
    directory = tmp_path / "configuration"
    directory.mkdir()
    path = directory / "repos.yml"
    path.write_text(
        "repos:\n  relative: ../relative\n  home: ~/projects/home\n",
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    repos = cli.load_config(path)
    assert [repo.name for repo in repos] == ["relative", "home"]
    assert [repo.path for repo in repos] == [
        (tmp_path / "relative").resolve(),
        (Path(os.environ["HOME"]) / "projects/home").resolve(),
    ]
    assert all(isinstance(repo, cli.Repo) for repo in repos)
    with pytest.raises(FrozenInstanceError):
        repos[0].name = "changed"


def test_selection_defaults_deduplicates_and_rejects_unknown_names(tmp_path):
    cli = cli_module()
    repos = [
        cli.Repo(name="second", path=tmp_path / "second"),
        cli.Repo(name="first", path=tmp_path / "first"),
    ]
    assert cli.select_repos(repos, []) == repos
    selected = cli.select_repos(repos, ["first", "second", "first"])
    assert {repo.name for repo in selected} == {"first", "second"}
    assert len(selected) == 2
    with pytest.raises(cli.ConfigError):
        cli.select_repos(repos, ["first", "FIRST"])


def test_default_config_list_is_ordered_and_never_invokes_git(
    tmp_path, config_factory, monkeypatch, invoke_cli, assert_json
):
    cli_module()
    first = tmp_path / "not-a-repository"
    second = tmp_path / "does-not-exist"
    config_factory(
        {"zeta": first, "alpha": second},
        path=Path(os.environ["HOME"]) / ".my-repo-ctl.yml",
    )

    def forbid_subprocess(*args, **kwargs):
        pytest.fail("list must not invoke Git or any other subprocess")

    monkeypatch.setattr(subprocess, "run", forbid_subprocess)
    code, human = invoke_cli(["list"])
    assert code == 0
    assert all(
        value in human.out for value in ["zeta", "alpha", str(first), str(second)]
    )
    code, captured = invoke_cli(["list", "--json"])
    assert code == 0
    results = assert_json(captured, "list", total=2, ok=2)
    assert [(item["name"], item["path"]) for item in results] == [
        ("zeta", str(first)),
        ("alpha", str(second)),
    ]
    assert not captured.err


@pytest.mark.parametrize("options_first", [True, False], ids=["before", "after"])
def test_common_options_work_on_either_side(
    tmp_path, config_factory, invoke_cli, assert_json, options_first
):
    config = config_factory({"one": tmp_path / "one", "two": tmp_path / "two"})
    options = ["--config", str(config), "--repo", "two", "--json", "--timeout", "2"]
    argv = options + ["list"] if options_first else ["list"] + options
    code, captured = invoke_cli(argv)
    assert code == 0
    results = assert_json(captured, "list", total=1, ok=1)
    assert results[0]["name"] == "two"


def test_selectors_accumulate_across_command_and_json_is_not_lost(
    tmp_path, config_factory, invoke_cli, assert_json
):
    config = config_factory(
        {"one": tmp_path / "one", "two": tmp_path / "two", "three": tmp_path / "three"}
    )
    code, captured = invoke_cli(
        [
            "--config",
            str(config),
            "--repo",
            "two",
            "--json",
            "list",
            "--repo",
            "one",
            "--repo",
            "two",
        ]
    )
    assert code == 0
    results = assert_json(captured, "list", total=2, ok=2)
    assert {item["name"] for item in results} == {"one", "two"}


def test_later_config_and_timeout_override_without_losing_json(
    tmp_path, config_factory, repo_factory, monkeypatch, invoke_cli, assert_json
):
    cli_module()
    wrong = config_factory({"wrong": tmp_path / "missing"})
    repo = repo_factory()
    right = config_factory({"right": repo})
    original_run = subprocess.run
    observed_timeouts = []

    def observe_run(*args, **kwargs):
        observed_timeouts.append(kwargs.get("timeout"))
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", observe_run)
    code, captured = invoke_cli(
        [
            "--config",
            str(wrong),
            "--timeout",
            "9",
            "--json",
            "status",
            "--config",
            str(right),
            "--timeout",
            "1.5",
        ]
    )
    assert code == 0
    results = assert_json(captured, "status", total=1, ok=1)
    assert results[0]["name"] == "right"
    assert observed_timeouts and all(value == 1.5 for value in observed_timeouts)


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--timeout", "0", "list"], id="zero-timeout"),
        pytest.param(["list", "--timeout", "-1"], id="negative-timeout"),
        pytest.param(["not-a-command"], id="unknown-command"),
        pytest.param(["checkout"], id="missing-branch"),
    ],
)
def test_invalid_arguments_exit_two_with_stderr(argv, capsys):
    main = cli_module().main
    try:
        code = main(argv)
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    assert code == 2
    assert not captured.out
    assert captured.err.strip()


@pytest.mark.parametrize("missing", [False, True], ids=["malformed", "missing"])
def test_config_errors_return_two_not_system_exit(tmp_path, invoke_cli, missing):
    path = tmp_path / "bad.yml"
    if not missing:
        path.write_text("repos: [", encoding="utf-8")
    code, captured = invoke_cli(["--config", str(path), "list", "--json"])
    assert code == 2
    assert not captured.out
    assert captured.err.strip()


@pytest.mark.mutation
def test_unknown_selector_rejected_before_any_mutation(
    repo_factory, git, config_factory, snapshot, invoke_cli
):
    repo = repo_factory()
    git(repo, "branch", "feature")
    config = config_factory({"known": repo})
    before = snapshot(repo)
    code, captured = invoke_cli(
        [
            "--config",
            str(config),
            "--repo",
            "known",
            "checkout",
            "feature",
            "--repo",
            "unknown",
            "--json",
        ]
    )
    assert code == 2
    assert not captured.out
    assert captured.err.strip()
    assert snapshot(repo) == before


@pytest.mark.parametrize("failure", ["timeout", "missing-git", "decode"])
def test_subprocess_failures_have_specific_errors_and_json_results(
    repo_factory, config_factory, monkeypatch, invoke_cli, assert_json, failure
):
    cli = cli_module()
    repo = repo_factory()
    config = config_factory({"one": repo})
    managed = cli.Repo(name="one", path=repo)

    def fail_run(argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(
                cmd=argv, timeout=kwargs["timeout"], stderr="timeout diagnostic"
            )
        if failure == "decode":
            raise UnicodeDecodeError("utf-8", b"raw-\xff", 4, 5, "invalid start byte")
        raise FileNotFoundError(2, "Git executable unavailable", "git")

    monkeypatch.setattr(subprocess, "run", fail_run)
    with pytest.raises(cli.RepoError) as exc:
        cli._git(managed, ["status", "--porcelain"], 0.25)
    detail = str(exc.value).lower()
    if failure == "timeout":
        assert "timeout" in detail or "timed out" in detail
    elif failure == "decode":
        assert isinstance(exc.value.__cause__, UnicodeDecodeError)
        assert "decode" in detail and "utf-8" in detail
        assert "rename" in detail or "encoding" in detail
    else:
        assert "git" in detail
    code, captured = invoke_cli(["--config", str(config), "status", "--json"])
    assert code == 1
    assert_json(captured, "status", total=1, failed=1)


def test_git_seam_does_not_convert_unexpected_errors(repo_factory, monkeypatch):
    cli = cli_module()
    repo = cli.Repo(name="one", path=repo_factory())

    def fail_run(*args, **kwargs):
        raise RuntimeError("unexpected subprocess failure")

    monkeypatch.setattr(subprocess, "run", fail_run)
    with pytest.raises(RuntimeError, match="unexpected subprocess failure"):
        cli._git(repo, ["status", "--porcelain"], 2)


def test_git_seam_preserves_diagnostics_and_successful_stdout(
    repo_factory, git, config_factory, monkeypatch, invoke_cli, assert_json
):
    cli = cli_module()
    repo = repo_factory()
    config = config_factory({"one": repo})
    original_run = subprocess.run
    invocations = []

    def observe_run(argv, **kwargs):
        invocations.append((argv, kwargs))
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", observe_run)
    code, captured = invoke_cli(["--config", str(config), "status", "--json"])
    assert code == 0
    assert_json(captured, "status", total=1, ok=1)
    assert invocations
    for argv, options in invocations:
        assert isinstance(argv, list)
        assert options["timeout"] == 300
        assert options.get("shell", False) is False
        assert options.get("stdin") in (
            subprocess.DEVNULL,
            subprocess.PIPE,
        ) or options.get("input") in ("", b"")
    monkeypatch.setattr(subprocess, "run", original_run)
    managed = cli.Repo(name="one", path=repo)
    assert (
        cli._git(managed, ["rev-parse", "HEAD"], 2).strip()
        == git(repo, "rev-parse", "HEAD").stdout.strip()
    )
    failed = git(repo, "rev-parse", "--verify", "refs/heads/absent", check=False)
    assert failed.returncode != 0 and failed.stderr.strip()
    with pytest.raises(cli.RepoError) as exc:
        cli._git(managed, ["rev-parse", "--verify", "refs/heads/absent"], 2)
    assert failed.stderr.strip() in str(exc.value)


@pytest.mark.mutation
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("pull", ["pull", "--rebase", "--autostash"]),
        ("fetch", ["fetch", "--prune"]),
        ("checkout", ["switch", "feature"]),
    ],
)
def test_mutations_execute_safe_argv_with_common_options_and_json_only(
    remote_pair,
    git,
    config_factory,
    monkeypatch,
    invoke_cli,
    assert_json,
    command,
    expected,
):
    pair = remote_pair
    git(pair.local, "branch", "feature")
    config = config_factory({"one": pair.local})
    original_run = subprocess.run
    invocations = []

    def observe_run(argv, **kwargs):
        invocations.append((argv, kwargs))
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", observe_run)
    args = [command, "feature"] if command == "checkout" else [command]
    code, captured = invoke_cli(
        [
            "--config",
            str(config),
            "--json",
            "--timeout",
            "9",
            "--repo",
            "one",
            *args,
            "--timeout",
            "1.5",
            "--repo",
            "one",
        ]
    )

    assert code == 0
    assert_json(captured, command, total=1, ok=1)
    assert not captured.err
    mutations = [
        argv
        for argv, _ in invocations
        if any(operation in argv for operation in ("pull", "fetch", "switch"))
    ]
    assert mutations == [["git", "--no-pager", "-C", str(pair.local), *expected]]
    assert all(options["timeout"] == 1.5 for _, options in invocations)
    assert all(options["shell"] is False for _, options in invocations)
    assert all(options["stdin"] == subprocess.DEVNULL for _, options in invocations)


@pytest.mark.mutation
@pytest.mark.parametrize(
    "branch",
    [
        "--force",
        "-c",
        "-",
        "@{-1}",
        "@{-2}",
        "bad..branch",
        "HEAD",
        "",
        "bad\x00branch",
    ],
)
def test_checkout_rejects_unsafe_branch_arguments_before_any_selected_repo(
    repo_factory,
    git,
    config_factory,
    snapshot,
    monkeypatch,
    invoke_cli,
    branch,
):
    repo = repo_factory()
    git(repo, "switch", "-c", "previous")
    git(repo, "switch", "main")
    config = config_factory({"one": repo})
    before = snapshot(repo)
    monkeypatch.chdir(repo)
    original_run = subprocess.run
    invocations = []

    def observe_run(argv, **kwargs):
        invocations.append(argv)
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", observe_run)
    code, captured = invoke_cli(
        ["--config", str(config), "--json", "checkout", "--", branch]
    )
    monkeypatch.setattr(subprocess, "run", original_run)

    assert code == 2
    assert not captured.out
    assert "branch" in captured.err.lower()
    assert all(
        argv == ["git", "--no-pager", "check-ref-format", "--branch", branch]
        for argv in invocations
    )
    assert snapshot(repo) == before


@pytest.mark.mutation
@pytest.mark.parametrize("command", ["pull", "fetch", "checkout"])
def test_mutation_config_error_prevents_every_git_call(
    tmp_path, config_factory, monkeypatch, invoke_cli, command
):
    config = config_factory({"one": tmp_path / "one"})
    config.write_text("repos: [", encoding="utf-8")

    def forbid_run(*args, **kwargs):
        pytest.fail("invalid configuration must prevent all Git calls")

    monkeypatch.setattr(subprocess, "run", forbid_run)
    args = [command, "feature"] if command == "checkout" else [command]
    code, captured = invoke_cli(["--config", str(config), "--json", *args])
    assert code == 2
    assert not captured.out
    assert captured.err.strip()


@pytest.mark.mutation
@pytest.mark.parametrize("command", ["pull", "fetch", "checkout"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["execute", "dry-run"])
def test_mutations_include_human_summary(
    remote_pair, git, config_factory, invoke_cli, command, dry_run
):
    pair = remote_pair
    git(pair.local, "branch", "feature")
    config = config_factory({"one": pair.local})
    args = [command, "feature"] if command == "checkout" else [command]
    if dry_run:
        args.append("--dry-run")
    code, captured = invoke_cli(["--config", str(config), *args])

    assert code == 0
    assert "one" in captured.out and str(pair.local) in captured.out
    assert "summary" in captured.out.lower()
    assert "1 total" in captured.out
    assert f"{0 if dry_run else 1} ok" in captured.out
    assert "0 failed" in captured.out
    assert f"{1 if dry_run else 0} planned" in captured.out
    assert not captured.err
