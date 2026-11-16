"""探明「选择工作领域领取奖励」这张卡的流程（一次性配置 + 手动领取）。

★ 只点这张卡的「去完成」，然后 dump 出来的界面；**不选领域、不确认**。
用法： py _probe_doubao_workfield.py
"""
from __future__ import annotations

import datetime as dt
import sys

sys.path.insert(0, ".")

from sites import doubao as d          # noqa: E402

JS_MARK = r"""
(keyword) => {
  document.querySelectorAll('[data-wf]').forEach(e => e.removeAttribute('data-wf'));
  const cards = [...document.querySelectorAll('[class*="card-"]')];
  for (let i = 0; i < cards.length; i++) {
    const c = cards[i];
    const t = (c.innerText || '').trim();
    if (t.indexOf(keyword) === -1) continue;
    const btn = [...c.querySelectorAll('button,[role=button]')]
      .find(b => (b.innerText || '').trim() === '去完成');
    if (!btn) return {found: true, button: null, text: t.replace(/\s+/g, ' ')};
    btn.setAttribute('data-wf', 'go');
    return {found: true, button: 'marked', text: t.replace(/\s+/g, ' ')};
  }
  return {found: false};
}
"""

JS_UI = r"""
() => ({
  url: location.href,
  text: (document.body.innerText || '').slice(0, 2200),
  dialogs: [...document.querySelectorAll('[role=dialog],[class*=modal],[class*=Modal],[class*=drawer],[class*=Drawer],[class*=popover],[class*=Popover]')]
    .map(e => (e.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 500)).filter(Boolean),
  btns: [...document.querySelectorAll('button,[role=button],a')]
    .filter(b => {
      const s = getComputedStyle(b), r = b.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 20 && r.height > 14;
    })
    .map(b => {
      const r = b.getBoundingClientRect();
      return {t: (b.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 20),
              x: Math.round(r.x), y: Math.round(r.y),
              w: Math.round(r.width), h: Math.round(r.height)};
    }),
  chips: [...document.querySelectorAll('[class*=chip],[class*=option],[class*=item],[class*=tag],[class*=select]')]
    .map(e => {
      const t = (e.innerText || '').trim().replace(/\s+/g, ' ');
      const r = e.getBoundingClientRect();
      if (!t || t.length > 30 || r.width < 40 || r.height < 18) return null;
      return {t: t, x: Math.round(r.x), y: Math.round(r.y),
              cls: (e.className || '').toString().replace(/\s+/g, ' ').slice(0, 40)};
    }).filter(Boolean),
})
"""


def main():
    from playwright.sync_api import sync_playwright

    out = d.out_dir()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(d.CDP_DEFAULT)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        before_ids = set(id(x) for x in ctx.pages)
        page = ctx.new_page()
        try:
            page.goto(d.QUOTA_URL, wait_until="domcontentloaded", timeout=30000)
            d._wait_card(page, budget=25)
            page.wait_for_timeout(1200)

            mk = page.evaluate(JS_MARK, "选择工作领域")
            print("标记结果：%s" % mk)
            if not mk.get("button"):
                print("!! 这张卡没有「去完成」（可能已处理过）")
                return

            page.locator('[data-wf="go"]').first.click(timeout=10000)   # 自动滚到可见
            print("已点击「去完成」，等 3 秒…")
            page.wait_for_timeout(3000)

            target = page
            for x in ctx.pages:
                if id(x) not in before_ids and x is not page:
                    target = x
                    print("★ 新标签页：%s" % x.url)
                    target.wait_for_timeout(2500)
                    break

            ui = target.evaluate(JS_UI)
            print("\nURL：%s" % ui["url"])
            print("\n=== 对话框/浮层 ===")
            for dd in ui["dialogs"]:
                print("  · %s" % dd)
            print("\n=== 按钮 ===")
            for b in ui["btns"]:
                print("  %-22r @(%s,%s) %sx%s" % (b["t"], b["x"], b["y"], b["w"], b["h"]))
            print("\n=== 可选项 ===")
            for c in ui["chips"][:40]:
                print("  %-16r @(%s,%s) %s" % (c["t"], c["x"], c["y"], c["cls"]))
            print("\n=== 页面文字 ===")
            print(ui["text"][:1500])

            shot = "%s/workfield-%s.png" % (out, stamp)
            target.screenshot(path=shot)
            print("\n截图：%s" % shot)
        finally:
            for x in list(ctx.pages):
                if id(x) not in before_ids:
                    try:
                        x.close()
                    except Exception:
                        pass


if __name__ == "__main__":
    main()
