#!/usr/bin/env python3
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))

from strategies.display import (  # noqa: E402
    localize_decision_display_fields,
    localize_strategy_text,
    mainline_mode_label,
    mainline_state_label,
    stock_role_label,
)


class StrategyDisplayTests(unittest.TestCase):
    def test_candidate_enums_have_chinese_prompt_labels(self):
        self.assertEqual(mainline_state_label("emerging"), "启动阶段")
        self.assertEqual(stock_role_label("follower"), "跟随股")
        self.assertEqual(mainline_mode_label("dual"), "双主线")
        self.assertEqual(mainline_state_label("future_state"), "future_state")

    def test_known_standalone_enums_are_localized_without_touching_identifiers(self):
        text = localize_strategy_text(
            "emerging题材由follower转为leader；保留niu_emerging、EMA20、CRO和hardcore"
        )
        self.assertEqual(
            text,
            "启动阶段题材由跟随股转为领涨股；保留niu_emerging、EMA20、CRO和hardcore",
        )

    def test_model_summary_and_action_reasons_are_localized_before_persistence(self):
        decision = {
            "summary": "emerging题材中的follower暂时观望",
            "actions": [
                {
                    "action": "BUY",
                    "reason": "mainline仍在，个股由follower转为core",
                }
            ],
            "buy_refinement": {"summary": "保留leader，放弃follower"},
        }
        localized = localize_decision_display_fields(decision)
        self.assertEqual(localized["summary"], "启动阶段题材中的跟随股暂时观望")
        self.assertEqual(
            localized["actions"][0]["reason"],
            "主线阶段仍在，个股由跟随股转为核心股",
        )
        self.assertEqual(localized["buy_refinement"]["summary"], "保留领涨股，放弃跟随股")

    def test_frontend_localizes_historical_reason_text(self):
        module_uri = (ROOT / "web" / "src" / "utils" / "practiceDisplay.js").as_uri()
        scenario = f"""
const {{ localizePracticeReason }} = await import({json.dumps(module_uri)});
console.log(JSON.stringify({{
  localized: localizePracticeReason('emerging题材由follower转为leader'),
  preserved: localizePracticeReason('niu_emerging EMA20 CRO hardcore'),
}}));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", scenario],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
        )
        self.assertEqual(
            json.loads(result.stdout),
            {
                "localized": "启动阶段题材由跟随股转为领涨股",
                "preserved": "niu_emerging EMA20 CRO hardcore",
            },
        )


if __name__ == "__main__":
    unittest.main()
