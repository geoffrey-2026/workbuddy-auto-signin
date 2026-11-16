"""只读探针：把「做任务 领额度」区的每张任务卡结构 dump 出来。

★ 只读：不点击、不领取。只扫描 DOM + 截图。
用法： py _probe_doubao_tasks.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys

sys.path.insert(0, ".")

from sites import doubao as d          # noqa: E402

JS_DUMP = r"""
() => {
  const out = {tasks: [], acc: null, pageText: ''};
  const m = (document.body.innerText || '').match(/累计获得额度[：:]\s*(\d+)/);
  out.acc = m ? parseInt(m[1], 10) : null;

  // 任务卡的特征：文字里带「额度」且带「+数字 额度」，或者带「去完成 / 已完成 / 已领取」
  const seen = new Set();
  const all = [...document.querySelectorAll('*')];
  const cands = all.filter(el => {
    const t = (el.innerText || '').trim();
    if (!t || t.length > 120) return false;
    if (!/\+?\s*\d+\s*额度/.test(t)) return false;
    return true;
  });

  // 只保留「最内层但那层已经包含按钮文案」的容器
  for (const el of cands) {
    const r = el.getBoundingClientRect();
    if (r.width < 200 || r.height < 30 || r.height > 260) continue;
    const cls = (el.className || '').toString();
    const key = cls + '|' + Math.round(r.y);
    if (seen.has(key)) continue;
    // 若它的父节点也是候选，说明还不在卡片级 —— 跳过（保留卡片级那层）
    seen.add(key);
    const btns = [...el.querySelectorAll('button,[role=button],a,div,span')]
      .filter(b => {
        const s = getComputedStyle(b);
        const bb = b.getBoundingClientRect();
        if (s.display === 'none' || s.visibility === 'hidden') return false;
        if (bb.width < 30 || bb.height < 18 || bb.width > 240) return false;
        return b.tagName === 'BUTTON' || b.getAttribute('role') === 'button'
               || s.cursor === 'pointer';
      })
      .map(b => {
        const bb = b.getBoundingClientRect();
        return {t: (b.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 20),
                x: Math.round(bb.x), y: Math.round(bb.y),
                w: Math.round(bb.width), h: Math.round(bb.height),
                tag: b.tagName.toLowerCase(),
                cls: (b.className || '').toString().replace(/\s+/g, ' ').slice(0, 50)};
      });
    out.tasks.push({
      y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
      cls: cls.replace(/\s+/g, ' ').slice(0, 60),
      text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 120),
      marked: !!el.querySelector('[class*="rewarded"],[class*="received"],[class*="done"],[class*="finish"]'),
      btns: btns,
    });
  }
  out.tasks.sort((a, b) => a.y - b.y);
  out.pageText = (document.body.innerText || '').slice(0, 1500);
  return out;
}
"""


def main():
    from playwright.sync_api import sync_playwright

    out = d.out_dir()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    shot = "%s/tasks-%s.png" % (out, stamp)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(d.CDP_DEFAULT)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            page.goto(d.QUOTA_URL, wait_until="domcontentloaded", timeout=30000)
            card, info = d._wait_card(page, budget=25)
            print("卡片等待：found=%s %.1fs textlen=%s"
                  % (card.get("found"), info.get("waited"), info.get("textlen")))
            # 往下滚，让懒加载的任务区都出来
            for _ in range(4):
                page.mouse.wheel(0, 900)
                page.wait_for_timeout(700)
            page.wait_for_timeout(1200)

            data = page.evaluate(JS_DUMP)
            print("累计额度：%s" % data.get("acc"))
            print("扫到卡片：%d 张\n" % len(data.get("tasks") or []))
            for t in data.get("tasks") or []:
                print("y=%-5s h=%-4s marked=%-5s %s"
                      % (t["y"], t["h"], t["marked"], t["text"]))
                print("        cls=%s" % t["cls"])
                for b in t["btns"]:
                    print("        按钮 %-8r @(%s,%s) %sx%s cls=%s"
                          % (b["t"], b["x"], b["y"], b["w"], b["h"], b["cls"]))
                print("")
            try:
                page.screenshot(path=shot, full_page=False)
                print("截图：%s" % shot)
            except Exception:
                pass
            print("--- 页面文字 ---")
            print(data.get("pageText"))
        finally:
            try:
                page.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
