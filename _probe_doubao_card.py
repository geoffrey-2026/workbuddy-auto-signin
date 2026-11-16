"""只读探针：量化「每日签到」卡片到底几秒后才渲染出来。

★ 只读：不点击、不领取，只打开页面采样 + 截图，最后关掉标签页。
用法： py _probe_doubao_card.py [最多等秒数，默认 45]
"""
from __future__ import annotations

import datetime as dt
import sys
import time

sys.path.insert(0, ".")

from sites import doubao as d          # noqa: E402

MAX_WAIT = int(sys.argv[1]) if len(sys.argv) > 1 else 45


def main():
    from playwright.sync_api import sync_playwright

    out = d.out_dir()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    shot = "%s/probe-%s.png" % (out, stamp)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(d.CDP_DEFAULT)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            t0 = time.time()
            page.goto(d.QUOTA_URL, wait_until="domcontentloaded", timeout=30000)
            print("goto 完成  %.2fs" % (time.time() - t0))

            # 每秒采样一次，记录卡片、累计额度、页面文字长度
            first_found = None
            for i in range(MAX_WAIT):
                page.wait_for_timeout(1000)
                card = page.evaluate(d.JS_CARD)
                acc = page.evaluate(d.JS_ACC)
                try:
                    txtlen = page.evaluate("() => (document.body.innerText||'').length")
                except Exception:
                    txtlen = -1
                el = time.time() - t0
                ls = "登录按钮: %s" % page.evaluate(d.JS_LOGGED_OUT)
                print("t=%5.1fs  found=%-5s acc=%-6s textlen=%-6s %s"
                      % (el, card.get("found"), acc, txtlen, ls))
                if card.get("found"):
                    first_found = el
                    print("  ★ 卡片出现，用时 %.1fs" % el)
                    print("  claimed=%s cardText=%r"
                          % (card.get("claimed"), (card.get("cardText") or "")[:80]))
                    print("  clickables=%s"
                          % [(c.get("t"), c.get("x"), c.get("y")) for c in
                             (card.get("clickables") or [])])
                    break
                if i in (2, 5, 10, 20):     # 中途留几张图看骨架屏演到哪一步
                    try:
                        page.screenshot(path=shot.replace(".png", "-t%d.png" % i))
                    except Exception:
                        pass

            if first_found is None:
                print("!! %d 秒内始终未见卡片" % MAX_WAIT)
                try:
                    body = page.evaluate("() => (document.body.innerText||'').slice(0,800)")
                    print("页面文字片段：\n%s" % body)
                except Exception:
                    pass

            try:
                page.screenshot(path=shot)
                print("截图：%s" % shot)
            except Exception:
                pass
        finally:
            try:
                page.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
