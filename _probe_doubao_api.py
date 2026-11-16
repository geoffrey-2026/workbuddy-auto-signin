"""只读探针：抓豆包「做任务 领额度」页面的接口，看任务状态与领取端点。

★ 只读：不点击、不领。只监听网络响应 + 截屏。
用法： py _probe_doubao_api.py
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from sites import doubao as d          # noqa: E402

HITS = []


def _on_response(resp):
    try:
        rt = resp.request.resource_type
    except Exception:
        rt = ""
    if rt not in ("xhr", "fetch"):
        return
    try:
        ct = (resp.headers or {}).get("content-type", "")
        if "json" not in ct.lower():
            return
        body = resp.text()
    except Exception:
        return
    url = resp.url
    keep = any(k in body for k in ("额度", "task", "Task", "reward", "Reward"))
    HITS.append({"url": url, "status": resp.status, "keep": keep, "body": body[:6000]})


def main():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(d.CDP_DEFAULT)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.on("response", _on_response)
        try:
            page.goto(d.QUOTA_URL, wait_until="domcontentloaded", timeout=30000)
            d._wait_card(page, budget=25)
            for _ in range(5):
                page.mouse.wheel(0, 800)
                page.wait_for_timeout(600)
            page.wait_for_timeout(1500)

            print("抓到 %d 条 XHR/JSON 响应，其中命中关键词 %d 条\n"
                  % (len(HITS), len([h for h in HITS if h["keep"]])))
            for h in [h for h in HITS if h["keep"]]:
                print("=" * 90)
                print("%s  [%s]" % (h["url"][:300], h["status"]))
                try:
                    obj = json.loads(h["body"])
                    print(json.dumps(obj, ensure_ascii=False, indent=2)[:3500])
                except Exception:
                    print(h["body"][:1500])
                print("")
            print("\n--- 未命中的 URL 清单（供参考）---")
            for h in HITS:
                if not h["keep"]:
                    print("  %s [%s]" % (h["url"][:200], h["status"]))
        finally:
            try:
                page.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
