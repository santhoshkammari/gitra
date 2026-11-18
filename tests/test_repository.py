import subprocess

import pytest

from gitra.cli import file_content, repository_data, repository_root


def make_repository(tmp_path):
    repo = tmp_path / "sample"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
    )
    (repo / "README.md").write_text("# Sample\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "."],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "Initial commit"],
        check=True,
    )
    return repo


def test_repository_root_finds_parent_repository(tmp_path):
    repo = make_repository(tmp_path)
    nested = repo / "src"

    assert repository_root(nested) == repo


def test_repository_data_lists_root_files_and_directories(tmp_path):
    repo = make_repository(tmp_path)

    data = repository_data(repo)

    assert data["name"] == "sample"
    assert data["commit"].endswith("Initial commit")
    assert {"name": "README.md", "kind": "file", "url": "/api/file/README.md"} in data["files"]
    assert {"name": "src/", "kind": "directory", "url": ""} in data["files"]


def test_file_content_reads_repository_file(tmp_path):
    repo = make_repository(tmp_path)

    assert file_content(repo, "README.md") == "# Sample\n"


def test_file_content_rejects_path_outside_repository(tmp_path):
    repo = make_repository(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="escapes"):
        file_content(repo, "../secret.txt")
