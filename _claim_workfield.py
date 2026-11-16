"""一次性：选工作领域 → 确定 → 若卡片变「领取」就领掉（+100）。

★ 只选「多媒体创作」一项（导演/3D 动画最贴切），不多选、不改别的设置。
用法： py _claim_workfield.py [--dry-run]
"""
from __future__ import annotations

import datetime as dt
import sys

sys.path.insert(0, ".")

from sites import doubao as d          # noqa: E402

DRY = "--dry-run" in sys.argv
FIELD = "多媒体创作"

JS_MARK_GO = r"""
(keyword) => {
  document.querySelectorAll('[data-wf]').forEach(e => e.removeAttribute('data-wf'));
  for (const c of document.querySelectorAll('[class*="card-"]')) {
    if ((c.innerText || '').indexOf(keyword) === -1) continue;
    const btn = [...c.querySelectorAll('button,[role=button]')]
      .find(b => (b.innerText || '').trim() === '去完成');
    if (!btn) return {ok: false, why: '没有「去完成」', text: (c.innerText||'').replace(/\s+/g,' ')};
    btn.setAttribute('data-wf', 'go');
    return {ok: true, text: (c.innerText || '').replace(/\s+/g, ' ')};
  }
  return {ok: false, why: '没找到这张卡'};
}
"""

JS_MARK_FIELD = r"""
(field) => {
  document.querySelectorAll('[data-wf-opt]').forEach(e => e.removeAttribute('data-wf-opt'));
  const dlg = [...document.querySelectorAll('[role=dialog],[class*=modal],[class*=Modal],[class*=drawer],[class*=Drawer]')]
    .find(e => (e.innerText || '').indexOf('工作领域') !== -1);
  if (!dlg) return {ok: false, why: '没找到领域浮层'};
  const cands = [...dlg.querySelectorAll('*')].filter(e => {
    const t = (e.innerText || '').trim();
    if (t !== field) return false;
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    return r.width > 24 && r.height > 16 && s.display !== 'none' && s.visibility !== 'hidden';
  });
  if (!cands.length) return {ok: false, why: '浮层里没有「' + field + '」'};
  const el = cands.sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width)[0];
  el.setAttribute('data-wf-opt', '1');
  const r = el.getBoundingClientRect();
  return {ok: true, x: Math.round(r.x), y: Math.round(r.y),
          w: Math.round(r.width), h: Math.round(r.height),
          tag: el.tagName.toLowerCase(), cls: (el.className || '').toString().slice(0, 50)};
}
"""

JS_MARK_CONFIRM = r"""
() => {
  document.querySelectorAll('[data-wf-ok]').forEach(e => e.removeAttribute('data-wf-ok'));
  const dlg = [...document.querySelectorAll('[role=dialog],[class*=modal],[class*=Modal],[class*=drawer],[class*=Drawer]')]
    .find(e => (e.innerText || '').indexOf('工作领域') !== -1);
  if (!dlg) return {ok: false, why: '没找到领域浮层'};
  const btn = [...dlg.querySelectorAll('button,[role=button]')]
    .find(b => (b.innerText || '').trim() === '确定');
  if (!btn) return {ok: false, why: '没有「确定」'};
  btn.setAttribute('data-wf-ok', '1');
  const r = btn.getBoundingClientRect();
  return {ok: true, x: Math.round(r.x), y: Math.round(r.y)};
}
"""

JS_CARD_STATE = r"""
() => {
  for (const c of document.querySelectorAll('[class*="card-"]')) {
    if ((c.innerText || '').indexOf('选择工作领域') === -1) continue;
    return {
      text: (c.innerText || '').replace(/\s+/g, ' '),
      btns: [...c.querySelectorAll('button,[role=button]')]
        .map(b => (b.innerText || '').trim()).filter(Boolean),
      marked: !!c.querySelector('[class*="rewarded"]'),
    };
  }
  return null;
}
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

            go = page.evaluate(JS_MARK_GO, "选择工作领域")
            print("① 找卡：%s" % go)
            if not go.get("ok"):
                print("   卡片已不是「去完成」，直接看状态：%s" % page.evaluate(JS_CARD_STATE))
                return

            page.locator('[data-wf="go"]').first.click(timeout=10000)
            page.wait_for_timeout(2000)

            opt = page.evaluate(JS_MARK_FIELD, FIELD)
            print("② 找领域「%s」：%s" % (FIELD, opt))
            if not opt.get("ok"):
                return
            if DRY:
                print("   --dry-run，不选不改")
                page.screenshot(path="%s/wf-%s-dry.png" % (out, stamp))
                return

            page.locator('[data-wf-opt="1"]').first.click(timeout=8000)
            page.wait_for_timeout(800)
            ok = page.evaluate(JS_MARK_CONFIRM)
            print("③ 找「确定」：%s" % ok)
            if not ok.get("ok"):
                return

            acc_before = page.evaluate(d.JS_ACC)
            page.locator('[data-wf-ok="1"]').first.click(timeout=8000)
            page.wait_for_timeout(2500)
            acc_mid = page.evaluate(d.JS_ACC)
            print("④ 已确定。额度 %s → %s" % (acc_before, acc_mid))

            state = page.evaluate(JS_CARD_STATE)
            print("⑤ 卡片现在：%s" % state)

            # 若出现「领取」就领
            claim = page.evaluate(r"""
            () => {
              document.querySelectorAll('[data-wf-claim]').forEach(e => e.removeAttribute('data-wf-claim'));
              for (const c of document.querySelectorAll('[class*="card-"]')) {
                if ((c.innerText || '').indexOf('选择工作领域') === -1) continue;
                const b = [...c.querySelectorAll('button,[role=button]')]
                  .find(x => ['领取', '立即领取'].includes((x.innerText || '').trim()));
                if (!b) return {ok: false};
                b.setAttribute('data-wf-claim', '1');
                return {ok: true, text: (b.innerText || '').trim()};
              }
              return {ok: false};
            }
            """)
            print("⑥ 领取按钮：%s" % claim)
            if claim.get("ok"):
                page.locator('[data-wf-claim="1"]').first.click(timeout=8000)
                page.wait_for_timeout(2500)
                print("   已点领取。额度 %s → %s"
                      % (acc_mid, page.evaluate(d.JS_ACC)))

            print("最终额度：%s" % page.evaluate(d.JS_ACC))
            print("卡片最终：%s" % page.evaluate(JS_CARD_STATE))
            shot = "%s/wf-%s.png" % (out, stamp)
            page.screenshot(path=shot)
            print("截图：%s" % shot)
        finally:
            for x in list(ctx.pages):
                if id(x) not in before_ids:
                    try:
                        x.close()
                    except Exception:
                        pass


if __name__ == "__main__":
    main()
