"""Command-line entry point for the local repository browser."""

from __future__ import annotations

import argparse
import http.server
import json
import socket
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import unquote, urlparse


PAGE = Path(__file__).with_name("index.html").read_text(encoding="utf-8")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def repository_root(path: Path) -> Path:
    root = git(path, "rev-parse", "--show-toplevel")
    return Path(root)


def repository_data(repo: Path) -> dict[str, object]:
    branch = git(repo, "branch", "--show-current") or "detached HEAD"
    commit = git(repo, "log", "-1", "--format=%h %s") or "No commits yet"
    entries = git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    paths = sorted(set(entries.split("\0")) - {""})
    files = []
    for relative in paths:
        if "/" not in relative:
            files.append({
                "name": relative,
                "kind": "file",
                "url": "/api/file/" + relative,
            })
            continue
        directory = relative.split("/", 1)[0] + "/"
        if not any(item["name"] == directory for item in files):
            files.append({"name": directory, "kind": "directory", "url": ""})
    return {
        "name": repo.name,
        "branch": branch,
        "commit": commit,
        "files": files,
    }


def file_content(repo: Path, relative: str) -> str:
    candidate = (repo / relative).resolve()
    if not candidate.is_relative_to(repo.resolve()):
        raise ValueError("File path escapes the repository")
    if not candidate.is_file():
        raise FileNotFoundError(relative)
    return candidate.read_text(encoding="utf-8", errors="replace")


class Handler(http.server.BaseHTTPRequestHandler):
    repo: Path

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(PAGE.encode("utf-8"))
                return
            if path == "/api/repository":
                self.send_json(repository_data(self.repo))
                return
            if path.startswith("/api/file/"):
                self.send_json({"content": file_content(self.repo, path[10:])})
                return
            self.send_error(404)
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as error:
            self.send_json({"error": str(error)}, status=400)

    def send_json(self, value: object, status: int = 200) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def available_port(host: str) -> int:
    with socket.socket() as listener:
        listener.bind((host, 0))
        return listener.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Open a local Git repository browser.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, help="Port (default: choose an available port)")
    args = parser.parse_args()

    try:
        repo = repository_root(Path.cwd())
    except subprocess.CalledProcessError as error:
        parser.error(f"current directory is not inside a Git repository: {error.stderr.strip()}")

    handler = type("RepositoryHandler", (Handler,), {"repo": repo})
    port = args.port or available_port(args.host)
    server = http.server.ThreadingHTTPServer((args.host, port), handler)
    url = f"http://{args.host}:{port}"
    print(f"Viewing {repo} at {url}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
