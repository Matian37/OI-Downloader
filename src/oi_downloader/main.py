import argparse
import getpass
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path

from oi_downloader.exceptions import LoginFailedError, WebsiteError
from oi_downloader.sync import sync_repo
from oi_downloader.tasks import scrape_tasks
from oi_downloader.web import Website

DISTRIBUTION_NAME = "oi-downloader"


def _package_metadata() -> tuple[str, str, str]:
    """
    Returns the (name, version, description) of the package.

    The values come from the distribution metadata, which is generated from
    pyproject.toml at build time and bundled by PyInstaller, so the parser works
    without the pyproject.toml file being present next to the executable or having
    duplicated metadata in the source code.
    """
    try:
        meta = metadata(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return DISTRIBUTION_NAME, "unknown", ""

    return meta["Name"], meta["Version"], meta["Summary"]


def is_repo_path_valid(repo_path: Path) -> bool:
    """Checks if the provided repo path is a valid directory."""
    return repo_path.is_dir()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    name, version, description = _package_metadata()

    parser = argparse.ArgumentParser(prog=name, description=description)
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"{name} {version}",
    )
    parser.add_argument(
        "-r",
        "--repo-path",
        type=Path,
        default=None,
        metavar="PATH",
        help="path to the folder containing the checklist and solutions",
    )

    args = parser.parse_args(argv)

    if args.repo_path is not None and not is_repo_path_valid(args.repo_path):
        parser.error(f"Invalid or non-existent repo path: {args.repo_path}")

    return args


def run_login_prompt(website: Website) -> None:
    while True:
        login = input("Enter your Szkopuł username:\n")
        password = getpass.getpass("Enter your password (input hidden):\n")

        try:
            website.login(login, password)
            break
        except LoginFailedError:
            print("⛔ Login failed...")


def run_repo_path_prompt() -> Path:
    while True:
        repo_path = Path(
            input("Enter repo folder containing the checklist and solutions:\n")
        )

        if is_repo_path_valid(repo_path):
            break
        else:
            print("⛔ Invalid or non-existent folder path.")

    return repo_path


def run(repo_path: Path | None = None) -> None:
    print("🚀 Starting downloader...")

    website = Website()
    run_login_prompt(website)
    tasks = scrape_tasks(website)

    if repo_path is None:
        repo_path = run_repo_path_prompt()
    sync_repo(website, tasks, repo_path)

    try:
        website.logout()
    except WebsiteError as e:
        print(f"🚨 Error occurred during logout, continuing, log: {e}")

    print("✅ Everything is up to date.")


def main():
    args = parse_args()

    try:
        run(args.repo_path)
    except KeyboardInterrupt:
        sys.exit(1)
    # parsers raise AssertionError on unexpected website structure
    except AssertionError as e:
        print(f"🚨 Error occurred, contact developer with full error log: {e}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
