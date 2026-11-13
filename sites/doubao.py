"""站点：豆包（Doubao）桌面版每日免费额度领取。

背景
----
官方活动「从容开工，豆包额度天天送」（页面 https://www.doubao.com/pc/agent-explore ）：
  · 每天签到领 200 额度
  · 周一额度翻倍
  · 签到 30 天可得 8800 额度
入口只在 **豆包电脑版**（左侧栏左下角头像 → 免费用豆包）；
网页版与手机 App 没有这个入口。

实现原理
--------
豆包桌面版是 Chromium 内嵌应用，带 ``--remote-debugging-port=9222`` 启动即开放 CDP。
登录 Cookie 与桌面版同 profile ⇒ 用 Playwright 连上去新开一个标签页直接访问
``/pc/agent-explore`` 即可，无需模拟点击侧边栏菜单。

已签到判据
----------
签到卡片内存在 class 含 ``rewarded`` 的元素（实测 ``rewarded-nPpEuG``，就是那个 ✓）
⇒ 今日已签。未签到时该位置是一个按钮。

★ 迁移说明（2026-11-13，workbuddy-main）
  本模块的逻辑整体迁自
  ``ImmuneWarPro/tools/美术生成/doubao_daily_claim.py``（免疫战争窗口所写，已实跑多日）。
  迁移时**只改三处**，业务逻辑与页面探针一字未动：
    ① 包成 ``run()`` 并返回签到台的统一契约（原脚本直接 ``sys.exit``）；
    ② ``playwright`` 改为**延迟 import** —— 原脚本在模块顶部 import，导致一台没装
       playwright 的机器上，整个签到台连 ``list`` 都跑不起来；
    ③ 输出目录可被 ``DOUBAO_CLAIM_LOG_DIR`` 覆盖 —— 让旧路径的转发垫片仍写回原处，
       这样免疫战争窗口的证据目录不受影响。
  ★ 原路径的脚本已改成**转发垫片**，避免两份正本各自演化。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

from . import base

SITE = "doubao"
TITLE = "豆包桌面版（每日 200 额度，周一翻倍）"
NEEDS = "已安装「豆包电脑版」并登录；首次运行会自动带 CDP 端口拉起它"

QUOTA_URL = "https://www.doubao.com/pc/agent-explore"
CDP_DEFAULT = "http://127.0.0.1:9222"
DOUBAO_EXE = os.path.expandvars(r"$LOCALAPPDATA\Doubao\Application\Doubao.exe")

# 退出码（★ 沿用原脚本约定，垫片脚本靠它透传；改这里必须同步改 README 与垫片注释）
EXIT_OK = 0          # 成功：今天刚领到，或今天早已领过（幂等）
EXIT_CDP = 2         # CDP 连不上（豆包没开 / 没带调试端口）
EXIT_PAGE = 3        # 签到页打不开
EXIT_CARD = 4        # 找不到「每日签到」卡片 / 没有可点控件
EXIT_NOCHANGE = 5    # 点了但额度没涨（可能被风控 / 活动下线）
EXIT_LOGIN = 6       # 未登录

# ---------- 页面内探针 ----------

# 1) 登录态：文字恰好是「登录」且宽 20~220 的可见控件
JS_LOGGED_OUT = r"""
() => {
  const cands = [...document.querySelectorAll('button,[role=button],div,span,a')];
  return cands.some(e => {
    const t = (e.innerText || '').trim();
    if (t !== '登录') return false;
    const r = e.getBoundingClientRect();
    if (r.width < 20 || r.width > 220 || r.height < 16 || r.height > 60) return false;
    const s = getComputedStyle(e);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  });
}
"""

# 2) 累计额度
JS_ACC = r"""
() => {
  const m = (document.body.innerText || '').match(/累计获得额度[：:]\s*(\d+)/);
  return m ? parseInt(m[1], 10) : null;
}
"""

# 3) 签到卡片状态 + 可点控件
JS_CARD = r"""
() => {
  const leaf = [...document.querySelectorAll('*')].filter(el => {
    const t = (el.innerText || '').trim();
    return t === '每日签到' && el.children.length === 0;
  });
  if (!leaf.length) return {found: false};

  // ★ 坑：「每日签到 +200 额度」那层内层容器宽度只有 ~408，右侧的 ✓ 在外面。
  //   必须走到卡片级（class 形如 card-XXXXXX，宽 > 450）才算拿到整行，
  //   否则 claimed 永远判成 false ⇒ 每天都会重复点。
  let card = null, cur = leaf[0];
  for (let i = 0; i < 9 && cur; i++) {
    const cls = (cur.className || '').toString();
    const r = cur.getBoundingClientRect();
    if (/(^|\s)card[-_]/i.test(cls) || r.width >= 450) { card = cur; break; }
    cur = cur.parentElement;
  }
  if (!card) return {found: false, reason: '没找到卡片级容器'};

  card.scrollIntoView({block: 'center'});

  const claimedEl = card.querySelector('[class*="rewarded"]');
  const cardText = (card.innerText || '').trim().replace(/\s+/g, ' ');

  // 未签到时找可点控件：button / role=button / cursor:pointer
  const clickables = [...card.querySelectorAll('button,[role=button],div,span,a')]
    .filter(el => {
      const r = el.getBoundingClientRect();
      if (r.width < 30 || r.height < 20 || r.width > 220) return false;
      const s = getComputedStyle(el);
      if (s.display === 'none' || s.visibility === 'hidden') return false;
      if (parseFloat(s.opacity) === 0) return false;
      const isBtn = el.tagName === 'BUTTON' || el.getAttribute('role') === 'button'
                    || s.cursor === 'pointer';
      return isBtn && !el.querySelector('[class*="rewarded"]');
    })
    .map(el => {
      const r = el.getBoundingClientRect();
      return {t: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 16),
              x: Math.round(r.x), y: Math.round(r.y),
              w: Math.round(r.width), h: Math.round(r.height),
              tag: el.tagName.toLowerCase(),
              cls: (el.className || '').toString().replace(/\s+/g, ' ').trim().slice(0, 70)};
    });

  return {
    found: true,
    claimed: !!claimedEl,
    claimedCls: claimedEl ? (claimedEl.className || '').toString().trim().slice(0, 60) : '',
    cardText,
    clickables,
  };
}
"""


# 4) 兜底：页面顶部那颗黑色 CTA（实测 <button class="exploreButton-xxxx">立即领取</button>）
#    卡片里没找到可点控件时用它。官方流程本来就写的是「首页签到卡片 → 立即签到」。
JS_CTA = r"""
() => {
  const RE = /^(立即领取|立即签到|去签到|领取|签到)$/;
  const cands = [...document.querySelectorAll('button,[role=button],a')]
    .filter(el => {
      const t = (el.innerText || '').trim();
      if (!RE.test(t)) return false;
      const r = el.getBoundingClientRect();
      if (r.width < 40 || r.height < 20) return false;
      const s = getComputedStyle(el);
      return s.display !== 'none' && s.visibility !== 'hidden' && s.cursor === 'pointer';
    })
    .map(el => {
      const r = el.getBoundingClientRect();
      return {t: (el.innerText || '').trim(),
              x: Math.round(r.x), y: Math.round(r.y),
              w: Math.round(r.width), h: Math.round(r.height),
              tag: el.tagName.toLowerCase(),
              cls: (el.className || '').toString().replace(/\s+/g, ' ').trim().slice(0, 70)};
    });
  cands.sort((a, b) => a.y - b.y);   // 取最上面那颗主 CTA
  return cands[0] || null;
}
"""


# ---------- 落地：日志与截图 ----------

def out_dir():
    """证据目录：默认 ``<仓库>/logs/doubao``，可用 ``DOUBAO_CLAIM_LOG_DIR`` 覆盖。

    ★ 可覆盖是为了**转发垫片**：免疫战争窗口的历史证据都在
      ``ImmuneWarPro/08-证据/豆包打卡``，垫片把环境变量指回去，那边照旧能看。
    """
    env = (os.environ.get("DOUBAO_CLAIM_LOG_DIR") or "").strip()
    if env:
        return os.path.abspath(os.path.expandvars(env))
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "logs", "doubao")


def _log(acc, claimed, action, note=""):
    out = out_dir()
    os.makedirs(out, exist_ok=True)
    log = os.path.join(out, "claim-log.tsv")
    new = not os.path.exists(log)
    with open(log, "a", encoding="utf-8") as f:
        if new:
            f.write("time\tacc\tclaimed\taction\tnote\n")
        f.write("\t".join([
            dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "-" if acc is None else str(acc),
            "1" if claimed else "0",
            action,
            str(note).replace("\t", " ").replace("\n", " "),
        ]) + "\n")
    return log


# ---------- CDP ----------

def _cdp_alive(cdp):
    try:
        import urllib.request
        with urllib.request.urlopen(cdp + "/json/version", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def _ensure_cdp(cdp, wait=40, launch=True):
    """CDP 没开就尝试带调试端口拉起豆包。返回 (ok, 说明)。"""
    if _cdp_alive(cdp):
        return True, "CDP 已在监听"
    if not launch:
        return False, "CDP 未开放且指定了不自动拉起：%s" % cdp
    if not os.path.exists(DOUBAO_EXE):
        return False, "豆包主程序不在预期路径：%s（可用 DOUBAO_EXE 覆盖）" % DOUBAO_EXE
    port = cdp.rsplit(":", 1)[-1]
    try:
        subprocess.Popen(
            [DOUBAO_EXE, "--remote-debugging-port=%s" % port,
             "--remote-allow-origins=*"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return False, "带调试端口拉起豆包失败：%r" % (e,)
    for _ in range(wait * 2):
        time.sleep(0.5)
        if _cdp_alive(cdp):
            time.sleep(2.0)      # 让主窗口把登录态加载完
            return True, "已带调试端口拉起豆包"
    return False, "拉起豆包后 %d 秒内 CDP 仍未就绪" % wait


def _playwright():
    """延迟 import：没装 playwright 不该拖垮整个签到台（其他站点还要能跑）。"""
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except Exception as e:
        raise RuntimeError("缺少 playwright（%r）—— 在签到台目录跑：pip install playwright "
                           "&& playwright install chromium" % (e,))


# ---------- 主流程 ----------

def run(dry_run=False, force=False, shot=True, cdp=None, launch=True, **_kw):
    """领取豆包今日免费额度。返回签到台统一契约（``base.make_result``）。"""
    cdp = (cdp or os.environ.get("DOUBAO_CDP") or CDP_DEFAULT).strip()
    out = out_dir()

    try:
        os.makedirs(out, exist_ok=True)
    except Exception as e:
        return base.make_result(SITE, base.ERROR,
                                "建不了证据目录：%s" % out,
                                detail=repr(e), exit_code=EXIT_CDP)

    osc = sys.stdout
    log_buf = []

    class _Tee(object):
        """把 print 同时收进 detail 缓冲（原脚本是 print 到控制台的，这里要留证）。"""

        def write(self, s):
            log_buf.append(s)
            try:
                osc.write(s)
            except Exception:
                pass
            return len(s)

        def flush(self):
            try:
                osc.flush()
            except Exception:
                pass

    try:
        ok, why = _ensure_cdp(cdp, launch=launch)
        if not ok:
            path = _log(None, False, "cdp-down", why)
            return base.make_result(SITE, base.FAILED, "连不上豆包（%s）" % why,
                                    detail=why, evidence=[path], exit_code=EXIT_CDP)

        try:
            sync_playwright = _playwright()
        except RuntimeError as e:
            path = _log(None, False, "no-playwright", str(e))
            return base.make_result(SITE, base.ERROR, "缺 playwright 依赖",
                                    detail=str(e), evidence=[path], exit_code=EXIT_CDP)

        sys.stdout = _Tee()
        try:
            return _claim(sync_playwright, cdp, out, dry_run, force, shot)
        finally:
            sys.stdout = osc
    except Exception as e:
        # ★ 绝不把异常抛给 hub：这里已经知道「是豆包站点出的事」，抛上去只剩 traceback
        path = _log(None, False, "exception", repr(e))
        return base.make_result(SITE, base.ERROR, "豆包签到异常：%s" % type(e).__name__,
                                detail=base.tail("".join(log_buf) + "\n" + repr(e)),
                                evidence=[path], exit_code=EXIT_CDP)


def _claim(sync_playwright, cdp, out, dry_run, force, shot):
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    shot_path = os.path.join(out, "claim-%s.png" % stamp)
    ev = []

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp)
        except Exception as e:
            path = _log(None, False, "cdp-down", repr(e))
            return base.make_result(SITE, base.FAILED, "连不上 CDP（%s）" % cdp,
                                    detail=repr(e), evidence=[path],
                                    exit_code=EXIT_CDP)

        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            try:
                page.goto(QUOTA_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                if shot:
                    _safe_shot(page, shot_path, ev)
                path = _log(None, False, "page-fail", repr(e))
                return base.make_result(SITE, base.FAILED, "签到页打不开",
                                        detail=repr(e), evidence=ev + [path],
                                        exit_code=EXIT_PAGE)
            page.wait_for_timeout(3500)

            if page.evaluate(JS_LOGGED_OUT):
                if shot:
                    _safe_shot(page, shot_path, ev)
                path = _log(None, False, "not-logged-in")
                return base.make_result(SITE, base.ERROR, "豆包未登录",
                                        detail="页面出现「登录」按钮 ⇒ 先在豆包里登录一次",
                                        evidence=ev + [path], exit_code=EXIT_LOGIN)

            acc0 = page.evaluate(JS_ACC)
            card = page.evaluate(JS_CARD)
            if not card.get("found"):
                if shot:
                    _safe_shot(page, shot_path, ev)
                path = _log(acc0, False, "card-not-found")
                return base.make_result(SITE, base.FAILED, "页面上找不到「每日签到」卡片",
                                        detail="活动可能改版或下线；先看截图再判断，不要盲目重试",
                                        evidence=ev + [path], exit_code=EXIT_CARD)

            print("· 累计额度 %s ｜ 今日已签：%s" % (acc0, "是" if card["claimed"] else "否"))
            print("· 卡片：%s" % str(card.get("cardText") or "")[:60])

            if card["claimed"] and not force:
                if shot:
                    _safe_shot(page, shot_path, ev)
                path = _log(acc0, True, "already")
                return base.make_result(SITE, base.ALREADY,
                                        "今天早已领过（累计 %s）" % acc0,
                                        evidence=ev + [path], exit_code=EXIT_OK)

            if dry_run:
                if shot:
                    _safe_shot(page, shot_path, ev)
                path = _log(acc0, card["claimed"], "dry-run")
                return base.make_result(SITE, base.SKIPPED,
                                        "--dry-run：未点击（累计 %s）" % acc0,
                                        detail=json.dumps(card.get("clickables") or [],
                                                          ensure_ascii=False),
                                        evidence=ev + [path], exit_code=EXIT_OK)

            # 挑点击目标：优先文字含「领取/签到」的，否则取最右侧那个；
            # 卡片里一个都没有时，退到页面顶部的「立即领取」主 CTA。
            cands = card.get("clickables") or []
            target = None
            for c in cands:
                if re.search(r"领取|签到", c.get("t") or ""):
                    target = c
                    break
            if target is None and cands:
                target = max(cands, key=lambda c: c.get("x") or 0)

            if target is None:
                target = page.evaluate(JS_CTA)
                if target:
                    print("· 卡片内没控件，改用顶部主 CTA：%r" % (target.get("t"),))

            if target is None:
                if shot:
                    _safe_shot(page, shot_path, ev)
                path = _log(acc0, card["claimed"], "no-button")
                return base.make_result(SITE, base.FAILED,
                                        "找不到可点的签到控件",
                                        detail="卡片内与顶部 CTA 都没命中；先看截图",
                                        evidence=ev + [path], exit_code=EXIT_CARD)

            print("· 点击：%r @ (%s,%s) %s"
                  % (target.get("t"), target.get("x"), target.get("y"),
                     target.get("tag")))
            page.mouse.click(target["x"] + target["w"] // 2,
                             target["y"] + target["h"] // 2)
            page.wait_for_timeout(3500)

            acc1 = page.evaluate(JS_ACC)
            card1 = page.evaluate(JS_CARD)
            claimed1 = bool(card1.get("claimed"))
            delta = (acc1 - acc0) if (acc0 is not None and acc1 is not None) else None

            if shot:
                _safe_shot(page, shot_path, ev)

            if claimed1 or (delta is not None and delta > 0):
                path = _log(acc1, True, "claimed", "+%s" % delta)
                return base.make_result(SITE, base.CLAIMED,
                                        "新领到 +%s（累计 %s → %s）" % (delta, acc0, acc1),
                                        evidence=ev + [path], exit_code=EXIT_OK)

            path = _log(acc1, claimed1, "no-increase",
                        "%s -> %s" % (acc0, acc1))
            return base.make_result(SITE, base.FAILED,
                                    "点了但额度没涨（%s → %s）" % (acc0, acc1),
                                    detail="可能被风控或活动规则变了；先看截图",
                                    evidence=ev + [path], exit_code=EXIT_NOCHANGE)
        finally:
            try:
                page.close()
            except Exception:
                pass


def _safe_shot(page, path, ev):
    try:
        page.screenshot(path=path)
        ev.append(path)
    except Exception:
        pass


def doctor():
    """离线体检：能不能跑（不看网络、不启动豆包）。"""
    info = {
        "site": SITE,
        "doubao_exe": DOUBAO_EXE,
        "doubao_exe_exists": os.path.exists(DOUBAO_EXE),
        "cdp": os.environ.get("DOUBAO_CDP") or CDP_DEFAULT,
        "cdp_alive": _cdp_alive(os.environ.get("DOUBAO_CDP") or CDP_DEFAULT),
        "out_dir": out_dir(),
    }
    try:
        _playwright()
        info["playwright"] = True
    except RuntimeError as e:
        info["playwright"] = False
        info["playwright_why"] = str(e)
    return info
