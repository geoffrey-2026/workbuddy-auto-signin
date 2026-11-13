"""签到台 —— 一个入口，管所有「每天要领一下」的事。

为什么要有它
------------
签到这件事之前散在各处：WorkBuddy 自己有 ``signin.py``，豆包有一份
``ImmuneWarPro/tools/美术生成/doubao_daily_claim.py``，再往后还有游戏、网页、
别的 agent……每来一个就多一条计划任务、多一个日志位置、多一种"成功"的定义。
出问题时人得挨个去翻，还分不清「今天没领」和「领失败了」。

⇒ 收成一个台子：**一个列表、一次运行、一份汇总**。
   每个站点只管"怎么领"，领完统一折成一张表（见 ``sites/base.py`` 的契约）。

用法
----
    py signin_hub.py list                 # 有哪些站点、各自要什么前置条件
    py signin_hub.py run all              # ★ 日常就这一条
    py signin_hub.py run doubao           # 只跑一个
    py signin_hub.py run all --dry-run    # 只看状态，不点、不领
    py signin_hub.py run all --skip doubao
    py signin_hub.py doctor               # 离线体检（不联网、不启动 GUI 程序）

选项
----
    --dry-run       只查不领（各站点自己实现"只查"的分支）
    --json          只输出一行 JSON（给自动化/日志用，不打印过程）
    --quiet         不打印各站点的过程输出（豆包的 playblast 式打印会被吞掉）
    --skip a,b      本次跳过这些站点
    --log <path>    额外把结果 JSON 追加到该文件（无窗口运行时用）

退出码
------
    0  全部成功（含"早已领过"）
    1  部分失败（至少一个成功）
    2  全失败 / 用法错 / 没有站点

★ 「早已领过」算成功。把它当失败，汇总每天报红，人就会开始无视告警——
  那比不监控更糟。
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import sites                                    # noqa: E402
from sites import base                          # noqa: E402

DEFAULT_LOG = os.path.join(HERE, "logs", "hub.log")


def _parse(argv):
    ap = argparse.ArgumentParser(add_help=True, description="签到台")
    ap.add_argument("cmd", nargs="?", default="",
                    help="list / run / doctor")
    ap.add_argument("target", nargs="?", default="all",
                    help="run 的目标：all 或逗号分隔的站点 id")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", dest="as_json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--skip", default="")
    ap.add_argument("--only", default="")
    ap.add_argument("--log", default="")
    ap.add_argument("--timeout", type=int, default=0,
                    help="单站点超时秒数（0 = 用站点默认）")
    return ap.parse_args(argv)


def _split(s):
    return [x.strip() for x in str(s or "").split(",") if x.strip()]


def _emit(payload, as_json=False, log_path=""):
    """输出结果：人话 + （可选）单行 JSON；并可按需追加到文件。

    ★ 文件写入失败**不许**影响退出码与 stdout —— 日志只是旁路。
    """
    if as_json:
        line = json.dumps(payload, ensure_ascii=False, default=str)
        print(line)
    if log_path:
        try:
            d = os.path.dirname(os.path.abspath(log_path))
            if d and not os.path.isdir(d):
                os.makedirs(d)
            with io.open(log_path, "a", encoding="utf-8", newline="\n") as f:
                f.write("[%s] %s\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                       json.dumps(payload, ensure_ascii=False, default=str)))
        except Exception:
            pass


def _print_table(results):
    print("")
    print("%-12s %-8s %s" % ("站点", "结果", "说明"))
    print("-" * 78)
    for r in results:
        print("%-12s %-8s %s" % (r.get("site") or "?", r.get("label") or "",
                                 (r.get("report") or "").replace("\n", " ")))
    print("-" * 78)


def cmd_list(args):
    rows = []
    for n in sites.names():
        d = sites.info(n)
        rows.append((n, d.get("title") or "", d.get("needs") or ""))
    if args.as_json:
        print(json.dumps({"sites": [{"id": a, "title": b, "needs": c}
                                    for a, b, c in rows]}, ensure_ascii=False))
        return 0
    print("签到台 —— 已注册 %d 个站点（按执行顺序：%s）"
          % (len(rows), " → ".join(sites.ORDER)))
    print("")
    for n, title, needs in rows:
        print("· %-11s %s" % (n, title))
        if needs:
            print("  %-11s 前置：%s" % ("", needs))
    print("")
    print("跑全部：py signin_hub.py run all")
    return 0


def cmd_doctor(args):
    out = []
    for n in sites.names():
        d = sites.info(n) or {}
        out.append(d)
    if args.as_json:
        print(json.dumps({"doctor": out}, ensure_ascii=False, default=str))
        return 0
    print("签到台体检（离线：不联网、不启动外部程序）")
    print("")
    for d in out:
        print("· %s  %s" % (d.get("site"), d.get("title") or ""))
        for k, v in (d.get("doctor") or {}).items():
            if isinstance(v, dict):
                print("    %-16s %s" % (k, json.dumps(v, ensure_ascii=False, default=str)[:200]))
            else:
                print("    %-16s %s" % (k, v))
        print("")
    return 0


def cmd_run(args):
    target = (args.target or "all").strip()
    only = _split(args.only)
    if target and target != "all":
        only = _split(target) + only
    skip = _split(args.skip)

    bad = sites.unknown(only=only, skip=skip)
    if bad:
        msg = "没注册过这些站点：%s（已注册：%s）" % ("、".join(bad), "、".join(sites.names()))
        if args.as_json:
            print(json.dumps({"result": "ERROR", "report": msg}, ensure_ascii=False))
        else:
            print("× " + msg)
        return 2

    todo = sites.ordered(only=only or None, skip=skip)
    if not todo:
        msg = "没有要跑的站点（only=%r skip=%r）" % (only, skip)
        if args.as_json:
            print(json.dumps({"result": "ERROR", "report": msg}, ensure_ascii=False))
        else:
            print("× " + msg)
        return 2

    if not args.as_json and not args.quiet:
        print("签到台：准备跑 %d 个站点 —— %s%s"
              % (len(todo), "、".join(todo), "（--dry-run）" if args.dry_run else ""))

    results = []
    for n in todo:
        if not args.as_json and not args.quiet:
            print("\n── %s ──" % n)
        kw = {}
        if args.timeout and args.timeout > 0:
            kw["timeout"] = args.timeout
        # ★ quiet 模式把站点的过程输出吞掉（豆包会打一堆"· 点击…"），
        #   但异常仍要能被兜住 —— 这里只重定向 stdout，stderr 保留。
        old = sys.stdout
        if args.quiet:
            try:
                sys.stdout = io.StringIO()
            except Exception:
                pass
        try:
            res = sites.run_site(n, dry_run=args.dry_run, **kw)
        finally:
            if args.quiet:
                sys.stdout = old
        results.append(res)
        if not args.as_json and not args.quiet:
            print("→ %s：%s" % (res.get("label"), res.get("report")))

    result, report, need = base.summarize(results)
    payload = {
        "result": result,
        "report": report,
        "needs_attention": bool(need),
        "dry_run": bool(args.dry_run),
        "when": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sites": results,
    }
    if args.as_json:
        _emit(payload, as_json=True, log_path=args.log)
    else:
        if not args.quiet:
            _print_table(results)
        print("%s：%s" % ({"OK": "√ 全部完成", "PARTIAL": "★ 部分没成",
                           "ERROR": "× 全都没成"}[result], report))
        if need:
            print("需要处理：")
            for r in results:
                if not r.get("ok"):
                    print("  · %s：%s" % (r.get("site"), r.get("report")))
                    if r.get("evidence"):
                        print("    证据：%s" % "；".join(r["evidence"][:3]))
        _emit(payload, as_json=False, log_path=args.log)

    return 0 if result == "OK" else (1 if result == "PARTIAL" else 2)


USAGE = __doc__


def main(argv=None):
    args = _parse(list(sys.argv[1:] if argv is None else argv))
    cmd = (args.cmd or "").strip().lower()
    if cmd in ("", "-h", "--help", "help"):
        print(USAGE)
        return 0
    try:
        if cmd == "list":
            return cmd_list(args)
        if cmd == "doctor":
            return cmd_doctor(args)
        if cmd == "run":
            return cmd_run(args)
        print("未知命令：%s（可用：list / run / doctor）" % cmd)
        return 2
    except KeyboardInterrupt:
        print("\n（被中断）")
        return 130
    except Exception as e:
        # ★ 无窗口运行时，任何未捕获异常都会让当天的失败无痕消失
        payload = {"result": "ERROR",
                   "report": "签到台异常（%s）" % type(e).__name__,
                   "needs_attention": True,
                   "detail": base.tail(repr(e))}
        _emit(payload, as_json=args.as_json, log_path=args.log or DEFAULT_LOG)
        return 2


if __name__ == "__main__":
    sys.exit(main())
