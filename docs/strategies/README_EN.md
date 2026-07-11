# Strategy Research Guide

[简体中文](README.md) | English

This document describes the signal screening, simulated decision-making, and review mechanisms used for research experiments in NiuOne. These capabilities run only in a local research and simulation environment. They do not connect to brokerage accounts, execute real-money trades, or constitute investment advice.

## 1. Research Workflow

NiuOne divides the research workflow into four layers:

1. **Candidate generation**: Scan public market data using the currently enabled rules to produce samples for observation.
2. **Context enrichment**: Aggregate indices, sectors, capital flows, market activity, and recent news as needed.
3. **Simulated assessment**: Combine rule constraints, account state, and optional model output to generate simulation records.
4. **Archival and review**: Save input summaries, source status, simulated actions, and outcomes for later inspection.

Strategy outputs are experimental signals and must not be used as the basis for real trades. If a market-data or model source is temporarily unavailable, the system records its status and either continues with the currently available information or skips the affected step.

## 2. Strategy Sources

The settings page provides two strategy sources, with only one active at a time:

| Source | Description |
|---|---|
| Built-in rules | Use rule groups implemented in the project to generate candidates and participate in simulated assessments. |
| Preset text rules | Convert the user's natural-language requirements into candidate, entry, exit, position-sizing, and timing constraints. |

When built-in rules are used, Basic Strategies, Z-ge, and Li Daxiao are peer strategy groups, and only one can be enabled at a time. When preset text rules are used, the system first generates a neutral candidate pool and then completes the simulated assessment according to the text rules. If the text rules are empty, the system creates no new simulated positions and performs only risk checks on existing simulated holdings.

## 3. Built-in Strategy Groups

| Strategy group | Included proxy signals | Research focus |
|---|---|---|
| Basic Strategies | Breakout confirmation, trend pullback | General technical-pattern observation |
| Z-ge | Shaofu B1, B2 confirmation, B3 continuation, Super B1, exit risk controls | Trend- and timing-oriented rule experiments |
| Li Daxiao | Undervalued blue chips, bottom formation, contrarian sentiment, deleveraging defense | Value- and defense-oriented rule experiments |

### 3.1 Basic Strategies

- **Breakout confirmation**: Treat a stable pullback after a platform or previous-high breakout as a trend-confirmation sample.
- **Trend pullback**: Treat a strong-trend security that pulls back without breaking below BBI / EMA as a lower-entry observation sample.

### 3.2 Z-ge Rule Group

This rule group references public methods organized in `zettaranc-skill` and currently includes:

- **Shaofu B1**: Focuses on a low J value, an upward-shifting N pattern, a low-volume pullback, and BBI constraints.
- **B2 confirmation**: Looks for bullish, higher-volume confirmation after B1 and filters samples that are clearly lagging or too far above BBI.
- **B3 continuation**: Looks for a small bullish candle or doji after B2, as well as a shift from divergence to consensus.
- **Super B1**: Looks for low-volume stabilization after a high-volume breakdown while the J value remains low.

Exit and risk rules include constraints based on the previous low or entry candle, fixed risk thresholds, a premature-exit prevention score, staged exits, S1 / S2 / S3 top-escape signals, distribution patterns, white-line / BBI breakdowns, peak drawdown, ATR chandelier protection, and timing constraints for B2, B3, and Super B1.

### 3.3 Li Daxiao Rule Group

This rule group references the policy, value, bottom-formation, contrarian-sentiment, and leverage-risk-control frameworks in `li-daxiao-skill`. It uses highly liquid blue chips, low-level stabilization, low turnover, contracting volume and low volatility, anti-chasing rules, and risky-security filters as executable proxy signals.

The names above are used only to label rule experiments in this project. They do not indicate that the original authors participated in, approved, or endorsed this project. When redistributing related descriptions, retain the references to `zettaranc-skill` and `li-daxiao-skill`.

## 4. Simulated Decision Intelligence Package

The simulation process can compress multiple sources into a structured context and save it with the decision log. By default, it may include:

- Market-monitoring guidance and overnight market summaries;
- A-share indices, A50, U.S. indices or futures, gold, crude oil, and other market data;
- Sector performance, industry capital flows, trading activity, and turnover information;
- Recent news about candidate samples and confirmation or divergence between industry and market data;
- Simulated-account cash, total exposure, position weights, profit-and-loss status, and rule markers.

Each practice-trading candidate scan reuses real-time quotes for non-ST stocks on the main boards that the scanner has already retrieved, then recalculates the market label from the current counts of advancing and declining stocks and the breadth of limit-up and limit-down stocks. If data coverage is insufficient, the snapshot is stale, or the market is still in the 9:25 opening-auction phase, the system falls back to the latest auction, midday, or post-close report. Even if the current scan finds no candidates, it still refreshes and records the market context.

Position weight is calculated as `price × quantity ÷ current simulated total equity`. The log records each change as a percentage of simulated total equity, as well as the resulting individual-position and total-position percentages.

Related settings:

| Setting | Description |
|---|---|
| `DASHBOARD_DECISION_INTELLIGENCE_ENABLED` | Whether to enable the structured intelligence package |
| `DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS` | Cache lifetime for aggregated data |
| `DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` | Maximum number of items in each information category |
| `DASHBOARD_TRADE_DISCIPLINE_TEXT` | Custom simulation-discipline text |
| `DASHBOARD_MAX_OPEN_POSITIONS` | Reference maximum number of simulated open positions |
| `DASHBOARD_MAX_NEW_BUYS_PER_DECISION` | Reference maximum number of new simulated samples per decision round |
| `DASHBOARD_MAX_SINGLE_POSITION_PCT` | Reference percentage for an individual simulated position |
| `DASHBOARD_MAX_TOTAL_POSITION_PCT` | Reference percentage for total simulated exposure |
| `DASHBOARD_MIN_CASH_RESERVE_PCT` | Reference percentage for simulated cash reserves |

Percentage settings are used primarily for model context and research discipline. They must not be treated as execution-layer risk safeguards for real trading.

## 5. Configuration

Prefer maintaining the strategy source, rule group, text rules, and simulation discipline on the dashboard settings page. The corresponding environment variables include:

| Setting | Description |
|---|---|
| `DASHBOARD_STRATEGY_SOURCE` | Strategy source: built-in rules or preset text rules |
| `DASHBOARD_ENABLED_PERSONA_STRATEGIES` | Currently enabled built-in strategy group |
| `DASHBOARD_PRESET_STRATEGY_TEXT` | Custom preset text rules |
| `DASHBOARD_TRADE_DISCIPLINE_TEXT` | Additional simulation discipline |

Local configuration is stored in `.local-data/dashboard.env` by default. This file may contain model keys and administrative credentials and must not be committed to Git or copied into public contexts.

## 6. Extending Built-in Strategies

Strategy metadata is centralized in `app/strategy_registry.py`. To add a strategy:

1. Add the strategy group and its `label`, `color`, `desc`, `scorer`, `profile`, `position_limit_pct`, and `aliases` to the registry.
2. Implement the corresponding `score_xxx(rows)` scoring function in `app/multi_strategy_screen.py`.
3. Add automated tests for scoring boundaries, empty data, and abnormal market data.
4. Run `./scripts/validate.sh` to complete validation.

The scanner iterates over the scorers in the active strategy group and outputs `strategy_meta` to the dashboard and simulated-review module.

## 7. Usage Boundaries

- Do not treat model-generated content as factual. Verify the original data and information sources.
- Do not use this project as a substitute for licensed institutional services, professional risk assessment, or your own independent judgment.
- Historical replay, rule scores, and simulation results do not represent future performance.
- When adding data sources, verify data licensing, request-frequency limits, privacy requirements, and redistribution terms.
