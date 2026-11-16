"""探明并领取「累计获得额度」进度条上的里程碑图标（额外额度）。

★ 这是**纯领取**（不伪造任何任务完成），符合「只领不造」口径。
★ 只点 ``button.milestone``，绝不点任何「去完成」。
用法：
    py _probe_doubao_milestones.py            只 dump，不点（默认）
    py _probe_doubao_milestones.py --claim    真的点带 pulse 的，并记录额度变化
"""
from __future__ import annotations

import datetime as dt
import sys

sys.path.insert(0, ".")

from sites import doubao as d          # noqa: E402

CLAIM = "--claim" in sys.argv

# 进度条上的里程碑按钮（实测 class: milestone-G1ASHg milestoneAvailable-y4zdpO）
# 未领取的那个内部带 *Pulse* 类名（会呼吸/闪动），已领取的没有。
JS_MILESTONES = r"""
() => {
  return [...document.querySelectorAll('button[class*="milestone"]')].map(b => {
    const r = b.getBoundingClientRect();
    return {
      x: Math.round(r.x), y: Math.round(r.y),
      w: Math.round(r.width), h: Math.round(r.height),
      cls: (b.className || '').toString().replace(/\s+/g, ' '),
      pulse: /pulse/i.test(b.innerHTML || ''),
      txt: (b.innerText || '').trim().slice(0, 30),
      html: b.outerHTML.slice(0, 260),
    };
  });
}
"""


def _scan(page):
    return page.evaluate(JS_MILESTONES) or []


def main():
    from playwright.sync_api import sync_playwright

    out = d.out_dir()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(d.CDP_DEFAULT)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            page.goto(d.QUOTA_URL, wait_until="domcontentloaded", timeout=30000)
            d._wait_card(page, budget=25)
            page.wait_for_timeout(1500)
            page.mouse.wheel(0, -3000)      # 回顶部，保证进度条在视口内
            page.wait_for_timeout(800)

            ms = _scan(page)
            print("里程碑按钮 %d 个：" % len(ms))
            for i, m in enumerate(ms):
                print("  [%d] x=%-5s y=%-4s %sx%s pulse=%-5s cls=%s"
                      % (i, m["x"], m["y"], m["w"], m["h"], m["pulse"], m["cls"][:50]))
                print("      %s" % m["html"][:200])
            print("\nacc before = %s" % page.evaluate(d.JS_ACC))

            if not CLAIM:
                print("\n（只 dump，未点击。加 --claim 才会真点）")
                shot = "%s/bar-%s.png" % (out, stamp)
                page.screenshot(path=shot)
                print("截图：%s" % shot)
                return

            # ★ 只点「带 pulse」的（= 有奖励待领）。点完重扫，直到没有 pulse 或超过 5 轮。
            for round_no in range(5):
                todo = [m for m in _scan(page) if m["pulse"]]
                if not todo:
                    print("\n第 %d 轮：没有待领里程碑了" % (round_no + 1))
                    break
                m = todo[0]
                before = page.evaluate(d.JS_ACC)
                cx, cy = m["x"] + m["w"] // 2, m["y"] + m["h"] // 2
                print("\n第 %d 轮：点 @(%s,%s)%s，额度 before=%s"
                      % (round_no + 1, cx, cy, "（视口内）" if cy > 0 else "（★ 视口外！）", before))
                if cy < 0 or cy > 900:
                    print("  ★ 视口外，先滚回顶部")
                    page.mouse.wheel(0, -3000)
                    page.wait_for_timeout(800)
                    m = [x for x in _scan(page) if x["pulse"]][0]
                    cx, cy = m["x"] + m["w"] // 2, m["y"] + m["h"] // 2
                page.mouse.click(cx, cy)
                page.wait_for_timeout(2500)
                after = page.evaluate(d.JS_ACC)
                print("  额度 %s → %s" % (before, after))
                page.screenshot(path="%s/claim-%s-r%d.png" % (out, stamp, round_no + 1))
                if before == after and page.evaluate(d.JS_ACC) == before:
                    # 额度没变：可能弹的是说明浮层，截图留着看
                    print("  （额度没变，看截图判断是否弹了提示）")

            print("\n最终 acc = %s" % page.evaluate(d.JS_ACC))
            page.screenshot(path="%s/after-%s.png" % (out, stamp))
            print("截图：%s/after-%s.png" % (out, stamp))
        finally:
            try:
                page.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
