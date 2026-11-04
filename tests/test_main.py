from importlib.metadata import metadata as pkg_metadata
from importlib.metadata import version as pkg_version
from pathlib import Path
from unittest.mock import call

import pytest
import requests
from helpers import make_response
from pytest_mock import MockerFixture, MockType

from oi_downloader import main
from oi_downloader.exceptions import LoginFailedError, WebsiteError
from oi_downloader.sync import SOLUTIONS_FOLDER
from oi_downloader.web import (
    HOME_PAGE,
    LOGIN_PAGE,
    LOGOUT_PAGE,
    SUBMISSION_PAGE,
    TASKS_PAGE,
)


def test_package_metadata_success() -> None:
    meta = pkg_metadata(main.DISTRIBUTION_NAME)

    assert main._package_metadata() == (meta["Name"], meta["Version"], meta["Summary"])


def test_package_metadata_missing(mocker: MockerFixture) -> None:
    mocker.patch.object(main, "metadata", side_effect=main.PackageNotFoundError)

    assert main._package_metadata() == (main.DISTRIBUTION_NAME, "unknown", "")


def test_parse_args_default_repo_path() -> None:
    args = main.parse_args([])
    assert args.repo_path is None


def test_parse_args_repo_path(tmp_path: Path) -> None:
    assert main.is_repo_path_valid(tmp_path) is True

    args = main.parse_args(["--repo-path", str(tmp_path)])
    assert args.repo_path == tmp_path


def test_parse_args_repo_path_short(tmp_path: Path) -> None:
    assert main.is_repo_path_valid(tmp_path) is True

    args = main.parse_args(["-r", str(tmp_path)])
    assert args.repo_path == tmp_path


def test_parse_args_invalid_repo_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert main.is_repo_path_valid(missing) is False

    with pytest.raises(SystemExit) as exc:
        main.parse_args(["--repo-path", str(missing)])

    assert exc.value.code == 2


def test_parse_args_version(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit):
        main.parse_args(["--version"])

    expected = f"{main.DISTRIBUTION_NAME} {pkg_version(main.DISTRIBUTION_NAME)}"
    assert expected in capsys.readouterr().out


def test_parse_args_help(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit):
        main.parse_args(["--help"])

    out = capsys.readouterr().out
    assert main.DISTRIBUTION_NAME in out
    assert "--repo-path" in out


def test_is_repo_path_valid_directory(tmp_path: Path) -> None:
    assert main.is_repo_path_valid(tmp_path) is True


def test_is_repo_path_valid_missing(tmp_path: Path) -> None:
    assert main.is_repo_path_valid(tmp_path / "missing") is False


def test_is_repo_path_valid_file(tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.write_text("content")
    assert main.is_repo_path_valid(file) is False


def test_run_login_prompt_success(mocker: MockerFixture) -> None:
    website = mocker.Mock()
    mocker.patch("builtins.input", lambda _: "user")
    mocker.patch.object(main.getpass, "getpass", lambda _: "pass")
    printed = []
    mocker.patch("builtins.print", printed.append)

    main.run_login_prompt(website)

    website.login.assert_called_once_with("user", "pass")
    assert printed == []


def test_run_login_prompt_failure(mocker: MockerFixture) -> None:
    website = mocker.Mock()
    # first attempt fails, the retry succeeds
    website.login.side_effect = [LoginFailedError(), None]
    mocker.patch("builtins.input", lambda _: "user")
    mocker.patch.object(main.getpass, "getpass", lambda _: "pass")
    printed = []
    mocker.patch("builtins.print", printed.append)

    main.run_login_prompt(website)

    assert printed
    assert website.login.call_count == 2


def _run_repo_path_prompt(
    mocker: MockerFixture,
    tmp_path: Path,
    bad_path: Path | None = None,
) -> None:
    """Runs run_repo_path_prompt with a bad path (if given) and finishes with a valid one.

    Checks if printed error message on a bad path and did not on a good path.
    """
    good = tmp_path / "good"
    good.mkdir()

    # ensure the paths are valid and invalid as expected
    assert main.is_repo_path_valid(good) is True
    if bad_path is not None:
        assert main.is_repo_path_valid(bad_path) is False

    paths = [str(bad_path)] if bad_path is not None else []
    paths.append(str(good))

    paths_iter = iter(paths)
    mocker.patch("builtins.input", lambda _: next(paths_iter))
    printed = []
    mocker.patch("builtins.print", printed.append)

    result = main.run_repo_path_prompt()
    assert result == good

    if bad_path is not None:
        assert printed
    else:
        assert printed == []


def test_run_repo_path_prompt_success(mocker: MockerFixture, tmp_path: Path) -> None:
    _run_repo_path_prompt(mocker, tmp_path)


def test_run_repo_path_prompt_invalid(mocker: MockerFixture, tmp_path: Path) -> None:
    _run_repo_path_prompt(mocker, tmp_path, bad_path=Path("\x00"))


def test_run_repo_path_prompt_missing(mocker: MockerFixture, tmp_path: Path) -> None:
    _run_repo_path_prompt(mocker, tmp_path, bad_path=tmp_path / "missing")


def test_run_repo_path_prompt_file(mocker: MockerFixture, tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.write_text("content")
    _run_repo_path_prompt(mocker, tmp_path, bad_path=file)


def _run_with_repo_path(
    mocker: MockerFixture, repo_path: Path
) -> tuple[MockType, MockType, MockType]:
    """Returns (website, sync_repo, run_repo_path_prompt) after running main.run with a repo path."""
    website = mocker.patch.object(main, "Website").return_value
    mocker.patch.object(main, "run_login_prompt")
    mocker.patch.object(main, "scrape_tasks", return_value=[])
    sync_repo = mocker.patch.object(main, "sync_repo")
    run_repo_path_prompt = mocker.patch.object(main, "run_repo_path_prompt")

    main.run(repo_path=repo_path)
    return website, sync_repo, run_repo_path_prompt


def test_run_uses_repo_path_argument(mocker: MockerFixture, tmp_path: Path) -> None:
    website, sync_repo, run_repo_path_prompt = _run_with_repo_path(mocker, tmp_path)

    run_repo_path_prompt.assert_not_called()
    sync_repo.assert_called_once_with(website, [], tmp_path)


def test_run_makes_repo_path_absolute(
    mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    _, sync_repo, _ = _run_with_repo_path(mocker, Path("./checklist"))
    sync_repo.assert_called_once_with(mocker.ANY, [], tmp_path / "checklist")


def test_run_expands_user_in_repo_path(mocker: MockerFixture) -> None:
    _, sync_repo, _ = _run_with_repo_path(mocker, Path("~/checklist"))
    sync_repo.assert_called_once_with(mocker.ANY, [], Path.home() / "checklist")


class TestRun:
    CSRF_FORM = '<input type="hidden" name="csrfmiddlewaretoken" value="mid">'

    # editions 10 and 30 become "x" and "xxx" in the checklist
    TASKS_HTML = (
        '<div class="card" id="problems-problemgroups-10-e2-d0">\n'
        '<a href="foo">Foo (foo)</a>\n'
        '<a class="score" href="/s/101/">50</a>\n'
        '<div class="card" id="problems-problemgroups-30-e1">\n'
        '<a href="bar">Bar (bar)</a>\n'
        '<a class="score" href="/s/202/">100</a>\n'
    )

    SUBMISSION_101 = "int foo_solution;"
    SUBMISSION_202 = "int bar_solution;"

    SUCCESS_MSG = "✅ Everything is up to date."

    @staticmethod
    def _run_app(
        mocker: MockerFixture,
        repo_path: Path,
        get_responses: list,
        post_responses: list,
    ) -> tuple[MockType, MockType, list[str]]:
        """Runs the whole application with requests and responses set by arguments."""
        get = mocker.patch("oi_downloader.web.requests.get", side_effect=get_responses)
        post = mocker.patch(
            "oi_downloader.web.requests.post", side_effect=post_responses
        )

        mocker.patch("oi_downloader.sync.sleep")

        user_inputs = iter(["user", str(repo_path)])
        mocker.patch("builtins.input", lambda _: next(user_inputs))
        mocker.patch.object(main.getpass, "getpass", lambda _: "pass")

        printed: list[str] = []
        mocker.patch("builtins.print", printed.append)

        main.run()
        return get, post, printed

    @staticmethod
    def read_repo(root: Path) -> dict[Path, str | None]:
        return {
            path.relative_to(root): (
                path.read_text(encoding="utf-8") if path.is_file() else None
            )
            for path in root.rglob("*")
        }

    def test_success(self, mocker: MockerFixture, tmp_path: Path) -> None:
        get, post, printed = self._run_app(
            mocker,
            repo_path=tmp_path,
            get_responses=[
                # homepage
                make_response(self.CSRF_FORM, cookies={"csrftoken": "csrf-home"}),
                # tasks page
                make_response(self.TASKS_HTML),
                # submission pages
                make_response(f'<pre id="raw_source">{self.SUBMISSION_101}</pre>'),
                make_response(f'<pre id="raw_source">{self.SUBMISSION_202}</pre>'),
            ],
            post_responses=[
                # successful login
                make_response(
                    status_code=302,
                    cookies={"sessionid": "sid", "csrftoken": "csrf-auth"},
                ),
                # successful logout
                make_response(),
            ],
        )

        assert self.read_repo(tmp_path / SOLUTIONS_FOLDER) == {
            Path("x"): None,
            Path("x/etap2"): None,
            Path("x/etap2/probne"): None,
            Path("x/etap2/probne/foo"): None,
            Path("x/etap2/probne/foo/foo50.cpp"): self.SUBMISSION_101,
            Path("xxx"): None,
            Path("xxx/etap1"): None,
            Path("xxx/etap1/bar"): None,
            Path("xxx/etap1/bar/bar.cpp"): self.SUBMISSION_202,
        }

        assert get.call_args_list == [
            call(HOME_PAGE, cookies={}),
            call(TASKS_PAGE, cookies={"sessionid": "sid", "csrftoken": "csrf-auth"}),
            call(
                SUBMISSION_PAGE.format(101),
                cookies={"sessionid": "sid", "csrftoken": "csrf-auth"},
            ),
            call(
                SUBMISSION_PAGE.format(202),
                cookies={"sessionid": "sid", "csrftoken": "csrf-auth"},
            ),
        ]

        assert post.call_args_list == [
            call(
                LOGIN_PAGE,
                data={
                    "csrfmiddlewaretoken": "mid",
                    "login_view-current_step": "auth",
                    "auth-username": "user",
                    "auth-password": "pass",
                },
                cookies={"csrftoken": "csrf-home"},
                headers={"Referer": HOME_PAGE},
                allow_redirects=False,
            ),
            call(
                LOGOUT_PAGE,
                data={"csrfmiddlewaretoken": "csrf-auth"},
                cookies={"sessionid": "sid", "csrftoken": "csrf-auth"},
                headers={"Referer": HOME_PAGE},
            ),
        ]

        assert printed[-1] == self.SUCCESS_MSG

    def test_website_error_propagates(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        with pytest.raises(WebsiteError):
            self._run_app(
                mocker,
                repo_path=tmp_path,
                get_responses=[
                    make_response(self.CSRF_FORM, cookies={"csrftoken": "csrf-home"}),
                    # the tasks page request fails
                    requests.RequestException(),
                ],
                post_responses=[
                    make_response(
                        status_code=302,
                        cookies={"sessionid": "sid", "csrftoken": "csrf-auth"},
                    )
                ],
            )

        assert self.read_repo(tmp_path) == {}

    def test_logout_error_is_ignored(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        _, _, printed = self._run_app(
            mocker,
            repo_path=tmp_path,
            get_responses=[
                make_response(self.CSRF_FORM, cookies={"csrftoken": "csrf-home"}),
                make_response(self.TASKS_HTML),
                make_response(f'<pre id="raw_source">{self.SUBMISSION_101}</pre>'),
                make_response(f'<pre id="raw_source">{self.SUBMISSION_202}</pre>'),
            ],
            post_responses=[
                make_response(
                    status_code=302,
                    cookies={"sessionid": "sid", "csrftoken": "csrf-auth"},
                ),
                requests.RequestException(),
            ],
        )

        assert (tmp_path / SOLUTIONS_FOLDER).is_dir()
        assert any("Error occurred during logout" in message for message in printed)
        assert printed[-1] == self.SUCCESS_MSG
