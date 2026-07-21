#!/usr/bin/env python3
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RATING_UTILS = ROOT / "web" / "src" / "utils" / "usRatingDisplay.js"


class UsRatingFrontendParserTests(unittest.TestCase):
    def run_parser(self, content: str) -> dict:
        script = f"""
import {{parseRatingReport}} from {json.dumps(RATING_UTILS.as_uri())};
const report = parseRatingReport({json.dumps(content, ensure_ascii=False)});
console.log(JSON.stringify(report));
"""
        output = subprocess.check_output(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            text=True,
        )
        return json.loads(output)

    def test_parses_plain_stock_headers_when_followed_by_analyst_field(self):
        content = """牛牛大王，美股机构买入评级日报（2026年07月19日）

CNBC / Reuters
本行只是来源说明，不应识别为股票。

TEST / Test Corp
机构/分析师：Example Bank / Example Analyst
评级动作：新覆盖 Buy
目标价：100美元
核心理由/催化剂：测试催化剂
风险点：测试风险
适合关注类型：中线趋势

DEMO / Demo Holdings
机构/分析师：Second Bank / Second Analyst
评级动作：从 Hold 上调至 Buy
目标价：50美元
核心理由/催化剂：第二条测试催化剂
风险点：第二条测试风险
适合关注类型：长期配置

CNBC / Reuters
"""

        report = self.run_parser(content)

        self.assertEqual(
            [item["name"] for item in report["items"]],
            ["TEST / Test Corp", "DEMO / Demo Holdings"],
        )
        for item in report["items"]:
            self.assertTrue(
                all(
                    item.get(field)
                    for field in ("analyst", "action", "target", "reason", "risk", "type")
                )
            )

    def test_parses_two_letter_ticker(self):
        content = """牛牛大王，美股机构买入评级日报（2026年07月21日）

MU / Micron Technology
机构/分析师：Example Bank / Example Analyst
评级动作：维持 Buy
目标价：$180
核心理由/催化剂：存储周期改善
风险点：价格波动
适合关注类型：周期成长
"""

        report = self.run_parser(content)

        self.assertEqual([item["name"] for item in report["items"]], ["MU / Micron Technology"])


if __name__ == "__main__":
    unittest.main()
