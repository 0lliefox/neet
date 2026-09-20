"""`neet capture` — run due capture sources once (intended to be invoked every 10 min by launchd/cron).

    neet capture                      run all due sources
    neet capture --sources rss ea     run only these (ignores cadence)
    neet capture --force              ignore cadence for all
    neet capture --status             print last health per source
    neet capture --data-root PATH     override capture.data_root / $NEET_DATA_ROOT
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import requests

from .base import Context, env_snapshot
from .registry import all_sources
from .state import State
from .store import Store, utcnow

logger = logging.getLogger("neet.capture")


def _data_root(cfg: dict, override: str | None) -> Path:
    root = override or os.environ.get("NEET_DATA_ROOT") or (cfg.get("data_root")) or "data/capture_root"
    return Path(root).expanduser()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="neet capture")
    ap.add_argument("--config", default=None)
    ap.add_argument("--sources", nargs="+", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    from scraper.config import load as load_config
    cfg_all = load_config(args.config)
    cfg = cfg_all.get("capture", {}) or {}
    store = Store(_data_root(cfg, args.data_root))
    log_path = store.logs_dir / "capture.log"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(log_path), logging.StreamHandler(sys.stdout)])

    if args.status:
        for src, rec in sorted(store.last_health().items()):
            print(f"{src:24s} {rec.get('at','')}  ok={rec.get('ok')}  n={rec.get('n_items')}  {rec.get('error','') or ''}")
        return 0

    env = env_snapshot()
    session = requests.Session()
    n_ok = n_err = 0
    for src in all_sources(cfg):
        if args.sources and src.name not in args.sources:
            continue
        state = State(store.state_dir, src.name)
        if not args.force and not args.sources and not state.due(src.cadence_s):
            continue
        missing = src.missing_env(env)
        if missing:
            store.health({"source": src.name, "ok": False, "skipped": "missing_env", "missing": missing})
            continue
        # per-source lock: a long download (e.g. Street Manager archives) must not block the other sources
        lock = store.state_dir / f".lock_{src.name}"
        if lock.exists():
            age = time.time() - lock.stat().st_mtime
            if age < max(6 * 3600, 3 * src.cadence_s):
                logger.info("%s: previous run still active (lock age %.0fs); skipping", src.name, age)
                continue
            lock.unlink(missing_ok=True)  # stale
        lock.write_text(str(os.getpid()))
        t0 = time.time()
        ctx = Context(store=store, state=state, config=src.cfg, env=env, session=session)
        try:
            payloads = src.fetch(ctx)
            n_items = 0
            statuses = []
            n_written = n_unchanged = 0
            for p in payloads:
                if p is None:
                    continue
                if store.write(src.name, p) is None:
                    n_unchanged += 1
                else:
                    n_written += 1
                n_items += p.n_items or 0
                statuses.append(p.meta.get("status"))
            ok = all((s is None) or (200 <= int(s) < 300) for s in statuses) if statuses else True
            state.mark_run(ok=ok); state.save()
            store.health({"source": src.name, "ok": ok, "n_payloads": len(payloads), "n_written": n_written,
                          "n_unchanged": n_unchanged, "n_items": n_items,
                          "duration_s": round(time.time() - t0, 1), "statuses": statuses[:20]})
            n_ok += 1
        except Exception as exc:
            logger.exception("source %s failed", src.name)
            state.mark_run(ok=False); state.save()
            store.health({"source": src.name, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300],
                          "duration_s": round(time.time() - t0, 1)})
            n_err += 1
        finally:
            lock.unlink(missing_ok=True)
    if True:
        logger.info("capture run done: %d ok, %d failed", n_ok, n_err)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
