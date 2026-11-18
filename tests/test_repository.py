import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from gitra.cli import (
    Handler, compare_file, file_content, repository_data, repository_root,
    resolve_compare,
)


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
    (repo / "old-name.txt").write_text("rename me\n", encoding="utf-8")
    (repo / "gone.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "binary.bin").write_bytes(b"\x00\x01")
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


def test_parent_repository_discovery(tmp_path):
    repo = make_repository(tmp_path)
    nested = repo / "src"

    assert repository_root(nested) == repo


def test_repository_root_entries(tmp_path):
    repo = make_repository(tmp_path)

    data = repository_data(repo)

    assert data["name"] == "sample"
    assert data["commit"].endswith("Initial commit")
    assert {"name": "README.md", "kind": "file", "url": "/api/file/README.md"} in data["files"]
    assert {"name": "src/", "kind": "directory", "url": ""} in data["files"]


def test_repository_file_read(tmp_path):
    repo = make_repository(tmp_path)

    assert file_content(repo, "README.md") == "# Sample\n"


def test_path_escape(tmp_path):
    repo = make_repository(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="escapes"):
        file_content(repo, "../secret.txt")


def commit(repo, message):
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=True)
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()


def test_compare_diff_kinds(tmp_path):
    repo = make_repository(tmp_path)
    base = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    (repo / "README.md").write_text("changed\nlast", encoding="utf-8")
    head = commit(repo, "changes")
    (repo / "gone.txt").unlink()
    (repo / "binary.bin").unlink()
    (repo / "new.txt").write_text("new file\n", encoding="utf-8")
    (repo / "old-name.txt").rename(repo / "renamed.txt")
    final = commit(repo, "rename")

    result = resolve_compare(repo, base, final)
    assert result["merge_base"] == base
    assert result["commits"] == 2
    assert result["files"]
    assert result["adds"] == sum(item["adds"] for item in result["files"])
    assert result["dels"] == sum(item["dels"] for item in result["files"])
    assert any(item["status"].startswith("R") for item in result["files"])
    assert any(item["status"] == "A" for item in result["files"])
    assert any(item["status"] == "D" for item in result["files"])
    detail = compare_file(repo, base, final, "README.md")
    assert any(row["kind"] == "add" for hunk in detail["hunks"] for row in hunk["rows"])
    assert any(
        row.get("text") == "No newline at end of file"
        for hunk in detail["hunks"] for row in hunk["rows"]
    )
    binary = compare_file(repo, base, final, "binary.bin")
    assert "Binary files " in binary["patch"]


def test_three_dot_uses_merge_base(tmp_path):
    repo = make_repository(tmp_path)
    common = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    subprocess.run(["git", "-C", str(repo), "branch", "base"], check=True)
    (repo / "base-only.txt").write_text("base side\n", encoding="utf-8")
    base = commit(repo, "base side")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", common], check=True)
    (repo / "head-only.txt").write_text("head side\n", encoding="utf-8")
    head = commit(repo, "head side")

    result = resolve_compare(repo, base, head)

    assert result["merge_base"] == common
    assert [item["path"] for item in result["files"]] == ["head-only.txt"]


def test_html_content_is_rendered_as_text():
    page = Path(__file__).parents[1] / "gitra" / "index.html"
    source = page.read_text(encoding="utf-8")
    assert ".textContent" in source
    assert ".innerHTML" not in source


def test_compare_rejects_bad_inputs(tmp_path):
    repo = make_repository(tmp_path)
    with pytest.raises(FileNotFoundError, match="Unknown"):
        resolve_compare(repo, "missing", "HEAD")
    with pytest.raises(ValueError, match="Invalid"):
        compare_file(repo, "HEAD", "HEAD", "../outside")


def test_api_unknown_ref_returns_404(tmp_path):
    repo = make_repository(tmp_path)
    handler = type("TestHandler", (Handler,), {"repo": repo})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/compare?base=missing&head=HEAD"
        with pytest.raises(urllib.error.HTTPError) as response:
            urllib.request.urlopen(url)
        assert response.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
