"""签到台（sites/ + signin_hub.py）的纯逻辑测试。

★ 只测**不需要网络、不需要 GUI、不需要 playwright** 的部分。
  需要真的去点豆包的那部分，只能在有豆包桌面版的机器上实测（见 README「签到台」节）。

★ 为什么这些"琐碎"断言值得写：签到台的错法不是崩溃，而是**悄悄报成成功**——
  「早就领过」被算成失败 → 每天报红 → 人开始无视告警；
  「没注册的站点」被静默忽略 → 以为跑了一个其实跑了五个。
  这两类错都不会抛异常，只能靠断言钉住。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import signin_hub
import sites
from sites import base, doubao, workbuddy


class TestResultContract(unittest.TestCase):
    def test_ok_states_derive_ok_and_exit_zero(self):
        for st in (base.CLAIMED, base.ALREADY, base.SKIPPED):
            r = base.make_result("x", st, "说明")
            self.assertTrue(r["ok"], st)
            self.assertEqual(r["exit"], 0, st)
            self.assertFalse(r["needs_attention"], st)

    def test_already_checked_in_is_success_not_failure(self):
        # ★ 这条是整个签到台的价值观：已领过 = 完成
        r = base.make_result("doubao", base.ALREADY, "今天早已领过")
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "already")
        self.assertEqual(r["label"], "早已领过")

    def test_failed_and_error_are_not_ok(self):
        f = base.make_result("x", base.FAILED, "没成")
        e = base.make_result("x", base.ERROR, "起不来")
        self.assertFalse(f["ok"])
        self.assertFalse(e["ok"])
        self.assertTrue(f["needs_attention"])
        self.assertTrue(e["needs_attention"])
        self.assertEqual(f["exit"], 3)
        self.assertEqual(e["exit"], 2)

    def test_unknown_state_degrades_to_error(self):
        # provider 写错状态名时，绝不能"猜它是好的"
        r = base.make_result("x", "claimed!", "随便写的")
        self.assertEqual(r["state"], base.ERROR)
        self.assertFalse(r["ok"])
        self.assertIn("未知 state", r["detail"])

    def test_evidence_strings_are_dropped_when_empty(self):
        r = base.make_result("x", base.CLAIMED, "ok", evidence=["", None, "/a.png"])
        self.assertEqual(r["evidence"], ["/a.png"])

    def test_explicit_needs_attention_is_respected(self):
        r = base.make_result("x", base.ALREADY, "已领", needs_attention=True)
        self.assertTrue(r["ok"])
        self.assertTrue(r["needs_attention"])


class TestSummarize(unittest.TestCase):
    def _r(self, site, state):
        return base.make_result(site, state, "%s 说明" % site)

    def test_all_good(self):
        result, report, need = base.summarize([self._r("a", base.CLAIMED),
                                               self._r("b", base.ALREADY)])
        self.assertEqual(result, "OK")
        self.assertFalse(need)
        self.assertIn("a", report)

    def test_partial(self):
        result, report, need = base.summarize([self._r("a", base.CLAIMED),
                                               self._r("b", base.FAILED)])
        self.assertEqual(result, "PARTIAL")
        self.assertTrue(need)
        self.assertIn("需处理", report)

    def test_all_bad(self):
        result, _, need = base.summarize([self._r("a", base.FAILED)])
        self.assertEqual(result, "ERROR")
        self.assertTrue(need)

    def test_empty_is_error_not_ok(self):
        # 站点列表空了必须报错：空列表若算成功，等于"什么都没跑也叫完成"
        result, report, need = base.summarize([])
        self.assertEqual(result, "ERROR")
        self.assertTrue(need)


class TestRegistry(unittest.TestCase):
    def test_known_sites(self):
        self.assertIn("workbuddy", sites.names())
        self.assertIn("doubao", sites.names())

    def test_ordered_follows_order_and_respects_skip(self):
        todo = sites.ordered()
        self.assertEqual(todo, [n for n in sites.ORDER if n in sites.names()])
        self.assertNotIn("doubao", sites.ordered(skip=["doubao"]))

    def test_unknown_names_are_reported_not_ignored(self):
        # ★ 写错站点名必须报错，不能静默跑别的
        self.assertEqual(sites.unknown(only=["doubao", "foo"]), ["foo"])
        self.assertEqual(sites.unknown(skip=["bar"]), ["bar"])

    def test_unregistered_site_returns_error_result(self):
        r = sites.run_site("nope")
        self.assertFalse(r["ok"])
        self.assertEqual(r["state"], base.ERROR)

    def test_provider_exception_is_caught(self):
        class Boom(object):
            SITE = "boom"

            @staticmethod
            def run(dry_run=False, **kw):
                raise RuntimeError("炸了")

        sites._REGISTRY["_boom"] = Boom
        try:
            r = sites.run_site("_boom")
            self.assertEqual(r["state"], base.ERROR)
            self.assertIn("RuntimeError", r["report"])
        finally:
            sites._REGISTRY.pop("_boom", None)

    def test_non_dict_return_is_error(self):
        class Weird(object):
            SITE = "weird"

            @staticmethod
            def run(dry_run=False, **kw):
                return "ok"

        sites._REGISTRY["_weird"] = Weird
        try:
            self.assertEqual(sites.run_site("_weird")["state"], base.ERROR)
        finally:
            sites._REGISTRY.pop("_weird", None)


class TestWorkbuddyParsing(unittest.TestCase):
    def test_last_json_picks_final_line(self):
        text = "· 一些过程输出\n{\"result\": \"ALREADY\"}\n尾巴不是 JSON\n"
        self.assertEqual(workbuddy._last_json(text), {"result": "ALREADY"})

    def test_last_json_ignores_broken_lines(self):
        text = '{不是合法 json\n{"result": "CLAIMED"}\n'
        self.assertEqual(workbuddy._last_json(text), {"result": "CLAIMED"})

    def test_last_json_returns_none_when_absent(self):
        self.assertIsNone(workbuddy._last_json("什么都没有"))
        self.assertIsNone(workbuddy._last_json(""))

    def test_result_state_mapping(self):
        self.assertEqual(workbuddy._STATE["CLAIMED"], base.CLAIMED)
        self.assertEqual(workbuddy._STATE["ALREADY"], base.ALREADY)
        # 今天没有活动可领，不是失败
        self.assertEqual(workbuddy._STATE["INACTIVE"], base.SKIPPED)


class TestDoubaoModule(unittest.TestCase):
    def test_exit_codes_match_original_contract(self):
        # ★ 这几个数字是原脚本与免疫战争窗口自动化里写死的，改了就破坏兼容
        self.assertEqual(doubao.EXIT_OK, 0)
        self.assertEqual(doubao.EXIT_CDP, 2)
        self.assertEqual(doubao.EXIT_PAGE, 3)
        self.assertEqual(doubao.EXIT_CARD, 4)
        self.assertEqual(doubao.EXIT_NOCHANGE, 5)
        self.assertEqual(doubao.EXIT_LOGIN, 6)

    def test_probes_exist_and_are_js_functions(self):
        for name in ("JS_LOGGED_OUT", "JS_ACC", "JS_CARD", "JS_CTA"):
            js = getattr(doubao, name)
            self.assertTrue(js.strip().startswith("()"), name)
            self.assertIn("return", js, name)

    def test_card_probe_walks_up_to_card_level(self):
        # ★ 这条是实测踩出来的坑：只在内层容器里找 rewarded，claimed 永远是 False
        self.assertIn("rewarded", doubao.JS_CARD)
        self.assertIn("450", doubao.JS_CARD)

    def test_playwright_is_lazy(self):
        # 没装 playwright 也不能让 list / doctor 崩
        import inspect
        src = inspect.getsource(doubao)
        head = src.split("def _playwright", 1)[0]
        self.assertNotIn("\nfrom playwright", head.replace("from playwright.sync_api import sync_playwright\n        return", ""))

    def test_out_dir_env_override(self):
        old = os.environ.get("DOUBAO_CLAIM_LOG_DIR")
        try:
            os.environ["DOUBAO_CLAIM_LOG_DIR"] = r"D:\tmp\claim"
            self.assertEqual(doubao.out_dir(), os.path.abspath(r"D:\tmp\claim"))
            os.environ.pop("DOUBAO_CLAIM_LOG_DIR", None)
            self.assertTrue(doubao.out_dir().endswith(os.path.join("logs", "doubao")))
        finally:
            if old is None:
                os.environ.pop("DOUBAO_CLAIM_LOG_DIR", None)
            else:
                os.environ["DOUBAO_CLAIM_LOG_DIR"] = old


class _StubPage(object):
    """假页面：只实现 ``_wait_card`` / 归因函数用到的那几个方法（不连网、不真睡）。

    ``*_from_round`` 的语义：第 N 轮（0 起）之后，该探针才开始「有值」，
    用来复现「页面慢慢渲染」与「一直停在骨架屏」。
    """

    def __init__(self, card_from_round=None, textlen=0, textlen_from_round=None,
                 logged_out_from_round=None):
        self.round = 0
        self.card_from_round = card_from_round
        self.textlen = textlen
        self.textlen_from_round = textlen_from_round
        self.logged_out_from_round = logged_out_from_round
        self.slept = 0
        self.probes = []

    def evaluate(self, js):
        self.probes.append(js)
        if js == doubao.JS_CARD:
            if self.card_from_round is not None and self.round >= self.card_from_round:
                return {"found": True, "claimed": False,
                        "cardText": "每日签到 +400 额度 立即签到",
                        "clickables": [{"t": "立即签到", "x": 704, "y": 429,
                                        "w": 80, "h": 32}]}
            return {"found": False}
        if js == doubao.JS_TEXTLEN:
            if (self.textlen_from_round is not None
                    and self.round >= self.textlen_from_round):
                return self.textlen
            return 0
        if js == doubao.JS_LOGGED_OUT:
            return bool(self.logged_out_from_round is not None
                        and self.round >= self.logged_out_from_round)
        raise AssertionError("意外的探针：%r" % (js,))

    def wait_for_timeout(self, ms):
        self.slept += 1
        self.round += 1


class TestDoubaoCardWait(unittest.TestCase):
    """★ 2026-11-16 血案：固定等 3.5s 时页面还是骨架屏，却被报成「活动改版或下线」。

    这一组测的就是「等得够 + 归因对」。它们全是离线纯逻辑，不需要豆包、不需要 playwright。
    """

    def test_polls_until_card_renders_instead_of_fixed_sleep(self):
        # 第 3 轮才渲染出来 ⇒ 必须一直等到它出现，而不是查一次就判死
        page = _StubPage(card_from_round=3, textlen=1400, textlen_from_round=2)
        card, info = doubao._wait_card(page, budget=20.0, step=0.5)
        self.assertTrue(card["found"])
        self.assertGreaterEqual(info["samples"], 4)
        self.assertEqual(page.slept, 3)

    def test_found_immediately_costs_no_sleep(self):
        page = _StubPage(card_from_round=0, textlen=1327)
        card, info = doubao._wait_card(page, budget=20.0, step=0.5)
        self.assertTrue(card["found"])
        self.assertEqual(page.slept, 0)
        self.assertEqual(info["samples"], 1)

    def test_gives_up_after_budget_and_reports_zero_text(self):
        # ★ 关键：一直骨架屏时，正文长度必须如实带出来（0 字）——归因全靠它
        page = _StubPage(card_from_round=None)
        card, info = doubao._wait_card(page, budget=3.0, step=0.5)
        self.assertFalse(card["found"])
        self.assertEqual(info["textlen"], 0)
        # 采样次数受「采样上限」约束，不会无限转
        self.assertLessEqual(info["samples"], int(3.0 / 0.5) + 2)

    def test_stops_early_when_logged_out(self):
        # 未登录就别干等 25 秒
        page = _StubPage(card_from_round=None, logged_out_from_round=1)
        card, info = doubao._wait_card(page, budget=20.0, step=0.5)
        self.assertFalse(card["found"])
        self.assertTrue(info["logged_out"])
        self.assertLessEqual(info["samples"], 3)

    def test_env_override_budget(self):
        old = os.environ.get("DOUBAO_CARD_WAIT")
        try:
            os.environ["DOUBAO_CARD_WAIT"] = "60"
            self.assertEqual(doubao._card_budget(), 60.0)
            os.environ["DOUBAO_CARD_WAIT"] = "999"      # 上限夹住
            self.assertEqual(doubao._card_budget(), 180.0)
            os.environ["DOUBAO_CARD_WAIT"] = "垃圾"
            self.assertEqual(doubao._card_budget(), doubao.CARD_WAIT)
        finally:
            if old is None:
                os.environ.pop("DOUBAO_CARD_WAIT", None)
            else:
                os.environ["DOUBAO_CARD_WAIT"] = old


class TestDoubaoMissingCardReason(unittest.TestCase):
    """卡片没出来时必须分三档归因：未登录 / 还在加载 / 真没这张卡。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="doubao-reason-")
        self.old = os.environ.get("DOUBAO_CLAIM_LOG_DIR")
        os.environ["DOUBAO_CLAIM_LOG_DIR"] = self.tmp

    def tearDown(self):
        import shutil
        if self.old is None:
            os.environ.pop("DOUBAO_CLAIM_LOG_DIR", None)
        else:
            os.environ["DOUBAO_CLAIM_LOG_DIR"] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read_log(self):
        p = os.path.join(self.tmp, "claim-log.tsv")
        if not os.path.exists(p):
            return ""
        with open(p, encoding="utf-8") as f:
            return f.read()

    def test_skeleton_is_not_reported_as_site_redesign(self):
        # ★ 这条就是血案本体：正文 0 字 ⇒ 必须说「没渲染完」，不许说「活动改版」
        r = doubao._login_or_card_missing(
            _StubPage(), {"waited": 12.5, "textlen": 0, "logged_out": False},
            os.path.join(self.tmp, "x.png"), [], False, "first")
        self.assertEqual(r["state"], base.FAILED)
        self.assertIn("没渲染完", r["report"])
        self.assertNotIn("改版", r["report"])
        self.assertIn("page-not-rendered", self._read_log())

    def test_rendered_page_without_card_is_redesign(self):
        r = doubao._login_or_card_missing(
            _StubPage(), {"waited": 25.0, "textlen": 1400, "logged_out": False},
            os.path.join(self.tmp, "x.png"), [], False, "after-reload")
        self.assertEqual(r["state"], base.FAILED)
        self.assertIn("找不到「每日签到」卡片", r["report"])
        self.assertIn("card-not-found", self._read_log())

    def test_logged_out_wins_over_everything(self):
        r = doubao._login_or_card_missing(
            _StubPage(), {"waited": 1.0, "textlen": 0, "logged_out": True},
            os.path.join(self.tmp, "x.png"), [], False, "first")
        self.assertEqual(r["state"], base.ERROR)
        self.assertEqual(r["exit"], doubao.EXIT_LOGIN)
        self.assertIn("not-logged-in", self._read_log())


class TestRetryDecision(unittest.TestCase):
    """按需补跑：只在「今天确有 failed」时才真跑，正常日子不许去弹豆包窗口。"""

    def _payload(self, states, when=None):
        return {"when": when or "2026-11-16 00:05:12",
                "sites": [{"site": "doubao", "ok": s in (base.CLAIMED, base.ALREADY,
                                                          base.SKIPPED),
                           "state": s, "report": "x"} for s in states]}

    def test_all_good_does_not_retry(self):
        # ★ 这条最要紧：全成时补跑若还去开豆包，等于每天白天抢一次焦点
        need, why = signin_hub.should_retry(self._payload([base.CLAIMED, base.ALREADY]))
        self.assertFalse(need)
        self.assertIn("全部完成", why)

    def test_failed_triggers_retry(self):
        need, why = signin_hub.should_retry(
            self._payload([base.CLAIMED, base.FAILED]))
        self.assertTrue(need)
        self.assertIn("没成", why)

    def test_error_alone_does_not_retry(self):
        # 未登录 / 缺依赖属于「人得先介入」，补跑只会再失败一次、还多弹一次窗
        need, why = signin_hub.should_retry(self._payload([base.ERROR]))
        self.assertFalse(need)
        self.assertIn("人工处理", why)

    def test_no_record_means_retry(self):
        need, why = signin_hub.should_retry(None)
        self.assertTrue(need)
        self.assertIn("没有", why)

    def test_last_run_of_day_picks_last_and_ignores_other_days(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="hublog-")
        p = os.path.join(tmp, "hub.log")
        lines = [
            '[2026-11-15 00:05:14] {"when": "2026-11-15 00:05:14", "sites": []}',
            '[2026-11-16 00:05:12] {"when": "2026-11-16 00:05:12", "sites": [{"site":"d","ok":false,"state":"failed","report":"r1"}]}',
            '[2026-11-16 12:30:00] {"when": "2026-11-16 12:30:00", "sites": [{"site":"d","ok":true,"state":"claimed","report":"r2"}]}',
            '这是坏行，不该让解析崩',
        ]
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        last = signin_hub.last_run_of_day(p, day="2026-11-16")
        self.assertEqual(last["when"], "2026-11-16 12:30:00")
        self.assertTrue(last["sites"][0]["ok"])
        # 当天的记录被补跑修正后，就不该再补
        self.assertFalse(signin_hub.should_retry(last)[0])
        # 没有文件、没有当天记录都要能安全返回 None
        self.assertIsNone(signin_hub.last_run_of_day(os.path.join(tmp, "nope.log")))
        self.assertIsNone(signin_hub.last_run_of_day(p, day="1999-01-01"))
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    def test_retry_skips_without_touching_network(self):
        """补跑命令在「今天全成」时必须是纯读日志、不跑任何站点。"""
        import tempfile
        tmp = tempfile.mkdtemp(prefix="hubretry-")
        p = os.path.join(tmp, "hub.log")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write('[2026-11-16 00:05:12] {"when": "%s 00:05:12", '
                    '"sites": [{"site":"d","ok":true,"state":"claimed","report":"ok"}]}\n'
                    % __import__("datetime").date.today().isoformat())
        called = []
        real = signin_hub.cmd_run
        signin_hub.cmd_run = lambda a: called.append(1) or 0
        try:
            rc = signin_hub.main(["retry", "--json", "--log", p])
        finally:
            signin_hub.cmd_run = real
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(rc, 0)
        self.assertEqual(called, [], "全成时不许真的去跑站点")


class TestHubCli(unittest.TestCase):
    def test_split(self):
        self.assertEqual(signin_hub._split("a, b ,,c"), ["a", "b", "c"])
        self.assertEqual(signin_hub._split(""), [])

    def test_parse_only_and_skip(self):
        a = signin_hub._parse(["run", "all", "--skip", "doubao", "--dry-run"])
        self.assertEqual(a.cmd, "run")
        self.assertEqual(a.target, "all")
        self.assertEqual(a.skip, "doubao")
        self.assertTrue(a.dry_run)

    def test_unknown_site_exit_code(self):
        self.assertEqual(signin_hub.main(["run", "nosuchsite", "--json"]), 2)

    def test_list_runs(self):
        self.assertEqual(signin_hub.main(["list", "--json"]), 0)

    def test_survives_pythonw_where_stdout_is_none(self):
        """计划任务用 pythonw.exe 跑 ⇒ sys.stdout is None，print 会 AttributeError。

        ★ 这是真踩过的形态：任务结果报 0、日志里一个字都没有 —— 因为唯一那个
          print 先炸、兜底分支再炸，两次都在写同样的不可用 stdout。
        """
        old = sys.stdout
        try:
            sys.stdout = None
            self.assertEqual(signin_hub.main(["list", "--json"]), 0)
        finally:
            sys.stdout = old


class TestSingleSourceOfTruth(unittest.TestCase):
    def test_legacy_script_is_a_thin_shim(self):
        """免疫战争窗口那份脚本必须已改成**转发垫片**，否则就是两份正本各自演化。"""
        p = r"F:\CodeAi\Workbuddy\ImmuneWarPro\tools\美术生成\doubao_daily_claim.py"
        if not os.path.exists(p):
            self.skipTest("旧脚本不在本机（可能已删除）")
        src = open(p, encoding="utf-8").read()
        self.assertIn("signin_hub", src, "垫片必须调用签到台")
        self.assertIn("禁止", src, "垫片必须写明「禁止在此修改」")
        # 原脚本的核心探针不该再出现在垫片里
        self.assertNotIn("JS_CARD", src, "垫片里不该还有业务逻辑（会分叉）")


if __name__ == "__main__":
    unittest.main()
