# Gitra

Gitra is a lightweight local Git repository browser. Run `gitra` inside a
repository to start a local web server and open the repository in your browser.

## Install from a checkout

```bash
python -m venv .venv
.venv/bin/python -m pip install .
```

## Run

```bash
cd /path/to/your/repository
gitra
```

Use `gitra --no-browser` to keep the server in the foreground without opening a
browser. `--host` and `--port` control the listener; by default Gitra binds to
loopback and chooses an available port.

The current MVP browses tracked and untracked, non-ignored top-level files and
their first-level directories. The project roadmap prioritizes a faithful local
repository view, then working-tree diffs and review interactions, followed by
remote pull requests and issues. GitHub's visual design is a reference; Gitra
does not copy GitHub source code or proprietary assets.

## Performance

Gitra currently uses Python's standard library for its server and Git command
line for repository data. Startup and navigation performance should be measured
on representative repositories; no microsecond-level claim is made.
