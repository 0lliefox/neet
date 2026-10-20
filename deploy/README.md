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
- Status: double-click `NEET status.command` on the Desktop, or `~/neet/.venv/bin/neet capture --status`. The same
  report is written to `$NEET_DATA_ROOT/logs/STATUS.txt` after every run (readable from the Google Drive app). A
  source is STALE once its last success is older than 2 x cadence + 15 min. A macOS notification fires when a new
  problem appears and when everything recovers (`capture.notify: false` turns this off).
- Each run first checks the internet (TCP to `capture.preflight_hosts`); if it is down the run is skipped without
  touching any source's schedule. A failed source retries after 9 min, doubling per consecutive failure up to its
  cadence. If the data root's parent is missing (Drive not mounted yet) the run exits without creating it.
- Surviving a restart: the agent is a LaunchAgent, so it only runs once `ofox` is logged in. Turn on automatic
  login (System Settings > Users & Groups > Automatically log in as) and `sudo pmset -a autorestart 1` (power back
  on after a power cut).
