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
