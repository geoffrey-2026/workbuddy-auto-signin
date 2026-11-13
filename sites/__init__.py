"""签到台站点注册表 —— 新增一个站点只要两步。

1. 在 ``sites/`` 下写一个模块，提供 ``run(dry_run=False) -> dict``（契约见 ``base.py``），
   同时给出 ``SITE`` / ``TITLE`` / ``NEEDS`` 三个常量；
2. 在下面的 ``_REGISTRY`` 里加一行。

★ 不做"扫描目录自动发现"：注册是显式的，跑错站点的代价（比如点了不该点的按钮）
  远大于省下这一行。而且 ``list`` 要能在没装任何依赖的机器上正常工作。

★ 执行顺序 = 注册顺序。**把「需要外部程序先起来」的站点放后面**，
  让纯 HTTP 的站点先跑完，别被前面的 GUI 等待拖住（见 ``ORDER``）。
"""

from __future__ import annotations

from . import base
from . import doubao
from . import workbuddy

# 站点 id → 模块（模块自带 SITE / TITLE / NEEDS）
_REGISTRY = {
    "workbuddy": workbuddy,
    "doubao": doubao,
}

# `run all` 的执行顺序（★ 与注册顺序分开：注册顺序是给人读的，执行顺序是按代价排的）
ORDER = ["workbuddy", "doubao"]


def names():
    """所有站点 id（按注册顺序）。"""
    return list(_REGISTRY.keys())


def ordered(only=None, skip=None):
    """要跑的站点列表：``only`` 优先，其次 ``ORDER``；``skip`` 里的一律剔除。

    ★ 未注册的名字**不静默忽略** —— 由调用方报错，否则「我明明写了 --only foo」
      会变成"跑了一堆不相干的"，而人以为只跑了一个。
    """
    pool = [n for n in (only or ORDER) if n in _REGISTRY]
    out = []
    for n in pool:
        if n in (skip or []):
            continue
        out.append(n)
    return out


def unknown(only=None, skip=None):
    """``only`` / ``skip`` 里没注册过的名字（给调用方报错用）。"""
    out = []
    for n in list(only or []) + list(skip or []):
        if n not in _REGISTRY:
            out.append(n)
    return out


def get(name):
    return _REGISTRY.get(name)


def info(name):
    mod = _REGISTRY.get(name)
    if mod is None:
        return None
    d = {
        "site": getattr(mod, "SITE", name),
        "title": getattr(mod, "TITLE", ""),
        "needs": getattr(mod, "NEEDS", ""),
        "module": mod.__name__,
    }
    doc = getattr(mod, "doctor", None)
    if callable(doc):
        try:
            d["doctor"] = doc()
        except Exception as e:
            d["doctor"] = {"error": repr(e)}
    return d


def run_site(name, dry_run=False, **kw):
    """跑一个站点，**保证**返回契约 dict（provider 抛异常也兜成 error）。"""
    mod = _REGISTRY.get(name)
    if mod is None:
        return base.make_result(name, base.ERROR,
                                "没注册过这个站点：%s（已注册：%s）"
                                % (name, "、".join(names())), exit_code=2)
    fn = getattr(mod, "run", None)
    if not callable(fn):
        return base.make_result(name, base.ERROR,
                                "站点模块没有 run()：%s" % mod.__name__, exit_code=2)
    try:
        res = fn(dry_run=dry_run, **kw)
    except Exception as e:
        return base.make_result(
            name, base.ERROR,
            "%s 站点抛异常：%s" % (name, type(e).__name__),
            detail=base.tail(repr(e)), exit_code=2)
    if not isinstance(res, dict):
        return base.make_result(name, base.ERROR,
                                "%s 站点返回了 %r，不是契约 dict"
                                % (name, type(res).__name__), exit_code=2)
    res.setdefault("site", name)
    res.setdefault("evidence", [])
    res.setdefault("detail", "")
    return res
