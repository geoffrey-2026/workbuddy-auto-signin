"""站点：豆包「额外额度」领取 —— **只领不造**。

背景
----
豆包活动页 ``/pc/agent-explore`` 除了「每日签到 +400」之外，还有两类额外额度：

1. **进度条里程碑**（``button[class*="milestone"]``）
   累计额度到 500 / 2000 / 5000 时各有一份额外额度（实测 200 / 500 / 1300）。
   ★ 权威判据是 ``aria-disabled``：``"false"`` = 可领，``"true"`` = 已领/未达标。
   点了就进账，额度会涨 —— 这是**纯领取**，不涉及任何任务。

2. **任务卡上的「领取」按钮**
   例如「选择工作领域领取奖励 +100」写的是「完成后手动领取奖励」。
   任务**完成之后**，那张卡的按钮会从「去完成」变成「领取」。
   本模块只点「领取」，**绝不点「去完成」**。

★ 口径（2026-11-16 冰哥拍板「只领不造」）
  活动的本意是鼓励真实使用豆包。「去完成」之后再让豆包跑一遍、拿奖励，
  等于伪造使用行为 —— 不做。本模块只做「奖励已经在等你，把手伸过去拿」。

为什么单独一个站点（而不是塞进 doubao.py）
------------------------------------------
签到那条路已经被实跑验证过（有 45 条测试盯着），不该为了多领 1300 去重构它。
独立站点 = 独立的报告行、独立的重跑、坏了也不影响签到。

退出码沿用 doubao.py 的约定（同一个站点的两半，码位不另起一套）。
"""

from __future__ import annotations

import datetime as dt
import os
import time

from . import base
from . import doubao

SITE = "doubao-extra"
TITLE = "豆包额外额度（进度条里程碑 + 任务卡「领取」；只领不造）"
NEEDS = "同「豆包桌面版」：装了豆包电脑版并登录；首次运行会自动带 CDP 端口拉起它"

# ★ 允许点的按钮文字白名单 —— 这是安全护栏，**只有这些算「领取」**
#   ★★ 「领奖励」是实测出来的：点完「选择工作领域 → 确定」后，那张卡的按钮
#      变成的是「**领奖励**」，不是「领取」。我第一版只写了「领取/立即领取」，
#      于是扫不到它 —— 文案最不能猜，只能实测过再写进来。
CLAIM_TEXTS = ("领取", "立即领取", "领奖励", "领取奖励")
# ★ 永远不许点的（写在这里是为了让后人一眼看到边界，JS 侧也再挡一次）
FORBID_TEXTS = ("去完成", "立即签到", "去签到", "签到")

ROUNDS = 5          # 最多领几轮（每轮领一个，领完重扫）


# ---------- 页面探针 ----------

# 起手：把页面滚到顶部，并给"可领的里程碑"打标记
#   ★ 用 data-* 打标记再按标记点，比算坐标稳：不依赖滚动位置，Playwright 还会自动滚到可见
JS_MARK_MILESTONES = r"""
() => {
  document.querySelectorAll('[data-wbextra]').forEach(e => e.removeAttribute('data-wbextra'));
  const btns = [...document.querySelectorAll('button[class*="milestone"]')];
  const out = [];
  btns.forEach((b, i) => {
    const r = b.getBoundingClientRect();
    const disabled = (b.getAttribute('aria-disabled') || '').toLowerCase();
    const claimable = disabled === 'false';
    if (claimable) b.setAttribute('data-wbextra', 'm' + i);
    out.push({
      idx: i,
      code: b.getAttribute('data-reward-code') || '',
      label: b.getAttribute('aria-label') || '',
      disabled: disabled,
      claimable: claimable,
      x: Math.round(r.x), y: Math.round(r.y),
      w: Math.round(r.width), h: Math.round(r.height),
    });
  });
  return out;
}
"""

# 任务卡上的「领取」按钮：只认白名单文字，并把「去完成」明确排除
JS_MARK_TASK_CLAIMS = r"""
(allowList) => {
  document.querySelectorAll('[data-wbextra-task]').forEach(e => e.removeAttribute('data-wbextra-task'));
  const allow = new Set((allowList || []).map(s => String(s).trim()));
  const out = [];
  // 卡片级容器：class 形如 card-XXXX
  const cards = [...document.querySelectorAll('[class*="card-"]')]
    .filter(c => (c.innerText || '').indexOf('额度') !== -1);
  cards.forEach((c, ci) => {
    const cardText = (c.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80);
    [...c.querySelectorAll('button,[role=button]')].forEach(b => {
      const t = (b.innerText || '').trim();
      if (!allow.has(t)) return;                 // ★ 只认「领取 / 立即领取」
      const s = getComputedStyle(b);
      if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return;
      const r = b.getBoundingClientRect();
      if (r.width < 20 || r.height < 14) return;
      b.setAttribute('data-wbextra-task', 't' + ci + '_' + out.length);
      out.push({text: t, card: cardText,
                x: Math.round(r.x), y: Math.round(r.y)});
    });
  });
  return out;
}
"""


# ---------- 判据（可离线测试，无浏览器） ----------

def is_claim_text(text):
    """这个按钮文字算不算「领取」？**只有白名单里的才算。**

    ★ 这条是整个模块的价值观，必须能在离线测试里钉死：
      「去完成」必须返回 False —— 一旦返回 True，就等于代替用户去伪造任务完成。
    """
    t = str(text or "").strip()
    if any(f in t for f in FORBID_TEXTS):
        return False
    return t in CLAIM_TEXTS


def milestone_claimable(disabled_attr):
    """``aria-disabled`` 的取值算不算「可领」。缺省/空串一律按不可领（fail closed）。"""
    return str(disabled_attr or "").strip().lower() == "false"


# ---------- 主流程 ----------

def _log(action, note=""):
    """额外领取单独一本台账，别和签到的 claim-log.tsv 混在一起。"""
    out = doubao.out_dir()
    try:
        os.makedirs(out, exist_ok=True)
        p = os.path.join(out, "extra-log.tsv")
        new = not os.path.exists(p)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            if new:
                f.write("time\tacc\tgained\taction\tnote\n")
            f.write("\t".join([
                dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "-", "-", action, str(note).replace("\t", " ").replace("\n", " ")
            ]) + "\n")
        return p
    except Exception:
        return ""


def run(dry_run=False, force=False, shot=True, cdp=None, launch=True, **_kw):
    """领取豆包额外额度（里程碑 + 任务卡「领取」）。返回签到台统一契约。"""
    cdp = (cdp or os.environ.get("DOUBAO_CDP") or doubao.CDP_DEFAULT).strip()
    out = doubao.out_dir()
    try:
        os.makedirs(out, exist_ok=True)
    except Exception as e:
        return base.make_result(SITE, base.ERROR, "建不了证据目录：%s" % out,
                                detail=repr(e), exit_code=doubao.EXIT_CDP)

    ok, why = doubao._ensure_cdp(cdp, launch=launch)
    if not ok:
        p = _log("cdp-down", why)
        return base.make_result(SITE, base.FAILED, "连不上豆包（%s）" % why,
                                detail=why, evidence=[p], exit_code=doubao.EXIT_CDP)
    try:
        sync_playwright = doubao._playwright()
    except RuntimeError as e:
        p = _log("no-playwright", str(e))
        return base.make_result(SITE, base.ERROR, "缺 playwright 依赖",
                                detail=str(e), evidence=[p], exit_code=doubao.EXIT_CDP)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    shot_path = os.path.join(out, "extra-%s.png" % stamp)
    ev = []

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp)
        except Exception as e:
            pa = _log("cdp-down", repr(e))
            return base.make_result(SITE, base.FAILED, "连不上 CDP（%s）" % cdp,
                                    detail=repr(e), evidence=[pa],
                                    exit_code=doubao.EXIT_CDP)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            try:
                page.goto(doubao.QUOTA_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                pa = _log("page-fail", repr(e))
                return base.make_result(SITE, base.FAILED, "活动页打不开",
                                        detail=repr(e), evidence=[pa],
                                        exit_code=doubao.EXIT_PAGE)

            card, info = doubao._wait_card(page)
            if not card.get("found"):
                return doubao._login_or_card_missing(
                    page, info, shot_path, ev, shot, "extra")
            page.wait_for_timeout(1200)

            acc0 = page.evaluate(doubao.JS_ACC)
            gained = 0
            did = []

            # ---- 1) 进度条里程碑 ----
            for rnd in range(ROUNDS):
                miles = page.evaluate(JS_MARK_MILESTONES) or []
                claimable = [m for m in miles
                             if milestone_claimable(m.get("disabled"))]
                if not claimable:
                    break
                m = claimable[0]
                if dry_run:
                    did.append("可领里程碑 %s（%s，--dry-run 未点）"
                               % (m.get("code"), m.get("label")))
                    break
                before = page.evaluate(doubao.JS_ACC)
                sel = '[data-wbextra="m%d"]' % m["idx"]
                try:
                    page.locator(sel).first.click(timeout=8000)
                except Exception as e:
                    did.append("里程碑 %s 点了但点不动（%r）" % (m.get("code"), e))
                    break
                page.wait_for_timeout(2500)
                after = page.evaluate(doubao.JS_ACC)
                delta = (after - before) if (before is not None and after is not None) else 0
                gained += max(0, delta or 0)
                did.append("里程碑 %s %s +%s（%s → %s）"
                           % (m.get("code"), m.get("label"), delta, before, after))

            # ---- 2) 任务卡上的「领取」（★ 只点白名单，绝不点「去完成」）----
            for rnd in range(ROUNDS):
                tasks = page.evaluate(JS_MARK_TASK_CLAIMS, list(CLAIM_TEXTS)) or []
                tasks = [t for t in tasks if is_claim_text(t.get("text"))]
                if not tasks:
                    break
                t = tasks[0]
                if dry_run:
                    did.append("有「%s」待领：%s（--dry-run 未点）"
                               % (t.get("text"), t.get("card")))
                    break
                before = page.evaluate(doubao.JS_ACC)
                try:
                    page.locator('[data-wbextra-task]').first.click(timeout=8000)
                except Exception as e:
                    did.append("任务卡「领取」点不动（%r）" % (e,))
                    break
                page.wait_for_timeout(2500)
                after = page.evaluate(doubao.JS_ACC)
                delta = (after - before) if (before is not None and after is not None) else 0
                gained += max(0, delta or 0)
                did.append("任务卡 %s 领取 +%s（%s → %s）"
                           % (str(t.get("card"))[:24], delta, before, after))

            acc1 = page.evaluate(doubao.JS_ACC)
            if shot:
                try:
                    page.screenshot(path=shot_path)
                    ev.append(shot_path)
                except Exception:
                    pass

            detail = "\n".join(did) or "页面上没有任何可领项"
            if dry_run:
                pa = _log("dry-run", detail)
                return base.make_result(SITE, base.SKIPPED,
                                        "--dry-run：只查不领（当前 %s 点）" % acc0,
                                        detail=detail, evidence=ev + [pa],
                                        exit_code=doubao.EXIT_OK)

            if gained > 0:
                pa = _log("claimed", detail)
                return base.make_result(SITE, base.CLAIMED,
                                        "额外领到 +%s（%s → %s）" % (gained, acc0, acc1),
                                        detail=detail, evidence=ev + [pa],
                                        exit_code=doubao.EXIT_OK)

            pa = _log("nothing", detail)
            return base.make_result(SITE, base.ALREADY,
                                    "没有可领的额外额度（累计 %s）" % acc1,
                                    detail=detail, evidence=ev + [pa],
                                    exit_code=doubao.EXIT_OK)
        finally:
            try:
                page.close()
            except Exception:
                pass


def doctor():
    return {
        "site": SITE,
        "cdp": os.environ.get("DOUBAO_CDP") or doubao.CDP_DEFAULT,
        "cdp_alive": doubao._cdp_alive(os.environ.get("DOUBAO_CDP")
                                       or doubao.CDP_DEFAULT),
        "claim_texts": list(CLAIM_TEXTS),
        "forbid_texts": list(FORBID_TEXTS),
        "out_dir": doubao.out_dir(),
    }
