"""Command-line entry point for the local repository browser."""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socket
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import unquote, urlparse


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gitra</title>
<style>
:root{color-scheme:dark;--canvas:#0d1117;--panel:#161b22;--border:#30363d;
--text:#e6edf3;--muted:#8b949e;--blue:#2f81f7;--green:#3fb950}
*{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--text);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{height:64px;background:#010409;border-bottom:1px solid var(--border);
display:flex;align-items:center;padding:0 max(24px,calc((100vw - 1280px)/2));gap:16px}
.mark{font-size:20px;font-weight:700}.repo{color:var(--muted)}
main{max-width:1280px;margin:24px auto;padding:0 24px}
nav{display:flex;gap:24px;border-bottom:1px solid var(--border);margin-bottom:24px}
nav a{padding:12px 4px;color:var(--muted);text-decoration:none}
nav a.active{color:var(--text);border-bottom:2px solid #f78166}
.summary,.box{border:1px solid var(--border);border-radius:6px;background:var(--panel)}
.summary{padding:16px;margin-bottom:16px}.box{overflow:hidden}
.row{display:flex;align-items:center;gap:12px;padding:10px 16px;border-top:1px solid var(--border)}
.row:first-child{border-top:0}.row a{color:var(--blue);text-decoration:none}
.row a:hover{text-decoration:underline}.name{flex:1}.muted{color:var(--muted)}
pre{margin:0;padding:16px;overflow:auto;background:var(--canvas);font:12px/20px ui-monospace,monospace}
.error{color:#ff7b72}
@media(max-width:700px){main{padding:0 12px}header{padding:0 12px}.summary{padding:12px}}
</style>
</head>
<body><header><span class="mark">◉</span><span class="repo" id="repo">Repository</span>
<span class="muted" id="branch"></span></header>
<main><nav><a class="active" href="#code">Code</a><a href="#changes">Changes</a></nav>
<section class="summary" id="summary">Loading repository…</section>
<section class="box" id="content"></section></main>
<script>
const root=document.getElementById("content");
function row(name, detail, url=""){
  const container=document.createElement("div");
  container.className="row";
  const label=document.createElement("span");
  label.className="name";
  if(url){
    const link=document.createElement("a");
    link.href=url;
    link.textContent=name;
    label.append(link);
  }else{
    label.textContent=name;
  }
  const metadata=document.createElement("span");
  metadata.className="muted";
  metadata.textContent=detail;
  container.append(label,metadata);
  return container;
}
async function load(){
  const response=await fetch("/api/repository");
  const data=await response.json();
  document.getElementById("repo").textContent=data.name;
  document.getElementById("branch").textContent=data.branch;
  document.getElementById("summary").textContent=`${data.branch} · ${data.commit}`;
  root.replaceChildren(...data.files.map(file=>row(file.name,file.kind,file.url)));
}
async function showFile(path){
  const response=await fetch(`/api/file/${encodeURIComponent(path)}`);
  const data=await response.json();
  root.replaceChildren();
  const back=document.createElement("div");
  back.className="row";
  back.innerHTML="<a href='#code'>← Repository</a>";
  const preview=document.createElement("pre");
  root.append(back,preview);
  preview.textContent=data.content;
}
root.addEventListener("click",event=>{
  const link=event.target.closest("a[href^='/api/file/']");
  if(link){event.preventDefault();showFile(decodeURIComponent(link.pathname.slice(10)));}
});
load().catch(error=>{
  const message=document.createElement("div");
  message.className="row error";
  message.textContent=error.message;
  root.replaceChildren(message);
});
</script></body></html>"""


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
