from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StrategyBacktestFrontendTests(unittest.TestCase):
    def test_each_strategy_suite_card_links_to_its_backtest_page(self):
        source = (
            ROOT / "web" / "src" / "components" / "AdminEnvInput.vue"
        ).read_text(encoding="utf-8")

        self.assertIn("v-if=\"kind === 'strategy_suite'\"", source)
        self.assertIn("class=\"strategy-backtest-link\"", source)
        self.assertIn("`/admin/backtest/${encodeURIComponent(option.id)}`", source)
        self.assertIn(">回测</RouterLink>", source)

    def test_backtest_page_starts_and_polls_a_protected_progress_job(self):
        source = (
            ROOT / "web" / "src" / "components" / "AdminBacktestPage.vue"
        ).read_text(encoding="utf-8")
        router = (ROOT / "web" / "src" / "router.js").read_text(encoding="utf-8")

        self.assertIn("path: '/admin/backtest/:strategyId'", router)
        self.assertIn("fetch('/api/admin/backtests/options'", source)
        self.assertIn("fetch('/api/admin/backtests'", source)
        self.assertNotIn("risk_profile: form.riskProfile", source)
        self.assertNotIn("form.riskProfile", source)
        self.assertNotIn("riskProfileOptions", source)
        self.assertNotIn("selectedRiskProfile", source)
        self.assertIn("牛牛战法固定使用进取风险参数", source)
        self.assertIn("NIUONE_BACKTEST_PROTOCOL_VERSION", source)
        self.assertIn("'niuone-backtest-v32'", source)
        self.assertIn("staleResult", source)
        self.assertIn("当前结果由旧版牛牛回测协议生成", source)
        self.assertIn("/api/admin/backtests/latest/${encodeURIComponent(expectedStrategyId)}", source)
        self.assertIn("'X-NiuOne-Action': '1'", source)
        self.assertIn("async function restoreLatestJob()", source)
        self.assertIn("async function loadServerJob(expectedStrategyId", source)
        self.assertIn("window.setTimeout(() => loadServerJob(expectedStrategyId), 1200)", source)
        self.assertIn("precomputing: '预计算技术指标'", source)
        self.assertIn("normalizing: '整理历史行情'", source)
        self.assertIn("rebuilding_context: '重建题材截面'", source)
        self.assertIn("scoring: '执行策略评分'", source)
        self.assertIn("replaying_exits: '回放持仓退出'", source)
        self.assertIn("本日耗时 {{ formatDuration(job.day_elapsed_seconds) }}", source)
        self.assertIn("预计剩余 {{ formatDuration(job.eta_seconds) }}", source)
        self.assertIn("async function cancelBacktest()", source)
        self.assertIn("/api/admin/backtests/${encodeURIComponent(currentJobId)}/cancel", source)
        self.assertIn('class="backtest-cancel"', source)
        self.assertIn("终止回测", source)
        self.assertIn("background:var(--danger-button-bg)", source)
        self.assertIn("color:var(--danger-button-text)", source)
        self.assertIn(".backtest-cancel:hover:not(:disabled)", source)
        self.assertIn(".backtest-cancel:focus-visible", source)
        self.assertIn(".backtest-cancel::before{content:'■'", source)
        self.assertIn("cancelled: '已终止'", source)
        self.assertNotIn("localStorage", source)
        self.assertIn('role="progressbar"', source)
        self.assertIn("与模拟账户及持仓完全隔离", source)
        self.assertNotIn('class="backtest-convention"', source)
        self.assertIn("系统按历史行情自主选股；收盘信号于次日开盘买入", source)
        self.assertNotIn('class="backtest-auto-universe"', source)
        self.assertIn("无需输入股票", source)
        self.assertIn("不使用本地日 K 缓存", source)
        self.assertNotIn("牛牛反转按最近日线区间识别", source)
        self.assertNotIn("日内 V 型反转试仓依赖分时数据", source)
        self.assertIn("自动（东方财富 → 腾讯 → 新浪）", source)
        self.assertNotIn("自动（腾讯缓存 → 腾讯 → 东方财富）", source)
        self.assertNotIn("候选范围来源", source)
        self.assertIn('class="backtest-overview"', source)
        self.assertIn('v-if="job && isActive"', source)
        self.assertIn("'backtest-job-summary'", source)
        self.assertIn("结果已加载", source)
        self.assertNotIn('v-model="form.symbols"', source)
        self.assertIn("整体收益", source)
        self.assertIn("买卖收益", source)
        self.assertIn("交易明细", source)
        self.assertIn("statistics.value.evaluation_mode === 'strategy_portfolio'", source)
        self.assertIn("['trade_lifecycle', 'strategy_portfolio'].includes", source)
        self.assertIn("result.value?.selection?.portfolio", source)
        self.assertIn("result.value?.selection?.trades", source)
        self.assertIn("完整交易", source)
        self.assertIn("阶段升级会记录为同一周期内的加仓", source)
        self.assertIn("策略组合", source)
        self.assertIn("组合净收益", source)
        self.assertIn("最大回撤", source)
        self.assertIn("风险与资金效率", source)
        self.assertIn("portfolio.annualized_return_pct", source)
        self.assertIn("portfolio.sharpe_ratio", source)
        self.assertIn("portfolio.average_exposure_pct", source)
        self.assertIn("statistics.value.entry_rejection_counts", source)
        self.assertIn("买入未成交归因", source)
        self.assertIn("strategyPathLabel(trade)", source)
        self.assertIn("卖出规则使用每日收盘后可见的日 K 数据回放", source)
        self.assertIn("未入选原因", source)
        self.assertIn("selection?.diagnostics", source)
        self.assertIn("diagnostics.value.periods", source)
        self.assertIn("月度门槛敏感性与硬门槛消融", source)
        self.assertIn("row.score_sensitivity", source)
        self.assertIn("row.hard_gate_family_ablation", source)
        self.assertIn("row.leader_branch_coverage", source)
        self.assertIn("概念/行业龙头分支", source)
        self.assertIn("最佳标的当日阻断", source)
        self.assertIn("月度累计阻断族", source)
        self.assertIn("item.best_reasons", source)
        self.assertIn("item.monthly_blocker_family_counts", source)
        self.assertIn("function leaderBranchRows(values)", source)
        self.assertIn("left.stock_group_key.localeCompare(right.stock_group_key, 'zh-CN')", source)
        self.assertIn("left.sort_date.localeCompare(right.sort_date)", source)
        self.assertIn("leaderBranchRows(row.leader_branch_coverage)", source)
        self.assertIn(':rowspan="item.stock_group_size"', source)
        self.assertIn("最佳评分日", source)
        self.assertIn("function diagnosticThresholdLabel", source)
        self.assertIn("item.applicable_thresholds", source)
        self.assertIn("达到门槛后的主要拦截", source)
        self.assertIn("最接近入选", source)
        self.assertIn("子策略信号上限、同股去重及冷却期", source)
        self.assertNotIn("每日五只上限及冷却期处理前", source)
        self.assertIn('class="backtest-quality"', source)
        self.assertIn("数据质量与偏差说明", source)
        self.assertIn("qualityWarnings", source)
        self.assertIn("function warningSymbolCount(value)", source)
        self.assertIn("未获取：${missingCount} 只", source)
        self.assertIn("`部分标的发生行情源降级：${count} 只`", source)
        self.assertNotIn("未获取：${missingSymbols}", source)
        self.assertNotIn("行情源降级：${text.split", source)
        self.assertNotIn('v-for="warning in (result.warnings || [])"', source)
        self.assertIn("信号明细", source)
        self.assertIn("function stockCode(value)", source)
        self.assertIn("function stockName(item)", source)
        self.assertIn("{{ stockName(signal) }}", source)
        self.assertIn("{{ stockCode(signal.symbol) }}", source)
        self.assertIn("cooldown: '冷却期内重复信号'", source)
        self.assertIn("open_at_limit_up: '次日开盘涨停，无法按规则成交'", source)
        self.assertIn("{{ signalStatusReason(signal.status_reason) }}", source)
        self.assertIn("{{ stockName(item) }}", source)
        self.assertIn("{{ stockCode(item.symbol) }}", source)
        self.assertNotIn("<h2>行情数据</h2>", source)
        self.assertNotIn("const sourceRows", source)
        self.assertNotIn("{{ signal.symbol }}", source)
        self.assertNotIn("{{ item.symbol }}", source)

    def test_backtest_diagnostics_localize_warnings_and_reason_codes(self):
        source = (
            ROOT / "web" / "src" / "components" / "AdminBacktestPage.vue"
        ).read_text(encoding="utf-8")

        expected_reasons = {
            "markup_rebalance_rule": "主升回补条件未满足",
            "markup_upgrade_same_day_add": "主升升级当日不重复加仓",
            "markup_upgrade_early_done": "启动阶段升级加仓已完成",
            "markup_upgrade_confirmed_done": "主升阶段升级加仓已完成",
            "markup_upgrade_rule": "主升阶段升级加仓条件未满足",
            "markup_momentum_identity_block": "主升动量试仓不符合策略身份条件",
            "reversal_execution_gap": "试仓次日开盘跳空超过执行上限",
            "markup_momentum_execution_gap": "主升动量试仓次日跳空超过执行上限",
        }
        for code, label in expected_reasons.items():
            self.assertIn(f"{code}: '{label}'", source)
        self.assertIn("? '其他策略限制' : value", source)

        expected_warnings = {
            "current classification fallback used: iwencai_current_industry_concept": (
                "分类源降级"
            ),
            "stale current classification snapshot used": "分类快照过期",
            "NiuOne structural stops use the completed daily low": "结构止损假设",
            "NiuOne entries use 100% of the deterministic maximum risk-permitted": (
                "定仓差异"
            ),
            "NiuOne aggressive backtest profile increases account-risk": "进取参数",
        }
        for legacy_text, label in expected_warnings.items():
            self.assertIn(f"text.includes('{legacy_text}')", source)
            self.assertIn(f"return '{label}'", source)
        self.assertIn("牛牛结构止损使用已完成日 K 的最低价判断触发", source)
        self.assertIn("组合收益和回撤反映最大定仓情景", source)
        self.assertIn("不会放宽价格形态、结构止损、涨停或 T+1 规则", source)
        self.assertIn("部分标的历史行情获取失败", source)
        self.assertIn("当前行业/概念分类已改用问财备用源", source)
        self.assertIn("当前行业/概念分类使用了过期快照", source)
        self.assertIn("日期未知的过期快照", source)
        self.assertIn("选股回放缓存未能持久化", source)


if __name__ == "__main__":
    unittest.main()
