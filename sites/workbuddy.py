"""站点：WorkBuddy 桌面端签到（转调既有的 ``signin.py``）。

为什么不直接把 ``signin.py`` import 进来
---------------------------------------
``signin.py`` 是一个**成品脚本**：它自己解析 ``sys.argv``、自己 ``sys.exit``、
自己决定往 stdout 还是日志文件写。import 进来会让它的全局预算状态、日志开关
和签到台的进程状态互相污染；而它是上游（88lin）维护、有 1700+ 行、已被实跑验证的代码，
**不该为了统一入口去改它**。

⇒ 这里用**子进程**调用，只解析它的单行 JSON 输出。好处：
  · 它崩了只死一个子进程，签到台其他站点照跑；
  · 它的时间预算、凭据探测、加密凭据解密全部原样生效；
  · 上游更新时直接覆盖 ``signin.py`` 即可，本文件不用动。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from . import base

SITE = "workbuddy"
TITLE = "WorkBuddy 桌面端（每日积分 + 成长中心）"
NEEDS = "本机已登录 WorkBuddy 桌面端（凭据由客户端自动写入）"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNIN = os.path.join(REPO, "signin.py")

# signin.py 的结果 → 签到台状态
# ★ INACTIVE = 今天没有可领的活动（不是失败）；GROWTH = 只要到成长中心的东西。
_STATE = {
    "CLAIMED": base.CLAIMED,
    "ALREADY": base.ALREADY,
    "INACTIVE": base.SKIPPED,
    "GROWTH": base.ALREADY,
}


def _interpreter():
    """用**带控制台**的 python.exe 起子进程。

    ★ 若签到台自己被 pythonw.exe 拉起（计划任务静默模式），``sys.executable``
      就是 pythonw —— 子进程虽然仍能写管道，但一旦它内部再起什么东西、或要报错，
      会安静得毫无痕迹。找同目录的 python.exe 更稳。
    """
    exe = sys.executable or "python"
    if os.path.basename(exe).lower().startswith("pythonw"):
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(cand):
            return cand
    return exe


def _last_json(text):
    """从 stdout 里取**最后一行能解析成 dict 的 JSON**。

    ★ signin.py 的汇报是单行 JSON，但运行中也可能有别的输出；从后往前找，
      找到第一个能解析的就是结果（它的输出顺序保证结果在最后）。
    """
    for line in reversed([l for l in str(text or "").splitlines() if l.strip()]):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def run(dry_run=False, action=None, timeout=660, **_kw):
    """跑一次 WorkBuddy 签到。返回签到台统一契约。

    ``dry_run=True`` ⇒ 只查状态（``status``），不领。
    ``timeout`` 默认 660 秒：signin.py 自己的预算上限是 540 秒，留 120 秒给启动与收尾。
    """
    if not os.path.exists(SIGNIN):
        return base.make_result(SITE, base.ERROR,
                                "找不到 signin.py（%s）" % SIGNIN,
                                exit_code=2)

    act = action or ("status" if dry_run else "auto")
    exe = _interpreter()
    env = dict(os.environ)
    # 子进程别把 control 字符/emoji 打成编码错（Windows 默认 cp936 会在个别字符上炸）
    env.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        proc = subprocess.run([exe, SIGNIN, act], cwd=REPO, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return base.make_result(SITE, base.FAILED,
                                "跑了 %d 秒还没结束，被判超时" % timeout,
                                detail="signin.py 的时间预算上限是 540 秒，"
                                       "超时说明网络或服务端卡住了",
                                exit_code=3)
    except Exception as e:
        return base.make_result(SITE, base.ERROR,
                                "起不来 signin.py 子进程",
                                detail=repr(e), exit_code=2)

    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    obj = _last_json(out) or {}

    res = str(obj.get("result") or "").upper()
    report = str(obj.get("report") or "").strip()
    detail = base.tail("\n".join(x for x in (
        ("action: %s" % act),
        ("stdout:\n" + out.strip()) if out.strip() else "",
        ("stderr:\n" + err.strip()) if err.strip() else "",
    ) if x))

    if not obj:
        # 没拿到 JSON ⇒ 子进程没走到汇报那一步（崩溃 / 参数错 / 被强杀）
        return base.make_result(SITE, base.ERROR,
                                "signin.py 没给出结果（退出码 %s）" % proc.returncode,
                                detail=detail, exit_code=2)

    if act == "status":
        # status 输出的是原始接口返回，没有 result 字段
        http = obj.get("http")
        ok = isinstance(http, int) and 200 <= http < 300
        return base.make_result(
            SITE, base.SKIPPED if ok else base.FAILED,
            ("只查状态：HTTP %s（--dry-run，未领取）" % http) if ok
            else ("查状态失败：HTTP %s" % http),
            detail=detail, exit_code=0 if ok else 1)

    state = _STATE.get(res)
    if state is None:
        # ERROR / NETWORK / TIMEOUT / UNKNOWN → failed；凭据类 → error（要人去登录）
        if res in ("NO_AUTH", "NO_SESSION", "AUTH_ERROR", "AUTH_REJECTED",
                   "FORBIDDEN"):
            state = base.ERROR
        elif res:
            state = base.FAILED
        else:
            state = base.ERROR if proc.returncode else base.FAILED

    return base.make_result(
        SITE, state,
        report or ("signin.py 返回 %s" % (res or "（空）")),
        detail=detail, exit_code=proc.returncode if not report else (
            0 if state in base.OK_STATES else proc.returncode or 3))


def doctor():
    info = {
        "site": SITE,
        "signin_py": SIGNIN,
        "signin_py_exists": os.path.exists(SIGNIN),
        "interpreter": _interpreter(),
    }
    if info["signin_py_exists"]:
        # 只跑离线体检，不联网、不解密
        try:
            proc = subprocess.run([info["interpreter"], SIGNIN, "doctor"],
                                  cwd=REPO, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=120)
            info["doctor_json"] = _last_json((proc.stdout or b"").decode("utf-8", "replace"))
            info["doctor_exit"] = proc.returncode
        except Exception as e:
            info["doctor_error"] = repr(e)
    return info
