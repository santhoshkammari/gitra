"""Command-line entry point for the local repository browser."""

from __future__ import annotations

import argparse
import http.server
import json
import socket
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


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


def resolve_compare(repo: Path, base: str, head: str) -> dict[str, object]:
    try:
        base_commit = git(repo, "rev-parse", "--verify", f"{base}^{{commit}}")
        head_commit = git(repo, "rev-parse", "--verify", f"{head}^{{commit}}")
    except subprocess.CalledProcessError as error:
        raise FileNotFoundError("Unknown base or head ref") from error
    merge_base = git(repo, "merge-base", base_commit, head_commit)
    listing = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-status", "-z",
         "--find-renames", merge_base, head_commit],
        check=True, capture_output=True,
    ).stdout.decode("utf-8", errors="replace").split("\0")
    names = []
    index = 0
    while index < len(listing) and listing[index]:
        status = listing[index]
        if status.startswith(("R", "C")):
            names.append((status, listing[index + 1], listing[index + 2]))
            index += 3
        else:
            names.append((status, "", listing[index + 1]))
            index += 2
    files = []
    for status, old_path, new_path in names:
        path = new_path or old_path
        stats = subprocess.run(
            ["git", "-C", str(repo), "diff", "--numstat", "--find-renames",
             merge_base, head_commit, "--", path],
            check=True, capture_output=True, text=True,
        ).stdout.split("\t")
        adds = int(stats[0]) if stats and stats[0].isdigit() else 0
        dels = int(stats[1]) if len(stats) > 1 and stats[1].isdigit() else 0
        files.append({
            "path": path, "old_path": old_path, "status": status,
            "adds": adds, "dels": dels,
        })
    commits = git(repo, "rev-list", "--count", f"{base_commit}..{head_commit}")
    return {
        "base": base, "head": head, "merge_base": merge_base,
        "commits": int(commits), "files": files,
        "adds": sum(item["adds"] for item in files),
        "dels": sum(item["dels"] for item in files),
    }


def compare_file(repo: Path, base: str, head: str, path: str) -> dict[str, object]:
    if not path or Path(path).is_absolute() or ".." in Path(path).parts:
        raise ValueError("Invalid repository path")
    data = resolve_compare(repo, base, head)
    item = next((entry for entry in data["files"] if entry["path"] == path), None)
    if item is None:
        raise FileNotFoundError(path)
    patch = subprocess.run(
        ["git", "-C", str(repo), "diff", "--no-ext-diff", "--no-color",
         "--find-renames", "--unified=3", data["merge_base"], head, "--", path],
        check=True, capture_output=True,
    ).stdout.decode("utf-8", errors="replace")
    hunks = []
    current = None
    old_line = new_line = 0
    for line in patch.splitlines():
        if line.startswith("@@"):
            import re
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if match:
                old_line, new_line = int(match[1]), int(match[3])
            current = {"header": line, "rows": []}
            hunks.append(current)
            continue
        if current is None or line.startswith(("diff ", "index ", "--- ", "+++ ")):
            continue
        if line == r"\ No newline at end of file":
            current["rows"].append({"kind": "note", "text": "No newline at end of file"})
        elif line.startswith("+"):
            current["rows"].append({
                "kind": "add", "old": None, "new": new_line,
                "marker": "+", "text": line[1:],
            })
            new_line += 1
        elif line.startswith("-"):
            current["rows"].append({
                "kind": "delete", "old": old_line, "new": None,
                "marker": "−", "text": line[1:],
            })
            old_line += 1
        elif line.startswith(" "):
            current["rows"].append({
                "kind": "context", "old": old_line, "new": new_line,
                "marker": " ", "text": line[1:],
            })
            old_line += 1
            new_line += 1
        elif line.startswith("Binary files "):
            current["rows"].append({"kind": "note", "text": "Binary file not shown"})
    return {**item, "patch": patch, "hunks": hunks}


class Handler(http.server.BaseHTTPRequestHandler):
    repo: Path

    def send_page(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        body = PAGE.encode("utf-8")
        self.wfile.write(body)

    def send_file_compare(self, query: dict[str, list[str]]) -> None:
        detail = compare_file(
            self.repo,
            query["base"][0],
            query["head"][0],
            query["path"][0],
        )
        self.send_json(detail)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path == "/":
                self.send_page()
                return

            if path == "/api/repository":
                self.send_json(repository_data(self.repo))
                return

            if path == "/api/compare":
                self.send_json(resolve_compare(self.repo, query["base"][0], query["head"][0]))
                return

            if path == "/api/compare/file":
                self.send_file_compare(query)
                return

            if path.startswith("/compare/") or path.startswith("/pulls/"):
                self.send_page()
                return

            if path.startswith("/api/file/"):
                self.send_json({"content": file_content(self.repo, path[10:])})
                return

            self.send_error(404)
        except KeyError:
            self.send_json({"error": "base and head are required"}, status=400)
        except FileNotFoundError as error:
            self.send_json({"error": str(error)}, status=404)
        except (subprocess.CalledProcessError, ValueError) as error:
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
    parser.add_argument("--compare", help="Open a comparison in base...head form")
    parser.add_argument("--title", default="", help="Comparison title")
    args = parser.parse_args()

    try:
        repo = repository_root(Path.cwd())
    except subprocess.CalledProcessError as error:
        parser.error(f"current directory is not inside a Git repository: {error.stderr.strip()}")

    handler = type("RepositoryHandler", (Handler,), {"repo": repo})
    port = args.port or available_port(args.host)
    server = http.server.ThreadingHTTPServer((args.host, port), handler)
    url = f"http://{args.host}:{port}"
    if args.compare:
        if "..." not in args.compare:
            parser.error("--compare must be written as base...head")
        base, head = args.compare.split("...", 1)
        if not base or not head:
            parser.error("--compare must include base and head refs")
        url += f"/compare/{base}...{head}"
        if args.title:
            from urllib.parse import quote
            url += "?title=" + quote(args.title)
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
