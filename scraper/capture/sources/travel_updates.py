"""Travel North East (Nexus) travel updates — Metro, bus and ferry notices — captured with a headless browser
because nexus.org.uk/travelnortheast.uk sit behind a Cloudflare/consent layer that blocks plain HTTP (403).
The page renders every notice as `div.update-post` (non-selected modes are only hidden), so one load yields all
modes. Stores the raw HTML and a parsed JSON list (title, effective_from/until, body, last_updated, mode guess)."""
from __future__ import annotations

import json
import re

from ..base import CaptureSource, Context
from ..store import Payload

URL = "https://travelnortheast.uk/travel-updates/?transport=metro&type=live-update"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"


def parse_updates(html: str) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    out = []
    for i, div in enumerate(soup.select("div.update-post")):
        text = re.sub(r"[ \t]+", " ", div.get_text("\n", strip=True))
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        rec = {"index": i, "hidden": "hidden" in (div.get("class") or []), "title": lines[0] if lines else "",
               "effective_from": None, "effective_until": None, "last_updated": None, "body": "", "html": str(div)}
        body = []
        for l in lines[1:]:
            m = re.match(r"Effective from:\s*(.+)", l)
            if m:
                rec["effective_from"] = m.group(1); continue
            m = re.match(r"Effective until:\s*(.+)", l)
            if m:
                rec["effective_until"] = m.group(1); continue
            m = re.match(r"Last updated:\s*(.+)", l)
            if m:
                rec["last_updated"] = m.group(1); continue
            if l in ("For more details",):
                continue
            body.append(l)
        rec["body"] = " ".join(body)
        # mode/type hints from ancestors' data attributes or classes, if present
        hints = {}
        for anc in [div] + list(div.parents)[:4]:
            for k, v in (getattr(anc, "attrs", {}) or {}).items():
                if k.startswith("data-") and k not in hints:
                    hints[k] = v
        rec["hints"] = hints
        blob = (rec["title"] + " " + rec["body"]).lower()
        rec["mode_guess"] = ("metro" if re.search(r"\bmetro\b|station|platform|train", blob) else
                             "ferry" if "ferry" in blob else
                             "bus" if re.search(r"\bbus|route|service \d|services \d", blob) else "unknown")
        out.append(rec)
    return out


class TravelUpdatesSource(CaptureSource):
    name = "travel_updates"
    cadence_s = 3600

    def fetch(self, ctx: Context) -> list[Payload]:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_context(user_agent=UA, locale="en-GB").new_page()
            resp = page.goto(self.cfg.get("url", URL), wait_until="networkidle", timeout=60000)
            for sel in ('button:has-text("Deny")', "text=Deny", ".cmplz-deny"):
                try:
                    page.click(sel, timeout=2500)
                    break
                except Exception:
                    pass
            page.wait_for_timeout(1500)
            html = page.content()
            status = resp.status if resp else None
            final = page.url
            b.close()
        updates = parse_updates(html)
        raw = Payload(tag="page", body=html.encode("utf-8"), ext="html", meta={"url": final, "status": status}, n_items=len(updates))
        parsed = Payload(tag="updates", body=json.dumps({"url": final, "updates": [{k: v for k, v in u.items() if k != "html"} for u in updates]},
                                                        ensure_ascii=False).encode("utf-8"), ext="json",
                         meta={"url": final, "status": status}, n_items=len(updates))
        return [raw, parsed]
