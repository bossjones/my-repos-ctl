import io
import subprocess

import pytest


@pytest.fixture(autouse=True)
def plain_wide_console(monkeypatch):
    monkeypatch.setenv("COLUMNS", "240")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def invoke(invoke_cli, argv):
    try:
        return invoke_cli(argv)
    except SystemExit as exc:
        pytest.fail(
            f"pull presentation arguments must be accepted, got exit {exc.code}"
        )


def render_table(results, width=120):
    from my_repos_ctl import cli

    builder = getattr(cli, "_build_pull_summary_table", None)
    assert callable(builder), "Rich pull summary builder is not implemented"
    from rich.console import Console

    output = io.StringIO()
    console = Console(file=output, width=width, color_system=None)
    console.print(builder(results))
    return output.getvalue()


@pytest.mark.mutation
def test_default_pull_shows_git_stdout_and_rich_table(
    remote_pair, config_factory, invoke_cli
):
    config = config_factory({"demo/docs": remote_pair.local})
    code, captured = invoke(invoke_cli, ["--config", str(config), "pull"])
    assert code == 0
    assert "Already up to date." in captured.out
    assert "Pull summary" in captured.out
    assert all(header in captured.out for header in ("Status", "Repo", "Detail"))
    assert "1 pulled" in captured.out and "0 failed" in captured.out
    assert "\x1b[" not in captured.out
    assert not captured.err


@pytest.mark.mutation
@pytest.mark.parametrize("quiet", ["--quiet", "-q"])
def test_quiet_pull_has_only_table_and_counts(
    remote_pair, config_factory, invoke_cli, quiet
):
    config = config_factory({"demo/docs": remote_pair.local})
    code, captured = invoke(invoke_cli, ["--config", str(config), "pull", quiet])
    assert code == 0
    assert captured.out.count("Already up to date.") == 1
    assert captured.out.count("demo/docs") == 1
    assert "Pull summary" in captured.out and "1 pulled" in captured.out
    assert not captured.err


@pytest.mark.mutation
def test_human_summary_displays_failures_before_successes(
    remote_pair, tmp_path, config_factory, invoke_cli
):
    config = config_factory(
        {"good-demo": remote_pair.local, "bad-demo": tmp_path / "absent"}
    )
    code, captured = invoke(invoke_cli, ["--config", str(config), "pull", "--quiet"])
    assert code == 1
    assert captured.out.index("bad-demo") < captured.out.index("good-demo")
    assert "1 pulled" in captured.out and "1 failed" in captured.out
    assert not captured.err


@pytest.mark.mutation
def test_quiet_json_retains_selection_order_and_has_no_presentation(
    remote_pair, tmp_path, config_factory, monkeypatch, invoke_cli, assert_json
):
    from my_repos_ctl import cli

    def forbid_console(*args, **kwargs):
        pytest.fail("JSON must not construct a Rich console")

    monkeypatch.setattr(cli, "Console", forbid_console, raising=False)
    config = config_factory(
        {"good-demo": remote_pair.local, "bad-demo": tmp_path / "absent"}
    )
    code, captured = invoke(
        invoke_cli, ["--json", "--config", str(config), "pull", "--quiet"]
    )
    assert code == 1
    results = assert_json(captured, "pull", total=2, ok=1, failed=1)
    assert [result["name"] for result in results] == ["good-demo", "bad-demo"]
    assert results[0]["message"] == "Already up to date."
    assert "Pull summary" not in captured.out and "\x1b[" not in captured.out
    assert not captured.err


def test_quiet_dry_run_reports_planned_without_mutating(
    repo_factory, config_factory, snapshot, monkeypatch, invoke_cli
):
    repo = repo_factory()
    config = config_factory({"demo/docs": repo})
    before = snapshot(repo)
    original_run = subprocess.run

    def forbid_mutation(argv, **kwargs):
        assert not any(operation in argv for operation in ("pull", "fetch", "switch"))
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", forbid_mutation)
    code, captured = invoke(
        invoke_cli, ["--config", str(config), "pull", "--quiet", "--dry-run"]
    )
    monkeypatch.setattr(subprocess, "run", original_run)
    assert code == 0
    assert "Pull summary" in captured.out
    assert "0 pulled" in captured.out and "1 planned" in captured.out
    assert snapshot(repo) == before


@pytest.mark.mutation
def test_live_result_is_printed_before_next_repo_pull(
    remote_pair,
    tmp_path,
    git,
    configure_git,
    config_factory,
    monkeypatch,
    capsys,
    invoke_cli,
):
    second = tmp_path / "second"
    git(tmp_path, "clone", str(remote_pair.bare), str(second))
    configure_git(second)
    config = config_factory({"first-demo": remote_pair.local, "second-demo": second})
    original_run = subprocess.run
    checkpoints = []

    def check_progress(argv, **kwargs):
        if "pull" in argv and str(second) in argv:
            progress = capsys.readouterr().out
            assert "first-demo" in progress
            assert "Already up to date." in progress
            assert "Pull summary" not in progress
            checkpoints.append(progress)
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", check_progress)
    code, captured = invoke(invoke_cli, ["--config", str(config), "pull"])
    assert code == 0 and len(checkpoints) == 1
    assert "Pull summary" in captured.out and "2 pulled" in captured.out


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Already up to date.\n", "Already up to date."),
        ("  \n first line\n final line \n", "final line"),
        ("warning: first\nfatal: useful problem\nlast line", "fatal: useful problem"),
        ("note\nerror: useful problem\nfatal: later", "error: useful problem"),
        ("", ""),
        ("x" * 160, "x" * 160),
        ("x" * 161, "x" * 159 + "\u2026"),
    ],
)
def test_pull_excerpt_has_actionable_bounded_detail(message, expected):
    from my_repos_ctl import cli

    excerpt = getattr(cli, "_pull_excerpt", None)
    assert callable(excerpt), "pull excerpt helper is not implemented"
    assert excerpt(message) == expected


def test_render_keeps_markup_literal_and_full_identity():
    output = render_table(
        [
            {
                "name": "[red]demo[/]",
                "path": "/demo/[red]/repo",
                "status": "failed",
                "message": "fatal: [bold]literal[/] issue",
            }
        ]
    )
    assert "[red]demo[/]" in output
    assert "/demo/[red]/repo" in output
    assert "fatal: [bold]literal[/] issue" in output


def test_render_narrow_table_preserves_every_repo():
    results = [
        {
            "name": name,
            "path": "/demo/" + name,
            "status": status,
            "message": "error: " + "very-long-detail " * 20,
        }
        for name, status in (
            ("good-demo", "ok"),
            ("bad-demo", "failed"),
            ("next", "planned"),
        )
    ]
    output = render_table(results, width=60)
    assert output.index("bad-demo") < output.index("good-demo") < output.index("next")
    assert all(name in output for name in ("good-demo", "bad-demo", "next"))
    assert "Pull summary" in output


@pytest.mark.mutation
def test_render_narrow_table_wraps_full_path_instead_of_clipping():
    path = "/tmp/demo/" + "long-worktree-name-" * 5
    output = render_table(
        [
            {
                "name": "demo/long",
                "path": path,
                "status": "ok",
                "message": "",
            }
        ],
        width=45,
    )
    unwrapped = "".join(output.replace("\u2502", "").split())
    assert path in unwrapped
    assert "\u2026" not in output


def test_empty_successful_stdout_has_explicit_completion_detail(
    remote_pair, config_factory, monkeypatch, invoke_cli
):
    from my_repos_ctl import cli

    original_git = cli._git

    def empty_pull_stdout(repo, args, timeout):
        if args[0] == "pull":
            original_git(repo, args, timeout)
            return ""
        return original_git(repo, args, timeout)

    monkeypatch.setattr(cli, "_git", empty_pull_stdout)
    config = config_factory({"demo/docs": remote_pair.local})
    code, captured = invoke(invoke_cli, ["--config", str(config), "pull", "--quiet"])
    assert code == 0
    assert "Completed" in captured.out
    assert "1 pulled" in captured.out


def test_quiet_is_rejected_by_other_commands(config_factory, tmp_path, invoke_cli):
    config = config_factory({"demo/docs": tmp_path / "repo"})
    with pytest.raises(SystemExit) as exc:
        invoke_cli(["--config", str(config), "list", "--quiet"])
    assert exc.value.code == 2
