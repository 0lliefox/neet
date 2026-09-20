# Capture host deployment (Mac mini)

- Working copy: `~/neet` (real path). `~/Documents/neet` is a symlink to it. The repo must NOT live under
  `~/Documents`, `~/Desktop` or `~/Downloads`: macOS privacy protection blocks launchd agents from reading those
  folders (PermissionError on `.venv/pyvenv.cfg`) unless Full Disk Access is granted in the GUI.
- Runtime: `uv` (`~/.local/bin/uv`), Python 3.12 (`uv python install 3.12`), `uv venv --python 3.12 .venv`,
  `uv pip install -e ".[dev]"`, `python -m playwright install chromium`.
- Secrets: `~/neet/.env` (mode 600; see `.env.example`). `NEET_DATA_ROOT` points at the Google Drive folder.
- Agent: `deploy/install_launchd.sh` installs `uk.ac.ncl.neet.capture` (every 600 s, RunAtLoad, Nice 10);
  logs in `~/Library/Logs/neet-capture.{out,err}.log`; per-source health in `$NEET_DATA_ROOT/logs/health.jsonl`.
- Update: `cd ~/neet && git pull origin v2 && uv pip install -e . && ./deploy/install_launchd.sh`.
- Status: `~/neet/.venv/bin/neet capture --status`.
