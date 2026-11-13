"""签到台站点契约 —— 一个站点 = 一个 ``run()``，返回统一的 dict。

为什么要统一契约
----------------
签到台要能装下「WorkBuddy 接口签到」「豆包桌面端 CDP 领取」，以后还会有
「某个游戏每日签到」「某个网站打卡」……它们的**做法**完全不同
（HTTP 直调 / 浏览器自动化 / 点图 / 模拟按键），但**上层要看的东西是同一件**：
今天领到没有、领到多少、有没有需要人处理的事。

所以这里只固定**输出**，不固定**实现**：
  · provider 自己决定怎么登、怎么点、怎么判成功；
  · 但**必须**把结果折成同一张表，``signin_hub.py`` 才能一次跑完、一次汇报。

契约（★ 违反它 = 上层汇总就会错）
---------------------------------
``run(dry_run=False) -> dict``，必须含这些键：

===============  =====  ==============================================
键               类型   含义
===============  =====  ==============================================
``site``         str    站点 id，与注册表键一致
``ok``           bool   今天这件事算不算完成（**「早已领过」= True**，不是失败）
``state``        str    claimed / already / skipped / failed / error
``report``       str    一句话人话（给人看的，不含 traceback）
``detail``       str    诊断原文（排错用，可以长）
``evidence``     list   截图 / 日志的绝对路径（有就给，方便复查）
``exit``         int    与命令行退出码一致（垫片脚本据此透传）
``needs_attention`` bool 是否要人来处理
===============  =====  ==============================================

★ provider **不许**把异常抛给上层。hub 会兜住，但兜住的信息只剩 traceback，
  没有上下文。宁可自己 ``try`` 完再返回 ``state=failed`` 并写清原因。

★ 「今天已经领过了」必须算 **ok=True / state=already**。把它当失败，会让
  汇总每天都报红，人就开始无视告警——那才是真的失去监控。
"""

from __future__ import annotations

# 状态取值（全仓统一，别在 provider 里自造）
CLAIMED = "claimed"      # 今天新领到
ALREADY = "already"      # 今天早就领过（幂等，算成功）
SKIPPED = "skipped"      # 主动跳过（例如 --dry-run、站点被禁用、条件不满足）
FAILED = "failed"        # 试过了，没成（CDP 连不上 / 找不到按钮 / 点了没涨）
ERROR = "error"          # 没跑起来（缺依赖 / 配置错 / 未登录）

OK_STATES = (CLAIMED, ALREADY, SKIPPED)

# 各 state 的中文短标（汇总表用，避免每个 provider 自己写一套）
LABELS = {
    CLAIMED: "新领到",
    ALREADY: "早已领过",
    SKIPPED: "跳过",
    FAILED: "没成",
    ERROR: "出错",
}


def make_result(site, state, report, *, detail="", evidence=None,
                exit_code=None, needs_attention=None):
    """按契约造一个结果 dict。

    ★ ``ok`` / ``exit`` / ``needs_attention`` 由 ``state`` 派生，不让调用方各写一遍——
      三处口径一旦不同步，就会出现「表里写成功、退出码却非 0」这种自相矛盾的汇报。
    """
    site = str(site or "").strip()
    state = str(state or "").strip().lower()
    if state not in LABELS:
        # 未知状态一律按 error，不要"猜它是好的"
        detail = ("未知 state=%r（已按 error 处理）\n%s" % (state, detail)).strip()
        state = ERROR
    ok = state in OK_STATES
    if exit_code is None:
        exit_code = 0 if ok else (3 if state == FAILED else 2)
    if needs_attention is None:
        needs_attention = not ok
    return {
        "site": site,
        "ok": bool(ok),
        "state": state,
        "label": LABELS[state],
        "report": str(report or "").strip() or LABELS[state],
        "detail": str(detail or "").strip(),
        "evidence": [str(p) for p in (evidence or []) if p],
        "exit": int(exit_code),
        "needs_attention": bool(needs_attention),
    }


def tail(text, limit=1200):
    """取原文尾部（诊断信息最有用的是最后几行；全文可能几 MB）。"""
    s = str(text or "")
    if len(s) <= limit:
        return s
    return "…（前 %d 字省略）…\n%s" % (len(s) - limit, s[-limit:])


def summarize(results):
    """把多个站点结果折成一句人话 + 总状态。

    返回 ``(result, report, needs_attention)``：
      ``result`` = OK（全成）/ PARTIAL（有成有败）/ ERROR（全败）
    """
    rows = [r for r in (results or []) if isinstance(r, dict)]
    if not rows:
        return "ERROR", "没有任何站点结果（站点列表是空的？）", True
    good = [r for r in rows if r.get("ok")]
    bad = [r for r in rows if not r.get("ok")]
    parts = []
    for r in rows:
        parts.append("%s：%s%s" % (r.get("site") or "?",
                                  (r.get("report") or "").strip(),
                                  "" if r.get("ok") else "（需处理）"))
    if not bad:
        return "OK", "；".join(parts), False
    if not good:
        return "ERROR", "；".join(parts), True
    return "PARTIAL", "；".join(parts), True
