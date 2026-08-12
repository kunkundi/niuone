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

## 2. Independent Strategies

The settings page directly selects one active strategy suite. Basic Strategies, Z-ge, Li Daxiao, Sector Tide, NiuOne Method, and Preset Text are peer, mutually exclusive suites. Each suite independently owns its candidate scope, scoring, entry, exit, sizing, and model-prompt rules. Inactive suites do not enter the current new-position scan or decision context.

Switching suites does not rewrite historical position attribution. The active suite controls new BUYs only; each existing position loads the exit discipline captured by its entry-time `strategy_mark`. Every cycle refreshes positions before processing new candidates, and SELL/HOLD checks continue when the candidate list is empty or the daily-loss budget has fired. The new prompt-strategy path calls a model exactly once while creating a draft. After explicit confirmation, selection, pre-buy revalidation, position monitoring, and exit all execute the immutable local plan without another runtime model call. Each position remains bound to its entry version, so activating a later version cannot rewrite an open position. Missing versions, facts, fingerprints, or audit evidence fail closed rather than guessing an action.

### 2.1 User Guide: Enabling and Triggering a Strategy

1. Set **Active independent strategy** to **Sector Tide** on the settings page. The corresponding value is `DASHBOARD_ACTIVE_STRATEGY=sector_tide`. This setting is applied at runtime and takes effect on the next scan without restarting the Dashboard.
2. New candidates and model decisions reuse the Practice page's shared schedule. The scheduler inside the Dashboard process reads `DASHBOARD_PRACTICE_SCHEDULE_TIMES`; Sector Tide does not have a separate candidate-scan timer.
3. To run immediately, click **Manually trigger candidate scan and trading strategy** on the practice page. One full cycle performs the market scan, candidate generation, model assessment, and execution-layer risk checks.
4. A 09:25 scan may use the opening-auction result to form candidates, but it cannot simulate a fill during the 09:25–09:30 quiet period. Any executable action is queued for a fresh price, session, and risk check after 09:30.

Scheduling ownership is split between two processes:

| Work | Process | Main settings | Behavior |
|---|---|---|---|
| Candidate scan and model decision | Dashboard | `DASHBOARD_B1_SCHEDULE_ENABLED`, `DASHBOARD_PRACTICE_SCHEDULE_TIMES` | Checks every open position under its original exit discipline first, then sends current-suite candidates into the simulated decision flow; zero-candidate scans still perform position exits |
| Local automatic exits | Cron Scheduler | `DASHBOARD_B3_EXIT_TIME`, `DASHBOARD_TIME_EXIT_TIME` | Refreshes position data at the configured times and checks structural stops, sector deterioration, time boxes, strategy-specific R-multiple partial exits, and 2 ATR rules |

Automatic exits are discrete scheduled checks, not broker-native conditional orders or tick-by-tick monitoring. Refreshing the page only reads state and never creates a simulated fill. Switching away from Sector Tide stops new Sector Tide candidates, while existing Sector Tide positions continue to receive exits according to their stored strategy marks.

## 3. Strategy Suites

| Strategy group | Included proxy signals | Research focus |
|---|---|---|
| Basic Strategies | Breakout confirmation, trend pullback | General technical-pattern observation |
| Z-ge | Shaofu B1, B2 confirmation, B3 continuation, Super B1, exit risk controls | Trend- and timing-oriented rule experiments |
| Li Daxiao | Undervalued blue chips, bottom formation, contrarian sentiment, deleveraging defense | Value- and defense-oriented rule experiments |
| Sector Tide | Main-theme leader, early rotation, freeze recovery | Market regime, industry rotation, and within-sector relative strength |
| NiuOne Method | NiuOne Probe, NiuOne Launch, NiuOne Leading, NiuOne Resumption | Tracks the complete mainline lifecycle from brewing and markup through climax and fade |

### 3.1 Basic Strategies

- **Breakout confirmation**: Treat a stable pullback after a platform or previous-high breakout as a trend-confirmation sample.
- **Trend pullback**: Treat a strong-trend security that pulls back without breaking below BBI / EMA as a lower-entry observation sample.

### 3.2 Z-ge Rule Group

This rule group references public methods organized in [`zettaranc-skill`](https://github.com/lululu811/zettaranc-skill) and currently includes:

- **Shaofu B1**: Focuses on a low J value, an upward-shifting N pattern, a low-volume pullback, and BBI constraints.
- **B2 confirmation**: Looks for bullish, higher-volume confirmation after B1 and filters samples that are clearly lagging or too far above BBI.
- **B3 continuation**: Looks for a small bullish candle or doji after B2, as well as a shift from divergence to consensus.
- **Super B1**: Looks for low-volume stabilization after a high-volume breakdown while the J value remains low.

Exit and risk rules include constraints based on the previous low or entry candle, a premature-exit prevention score, staged exits, S1 / S2 / S3 top-escape signals, distribution patterns, white-line / BBI breakdowns, peak drawdown, ATR chandelier protection, and timing constraints for B2, B3, and Super B1.

Shaofu B1 uses a hold-state machine that separates hard exits from confirmed soft exits. A new position receives at least three A-share trading days of observation, and only close-confirmed structural stops, a white-line/yellow-line dead cross, two consecutive white-line breaks, or high-confidence distribution may exit during the first 30 minutes after the open. Profit-to-loss, one-off BBI/score weakness, peak giveback, ATR chandelier, and time efficiency are soft signals: industry main-fund inflow or a shrinking-volume pullback keeps the position on hold; an ordinary soft signal must appear in two distinct check windows before reducing half; industry outflow together with projected high-volume selling can confirm the half reduction immediately. Missing, failed, or stale flow and volume data stays neutral, and the remaining trend position still waits for a structural exit.

Industry direction reuses the Eastmoney industry main-net-flow snapshot already fetched by each scan. The projected stock-volume ratio combines current cumulative volume, the median volume of the latest 20 completed sessions, and the Dashboard's existing same-time market-turnover/full-day estimate, without adding a separate market request. A model-generated `SELL` for Shaofu B1 is advisory only and is downgraded to `HOLD`; the local position state machine owns actual exits.

### 3.3 Li Daxiao Rule Group

This rule group references the policy, value, bottom-formation, contrarian-sentiment, and leverage-risk-control frameworks in [`li-daxiao-skill`](https://github.com/sherjy/li-daxiao-skill). It uses highly liquid blue chips, low-level stabilization, low turnover, contracting volume and low volatility, anti-chasing rules, and risky-security filters as executable proxy signals.

### 3.4 Sector Tide

Sector Tide builds one cross-sectional snapshot from the same liquid-stock universe before scoring any stock. It then applies hard gates in the order market regime → industry tide → within-sector stock strength. Industry strength is a mandatory gate rather than a score bonus.

Each scan reads the rolling latest Dragon-Tiger snapshot only when its date exactly matches the prior A-share trading day. Main-list net flow, all top-five buy/sell seats, and institution-seat net flow are used as confirmation; incomplete same-day data and older snapshots remain neutral, with no arbitrary-date fallback. The industry overlay is capped at ±2.5 points and the stock overlay at ±0.35, so their combined effect on the ten-point candidate score cannot exceed ±0.45. A stock that is absent from the list stays neutral. A missing snapshot or incomplete seat data falls back to the available main list or a neutral value. A positive overlay is suppressed when the stock is up more than 7% that day or sits over 1.5 ATR above EMA20, while negative risk evidence remains active. This is a candidate-ranking feature for historical validation, not proof of higher future win rates.

The scan also reads an overnight summary only when it is valid for the current A-share date and represents the latest fully closed US session. Overall US risk appetite contributes between -0.15 and +0.05 points, while explicit US-sector-to-A-share-industry mappings contribute up to ±0.15 more. After the first technical and industry pass, only the top five Sector Tide candidates receive a structured three-day company-news precheck. Positive, negative, and neutral labels contribute +0.15, -0.30, and zero points respectively; missing, stale, or unclassified results stay neutral. Dragon-Tiger, overnight-US, and company-news confirmation is capped together between -0.90 and +0.60 points. Positive evidence applies only when the A-share industry is already leading or improving and the stock is not extended, while negative evidence remains active. The structured news record is cached with the candidate and reused by model decisioning, avoiding a duplicate request in the same scan cycle.

- **Main-theme Leader**: available only in offensive or rotation regimes, requires a leading industry and a stock in the top 20% of its industry, and accepts only a breakout or a low-volume EMA20 pullback. Its 8% single-name limit is an absolute ceiling; dynamic risk determines the actual size.
- **Early Rotation**: requires an improving industry and a stock in its top 30%. It rejects a one-day gain above 7% and an extension above 1.5 ATR from EMA20. Its 6% limit is an absolute ceiling.
- **Freeze Recovery**: available only after defense has cleared, requires one of the first industries and stocks to recover, and exits if recovery is not confirmed by T+2. Its 4% limit is an absolute ceiling; the recovery risk budget determines the actual size.

In offensive/rotation/recovery regimes, per-trade NAV risk is budgeted at 0.30%/0.20%/0.10%, strategy open-stop risk at 1.50%/0.80%/0.30%, sector risk at 0.60%/0.40%/0.20%, total exposure at 45%/30%/15%, and sector exposure at 12%/10%/6%. Defensive regimes set all new-risk budgets to zero. Effective loss distance equals structural stop distance plus the larger of the trailing 60-day downside-gap p95 and 0.5 ATR, plus a 0.20% execution reserve; the smaller of risk-sized weight and the registered ceiling binds. Each industry remains limited to two names. Missing industry-flow data explicitly falls back to volume participation and is never interpreted as an inflow.

#### User Guide: Sizing and Exit Behavior

- Every model BUY/SELL action must specify a round lot in multiples of 100 shares. The execution layer never invents a default size. For NiuOne only, an otherwise valid BUY above a positive deterministic risk ceiling is reduced to that ceiling, while a model SELL above a positive whole-lot T+1 available quantity is reduced to that availability. Eligibility, capacity, missing-input, zero-ceiling, and zero/non-round-lot-availability failures still reject, and other suites retain their existing behavior.
- A candidate must have a valid structural stop no farther than both 6% and 1.5 ATR. Missing downside-gap/ATR reserve data blocks the entry.
- The requested size is checked against the dynamic single-name cap, market-regime total exposure, same-industry count, industry exposure, per-trade risk, strategy open risk, and strategy-sector risk. Any breach rejects the entire order and records the reason in the decision log.
- A regime budget is Sector Tide's maximum permission. If global market guidance, `DASHBOARD_MAX_TOTAL_POSITION_PCT`, or the cash reserve is tighter, the execution layer uses the smallest limit; a looser global setting never expands the Sector Tide budget.
- Total and industry exposure use the whole simulated account. Same-industry counts include every open position carrying that industry label. Open-stop and sector-stop risk totals include Sector Tide positions only.
- Main-theme Leader exits after five trading days without progress, Early Rotation after three days without continuation, and Freeze Recovery at T+2 without confirmation. These time-box rules are evaluated during the configured end-of-day exit check.
- `2R target = average cost + 2 × (average cost - entry structural stop)`. The first 2R event sells half; the remainder exits at `highest price since entry - 2 × ATR` after that trailing line is above cost.

#### Developer Contract: Regimes, Scores, and Data

The market regime is created from the same scan snapshot. The market composite weights core-index trend at 25%, advance/decline breadth at 25%, median return at 15%, limit-up/limit-down structure at 15%, the universe's 20-day trend at 10%, and volume participation at 10%.

| State | Rule |
|---|---|
| `offensive` | Market score ≥65 and advance breadth ≥55%, after state confirmation |
| `rotation` | Neither defensive nor offensive, after state confirmation |
| `recovery` | The prior confirmed state was defensive and the current raw state has cleared defense |
| `defensive` | Market score <40 uses the minimum-risk tier; a compound hard stop also enters defensive and blocks entries |

The compound hard stop requires index breakdown, market-breadth breakdown, and limit-down expansion at the same time. Except for a hard stop, a state change normally requires two consecutive scans with the same raw state; a first run with no state history accepts the current result immediately.

Industry score weights and tide thresholds are:

| Factor | Weight |
|---|---:|
| 20-day relative-strength percentile | 25% |
| 5-day relative-strength percentile | 15% |
| Rank-acceleration percentile | 15% |
| Breadth above EMA20 | 20% |
| 20-day new-high ratio | 10% |
| Industry flow; volume participation when missing | 10% |
| Industry turnover-liquidity percentile | 5% |
| Prior-trading-day Dragon-Tiger confirmation | Capped ±2.5-point overlay outside the base score |
| Latest completed US session and industry mapping | Capped -0.25 to +0.20 stock-score overlay |
| Top-five candidate company news over the prior three days | +0.15 positive / -0.30 negative stock-score overlay |

An industry needs at least three valid members, and each stock needs at least 55 daily bars. `leading` requires an industry score ≥75 and a 20-day relative-strength percentile ≥70. `improving` requires a score ≥65, rank acceleration ≥15, and a 5-day relative-strength percentile ≥65. A score <45 or 20-day relative-strength percentile <35 is `lagging`; every other case is `weakening`.

The entry-score thresholds for Main-theme Leader, Early Rotation, and Freeze Recovery are 8.0, 8.2, and 8.5; their minimum within-industry strength percentiles are 80, 70, and 70. Candidate-cache records must carry the market regime, industry tide, Dragon-Tiger snapshot date/availability/capped adjustment, US-session date/risk tone/sector mapping, news label/summary/fetch time, structural stop, gap reserve, effective loss distance, and all applicable risk budgets. The execution layer recalculates risk using the live simulated fill price and shared registered parameters; it does not trust model-supplied risk numbers.

The primary implementation files are `app/strategies/scoring/sector_tide.py`, `app/strategies/sector_tide_risk.py`, and `app/trading/practice_trader.py`. Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_sector_tide_strategy.py' -v` for the focused regression suite and `./scripts/validate.sh` for full validation.

### 3.5 NiuOne Method

The NiuOne Method first scores every stock in the same full, quote-valid cross section, then aggregates strong stocks by industry as an auditable theme proxy. Stock strength combines 20-day and 5-day relative strength at 30%/20%, volume participation and cross-market turnover-amount percentile at 15%/15%, and EMA20/EMA50 alignment plus 20-day highs at 10% each. Turnover amount measures actual capital participation and does not affect position caps. A separate 0–100 stock-activity score combines 65% cross-market amount percentile, 25% within-theme amount percentile, and 10% volume-participation percentile for candidate explanation and forward auditing. Industry is a deterministic proxy, not an assertion that similarly named stocks share a concept.

Mature mainline actions now have a deterministic capital-activity gate. NiuOne Leading, Resumption, and Launch must rank at or above the 60th percentile by turnover amount across the market and at or above the 50th percentile inside the action-selected theme; missing amount evidence fails closed. The gate neither rewards size by itself nor treats high turnover as sufficient, so a genuinely liquid small-cap leader can still pass while a thinly traded name cannot qualify merely because a small float produces a sharp move. Probe remains exempt for early discovery, but an unconfirmed activity profile is explicitly labeled as probe-only.

Theme scores combine strong-stock strength, breadth, leadership depth, capital participation, persistence across market dates, and bounded Dragon-Tiger confirmation; they do not use company news. Contribution weights use stock strength and square-root turnover; effective strong-stock count is `1 / Σ(weight²)`. A single dominant stock is penalized and cannot confirm a mainline. The Theme Strength page also shows normalized effective breadth as `effective strong-stock count / valid theme members × 100%` for comparison across differently sized themes, while state thresholds continue to use the raw effective count. The result may explicitly be `mainline.mode=none`.

Theme states progress through `candidate`, `emerging`, `mainline`, `diverging`, `fading`, and `inactive`, but scan observation is now separated from trading confirmation. No number of same-day scans can authorize a mainline buy: a theme that meets the cross-sectional threshold is recorded as an `intraday_mainline` observation only. It becomes `mainline` only when it remains strong on the next adjacent trading day and at least two of its top-five core stocks persist. A date gap or a completely replaced core resets confirmation. Mainline quality requires score ≥75, at least three strong stocks, and effective count ≥2.4.

Those internal states map to a five-stage trading lifecycle. `candidate` and a not-yet-persistent `emerging` theme are **Mainline Brewing**; persistent `emerging` and ordinary-strength `mainline` observations are **Mainline Markup**; a cross-session-confirmed `mainline` with a theme score of at least 78 is **Mainline Climax**; `diverging` is **Mainline Divergence**; and `fading` is **Mainline Fade**. The 78-point boundary comes from the high-strength tail of the mainline-score distribution, not from an outcome-optimized threshold search. An `inactive` theme that never started is not mislabeled as a completed fade. The five stages and four actions now share one production hard route: Brewing permits Probe only; Markup permits Launch or Leading; Climax permits qualifying core Leading or stabilized Resumption entries; Divergence permits a still-valid core Leading action or a stabilized Resumption; Fade is exit-only. Continued declines produce no entry. Causal hysteresis still moves a Markup fallback or a cooling Climax into Divergence before Fade. Scoring, Dashboard candidates, and historical replay all call the same stage/action validator instead of treating lifecycle as shadow attribution only.

When a stock belongs to several Eastmoney concepts, each action chooses its own lifecycle-compatible membership: Probe looks for a Brewing branch, Launch for a persistent `emerging` branch, and Leading/Resumption for a confirmed-mainline or valid-Divergence branch. A global profile chosen before the action can no longer reject a leader under an unrelated branch's stage. Primary/secondary mainline labels remain display and ranking signals rather than a rule that only two themes may trade; theme risk, per-theme holdings, daily openings, and total holdings remain enforced by the portfolio layer.

- **NiuOne Probe** (compatible with internal ID `niu_reversal_probe` and legacy label “NiuOne Reversal”) is restricted to Brewing: `candidate` or a not-yet-persistent `emerging` theme. Once the lifecycle reaches Markup or Divergence, the lower-certainty probe route is blocked. A stock already marked `stock_strong` inside a `candidate` theme must still wait for Launch confirmation. It also requires a stock-level V-shaped trend in the latest 30 daily bars. The left decline must span at least five sessions and 8%; the trough must be three to fifteen sessions old; the right side must rebound at least 6%, recover within `[60%, 200%)` of the decline, rise on at least two thirds of its sessions, and close near its recent right-side high. A recovery ratio of 200% or more is no longer an early probe. The theme must also have either at least six strong stocks or a Brewing-state streak of at least three trading days. Entry must sit 1.0–1.5 ATR above EMA20. Up to the two highest-ranked Probe candidates are retained each day. Threshold 7.6; absolute cap 6.25%, while offensive/rotation/recovery/defensive per-trade risk is 0.35%/0.30%/0.25%/0.15%.
- **NiuOne Leading** (compatible with internal ID `niu_leader` and legacy display name “NiuOne Leader”) participates in Markup and Climax, or after a confirmed mainline becomes `diverging` while the stock remains a strong core leader. The stock must be a top-20% core name with a breakout or first low-volume EMA20 pullback. Markup and Climax still require a same-day theme-strength score of at least 60; Divergence instead retains the strong-core and top-20% requirements because broad theme strength would contradict the stage itself. There is no fixed daily-gain cap: only an execution quote at the board's rounded limit-up price is rejected. The applicable-anchor extension caps remain 1.5 ATR in offensive markets and 1.25 ATR in rotation. Threshold 8.0; absolute cap 30%.
- **NiuOne Resumption** (compatible with internal ID `niu_pullback` and legacy display names “NiuOne Reclaim” and “NiuOne Pullback”) participates in Climax or Divergence. The raw theme must remain `mainline/diverging` with score ≥70 and the stock must be top-30%, stabilizing near or reclaiming EMA20; a continued decline does not qualify. There is no fixed daily-gain cap; execution still rejects a quote at limit-up. Its EMA20 extension caps remain 1.25 ATR in offensive markets and 1 ATR in rotation or recovery. Threshold 8.2; cap 25%.
- **NiuOne Launch** participates only in early Markup: an `emerging` theme that persisted across adjacent trading days but has not yet become a confirmed mainline. Once the theme is `mainline`, even below the Climax boundary, the candidate must satisfy Leading rather than re-enter through the lower-certainty Launch identity. At least two core stocks must continue and the candidate still needs a core breakout/reclaim; purely intraday strength cannot trigger it, and Divergence or Climax cannot use Launch. It has no fixed daily-gain cap, while extension above 1.5 ATR from the applicable anchor remains blocked and an execution quote at limit-up is rejected. Threshold 8.4; cap 15%. Launch is still an observation position and does not scale merely because Brewing moved into early Markup.

With the capital-activity gate, theme-context/dedicated-cache schemas advance to v13/v11. Strict-forward candidate evidence records the activity score, market/theme amount percentiles, and confirmation result under `niuone-strict-forward-v32`; admin backtests advance to `niuone-backtest-v33`. Older locks, reports, and backtests must not be pooled with the new eligibility evidence.

From v33, user-facing strategy reasons use context-safe Chinese display mapping. Internal lifecycle, role, and mainline-mode enums remain English for calculations and compatibility interfaces; only standalone lowercase enums in Chinese strategy context are converted. Proper names, English technical prose, errors, acronyms, and identifiers remain untouched, and nested refinement reasons are converted before persistence. Trading rules are unchanged, but the prompt and display mapping belong to the frozen evidence chain, so strict-forward advances to `niuone-strict-forward-v33` with a default new cohort on `2026-08-13`.

All four NiuOne entry paths therefore remove the former 4%/5%/7% daily-gain hard gates. Paper execution calculates the rounded board-specific limit price from previous close and rejects only a quote already at that price; a lower price proceeds to the remaining theme, setup, ATR-extension, structural-stop, and portfolio-risk checks. Historical replay likewise records `open_at_limit_up` when the next session opens locked at its limit.

Mature-path offensive/rotation/recovery/defensive per-trade NAV risk is 1.50%/1.00%/0.60%/0.30%; Probe tightens it to 0.35%/0.30%/0.25%/0.15%. Suite open-risk limits are 4.50%/3.00%/1.80%/0.90%, and mature-path theme-risk limits are 3.00%/2.00%/1.20%/0.60%. Probe-theme risk is capped at 0.70%/0.60%/0.50%/0.30% and Probe-theme exposure at 12%/10%/8%/5%. Total exposure is 70%/55%/35%/20%. Defensive permits entries at the minimum-risk tier; only the compound hard stop blocks new positions. The suite permits at most two new positions per Beijing trading date across Practice decision cycles and has a hard five-position limit. Probe uses the lowest low of the latest three right-side confirmation bars as its stop with a 6%/2 ATR cap; Leading/Launch breakouts use 0.5 ATR below the breakout pivot, while Resumption setups retain the recent structural low. Mature paths use 10%/2.5 ATR, 8%/2 ATR, and 6%/1.5 ATR structural-stop caps in offensive, rotation, and recovery/defensive regimes. NiuOne uses the simple mean of 14 daily true ranges and records `atr_period=14`; effective loss distance includes the structural stop, gap reserve, and execution buffer. A Probe exits on the first observed `fading`/`inactive` theme state or score below 55, or after three sessions without right-side follow-through; it may upgrade when Launch or confirmed-mainline conditions are later met. Mature paths retain their theme and time-box checks. A Probe first entered in an offensive, recovery, or defensive regime takes 50% off intraday at 0.75R; rotation-entry Probes and mature Leading/Resumption/Launch paths retain the 1R/45% first leg. The entry regime is immutable across later scans and stage upgrades. Every path protects the runner at average cost from the next session and keeps the 2 ATR trailing check. Primary files are `app/strategies/scoring/niuone.py`, `app/strategies/niuone_risk.py`, and `app/trading/practice_trader.py`. Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_niuone_strategy.py' -v` for focused regressions and `./scripts/validate.sh` for full validation.

Practice still requires explicit model quantities in 100-share lots. For NiuOne, an otherwise valid BUY above the positive whole-lot ceiling jointly determined by all continuous risk constraints is reduced only to that ceiling; eligibility, holding/theme capacity, stop inputs, cash, every risk budget, and a zero ceiling remain fail-closed. A model-directed SELL above a positive whole-lot T+1 available quantity executes at that availability; when reduction is needed, zero or non-round-lot availability still rejects, while local automatic exits and other suites are unchanged. It cumulatively permits at most two NiuOne openings per Beijing trading date across decision cycles; adds and other strategy suites do not consume that budget. Strict-forward v20 records model-requested shares, executed shares, the deterministic maximum, whether reduction occurred, ceiling utilization, binding constraints, and opening-versus-add status for every executed BUY, with five-stage aggregation. For model SELL fills it separately records the request, availability at execution, actual shares, and reduction flag and fails the post-close quality gate on inconsistent evidence. Market, eligibility, capacity, risk-input, and risk-ceiling rejections use structured categories. The decision copy and durable fill must agree for an executed BUY to pass the data-quality gate. The entry industry is frozen with the lifecycle for theme-concentration diagnostics. Historical backtests remain maximum-permitted-risk sizing scenarios rather than model-order replays. v20 freezes the BUY/SELL reduction rules, daily opening policy, and durable Brewing/Markup/Climax/Divergence/Fade path through actual exit, requiring an observation on every operating day held so stage reach, transitions, full paths, and exit-stage outcomes can be diagnosed.

v20 defines 6.25% as the initial Brewing Probe cap rather than a lifetime utilization target. A Probe- or Launch-origin position with 2%–12% unrealized profit may add once toward a 10% cap when its emerging mainline persists across sessions, remains in Markup, and the stock stays in the strong Leading tier. Once the mainline is fully confirmed it may add once more toward a 20% cap. The actual target remains the minimum of risk sizing, theme/portfolio risk, available cash, and the stage cap. Profit above 12% is not chased; Climax, Divergence, and Fade never authorize an add. The first non-losing Climax observation trims one third once; the existing 0.75R/1R partial, breakeven protection, and 2 ATR trail remain active.

v21 replaces the post-confirmation lifetime add count with repeatable, event-driven wave rebalancing. A confirmed Leading position releases one third after either a 1 ATR decline from the current cycle's closing-price peak or three sessions without a new peak while at least 0.25 ATR below it. Released risk can be replaced only after price rises 0.5 ATR from the trim, the lifecycle returns to Markup, and strong Leading status is restored. Every filled re-entry resets the peak and consolidation counters, so another add requires another independent pullback; continuous strength cannot mechanically generate buys. A prior 0.75R/1R partial can provide the released inventory for the same re-arm process. Divergence may trigger a reduction, but unrecovered Divergence, Climax, and Fade cannot add. Single-name, theme, portfolio, cash, and T+1 constraints remain binding.

The strict-forward operations-review gate is separate from the performance-claim gate: three elapsed months with fewer than 30 completed lifecycles can only trigger a frequency and operations review. The trade-level gate requires at least 30 fully attributed lifecycles, an observed win rate no lower than the frozen 59.71% historical reference, a Wilson 95% lower bound above 50%, and fee-inclusive average net return, cumulative realized P&L, and profit factor above break-even. v20 then clusters by opening date and entry industry: both unique and Herfindahl-effective cluster counts must be at least 30, cluster-balanced win rate must be at least 59.71%, its normal 95% lower bound must exceed 50%, and cluster-balanced average net return must be positive. Same-date, same-industry trades cannot repeatedly count as independent evidence. A high-win-rate and positive-return claim additionally requires a frozen pre-cohort zero-position account boundary, no non-NiuOne or unknown-strategy fills, one durable post-15:00 equity mark for every actual operating day, positive portfolio return, maximum drawdown no worse than 6%, return-to-drawdown of at least 1, and complete operations and opportunity-funnel evidence. Promotion is never automatic.

The Dashboard's “Theme Strength” section at `/niuone-mainline` is a full-market theme-research view independent of both the configured trading universe and the active trading strategy. It shows the market regime, cross-day confirmed mainline, intraday leading themes, the five highest-scoring themes, and continuing core stocks. Intraday strength observes same-day participation without changing NiuOne's cross-session lifecycle or trading gates; the Probe still evaluates each stock's daily-bar interval independently. Coverage is calculated as stocks valid for theme clustering divided by the full reference pool; the page does not expose trade candidates, position limits, or BUY status. Uncovered stocks are assigned to mutually exclusive processing-stage reasons: unavailable or fewer than 30 daily bars, fewer than the required 55 bars, invalid key metrics, missing industry mapping, or other incomplete data. Legacy snapshots without this detail explicitly show that the next scan must fill it in. At 09:10 on each A-share trading day, the Dashboard runs `--prewarm-kline-cache` to save the latest 120 qfq daily bars for every supported non-ST stock in private SQLite. Intraday scans append or replace today's bar from batch live quotes and fetch only missing or date-stale symbols online. By default, the market-sentiment sampler obtains one coverage-validated per-stock Tencent quote batch every 30 seconds. The fast Theme Strength calculator reuses that same batch, reads daily history and industry mappings from local caches, retains slow confirmation factors from the newest complete research scan, and writes `niuone_mainline_minute_latest.json`. It makes no additional Tencent, news-model, or trading request and does not replace the previous valid result when coverage is insufficient or quotes are stale. `DASHBOARD_MARKET_BREADTH_SAMPLE_INTERVAL_SECONDS` can configure the shared interval from 30 through 600 seconds. After every shared scheduled or manual trading scan, the Dashboard still starts a background `--niuone-mainline-only` complete research scan. It always uses the NiuOne theme algorithm and the full non-ST reference pool, atomically updates only `niuone_mainline_latest.json`, and cannot change the selected strategy, candidate cache, or simulated trades. The page selects the newest generated result from the minute, complete-research, and migration-era caches. The five-theme limit applies only to the public page; the dedicated cache retains every theme for cross-day continuity checks. Browsers receive only an allow-listed public view rather than per-stock quotes, the raw scan context, or SQLite cache.

The names above are used only to label rule experiments in this project. They do not indicate that the original authors participated in, approved, or endorsed this project. When redistributing related descriptions, retain the references to `zettaranc-skill` and `li-daxiao-skill`.

### 3.6 Prompt-Strategy Closed Loop

The admin page's **Prompt Strategy Closed Loop** accepts fuzzy natural language. The server uses the configured decision model to refine it into `selection`, `entry`, `exit`, sizing, candidate limits, and missing-data behavior, but each draft can be successfully refined only once. An interrupted stream, empty response, or incomplete JSON clears the partial browser output and receives one bounded complete-response fallback; a first local compile failure feeds its safe schema errors into one creation-time rewrite. For directionally explicit today/yesterday volume, high, and low comparisons in the original text, the local completeness layer removes model-generated approximations or malformed nodes for those fields, deterministically rebuilds selection/entry with `market.value` and `offset_bars=0/1`, and records the completion in assumptions for user review. An ambiguous comparison direction still fails closed. The common model alias `all/any.conditions` is normalized explicitly to `children`, while malformed sizing is never guessed or silently converted. If the retry still fails, the UI shows a specific safe reason without exposing response bodies or credentials, and the failed draft becomes retryable. Before activation, the user reviews the full JSON, assumptions, ambiguities, and feature dependencies. Explicit confirmation must match the reviewed plan fingerprint. Activation uses a two-phase transition: persist an immutable pending version, synchronize the runtime suite, and only then atomically replace the active version. A failure restores the draft and leaves the previous active version intact.

An activated version follows this local path:

1. Scan every quote-valid stock in the configured A-share universe, apply `selection`, and enforce `candidate_limit` only after filtering.
2. Read each stage's `required_features` from the frozen plan, load enough daily bars, and compute only those registered dependencies. A KDJ-only strategy does not precompute every indicator. In addition to the explicit recursive warm-up budgets for KDJ, EMA, RSI, and MACD, `prompt-rules-v3` gives every registered feature a generic `offset_bars` history offset and includes that offset in the minimum-bar budget. The plan also freezes the exact feature version. Existing `prompt-rules-v2` versions remain replayable.
3. Enforce the data contract on every evaluation: closed-bar rules must match the expected trading date, while live-bar rules also require a parseable observation time within `freshness_seconds`. Missing, stale, or date-misaligned inputs become `unknown` and cannot trade.
4. Immediately before BUY execution, reload market data, verify the selection-audit fingerprint, and reevaluate `entry`. Frozen sizing remains subordinate to system single-name exposure, total exposure, and minimum-cash ceilings, including a post-fee cash check.
5. Bind the new position to its entry version and monitor that version's `exit` rule. If T+1 blocks a true exit, mark it pending. Before the eventual SELL, load the exact durable audit, replay it, and verify the version, binding, plan fingerprint, code, and true result; only then release the binding.
6. Append every selection, entry recheck, and exit evaluation with the version, plan SHA-256, fact and data-quality snapshots, tri-state result, evidence tree, action intent, and complete replay context. Audit JSON is zlib-compressed, and exact retries are deduplicated by an idempotency key without collapsing observations from different timestamps.

The current feature registry covers OHLCV, MA/EMA/BBI/KDJ/RSI/MACD/Bollinger Bands, returns, volume ratio, volatility, ATR, rolling highs/lows, and drawdown. New indicators are added as versioned registry entries. Every feature can declare `offset_bars=0..499`, where `0` is the current evaluation bar and `1` is the previous bar; feature warm-up plus offset may require at most 500 bars. Thus “today's low is below yesterday's low and today's high is above yesterday's high” compares low/high at offsets `0` and `1` exactly instead of approximating the condition with a two-day range or return. Prompts cannot inject Python, SQL, `eval`, unregistered features, or unmaterialized runtime facts. Rule results are `true`, `false`, or `unknown`; insufficient history becomes `unknown` and always means HOLD/no BUY. For example, “buy when KDJ < 0 and sell when KDJ > 15” defaults to the J line on closed daily bars. That assumption is shown before activation and is never reinterpreted at runtime.

This path produces research recommendations or simulated trades only and does not connect to a broker. Trading windows, 100-share A-share lots, T+1, cash, position count, and total-exposure controls always take precedence over a strategy action.

The Backtest page can independently replay any activated or retired frozen version. The dedicated engine makes no additional model call: it loads the declared warm-up history, runs `selection → entry → exit` causally, fills close-generated entries at the next session open under the frozen sizing rule, and monitors exits only from completed daily bars. Every task owns a separate CNY 1,000,000 portfolio and never touches Practice. Version ID, plan SHA-256, and `prompt-backtest-v1` isolate both jobs and selection caches. Results embed selection, entry-recheck, and daily position-monitor audits plus a stage-counted, fingerprinted manifest whose records are deterministically replayed. The backtest applies static system limits of six holdings, two new positions per session, 10% per name, 80% total exposure, and at least 20% cash. Production market guidance may tighten them further; because historical guidance snapshots are incomplete, replay discloses that limitation rather than inventing dynamic gates.

## 4. Simulated Decision Intelligence Package

The simulation process can compress multiple sources into a structured context and save it with the decision log. By default, it may include:

- Market-monitoring guidance and overnight market summaries;
- A-share indices, A50, U.S. indices or futures, gold, crude oil, and other market data;
- Sector performance, industry capital flows, trading activity, and turnover information;
- Recent news about candidate samples and confirmation or divergence between industry and market data;
- Simulated-account cash, total exposure, position weights, profit-and-loss status, and rule markers.

Each practice-trading candidate scan reuses real-time quotes already retrieved for the configured stock universe, then recalculates the market label from the current counts of advancing and declining stocks and the breadth of limit-up and limit-down stocks. If data coverage is insufficient, the snapshot is stale, or the market is still in the 9:25 opening-auction phase, the system falls back to the latest auction, midday, or post-close report. Even if the current scan finds no candidates, it still refreshes and records the market context.

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

Percentage settings are primarily model context and research discipline by default. Suites with registered hard limits recheck their own limits in the simulation layer. NiuOne single-name exposure is governed by its independent risk budget and registered 30%/25%/15%/6.25% caps, rather than being reduced to the default 10% single-name reference. Global total exposure, dynamic market exposure, and cash-reserve limits still apply whenever they are stricter. These controls must not be treated as safeguards for a real brokerage account.

## 5. Configuration

Prefer maintaining the active independent strategy, text rules, and simulation discipline on the dashboard settings page. The corresponding environment variables include:

| Setting | Description |
|---|---|
| `DASHBOARD_ACTIVE_STRATEGY` | Active independent strategy: `base`, `zettaranc`, `li_daxiao_bottom`, `sector_tide`, `niuone`, or `preset_text` |
| `DASHBOARD_PROMPT_STRATEGY_DB` | Private SQLite path for frozen versions, append-only audits, and position bindings |
| `DASHBOARD_PROMPT_REFINEMENT_MAX_CONCURRENCY` | Maximum creation-time AI-refinement concurrency, limited to 1–2 |
| `DASHBOARD_PRESET_STRATEGY_TEXT` | Legacy compatibility text used only when no frozen version is active; create new strategies through the admin closed loop |
| `DASHBOARD_PRESET_STRATEGY_CANDIDATE_LIMIT` | Legacy compatibility candidate cap; new caps are frozen into each version |
| `DASHBOARD_STOCK_UNIVERSE` | Final-candidate and new-BUY scope: `st`, `chi_next`, `star_market`, and `main_board`; defaults to Main Board; NiuOne's full reference universe does not expand it |
| `DASHBOARD_TRADE_DISCIPLINE_TEXT` | Additional simulation discipline |

When `preset_text` is active and an activated frozen version exists, scanning and simulated trading prefer the new local closed loop. The old text path remains only as a compatibility fallback when no frozen version is active. Pre-upgrade holdings that have no new version binding are not guessed, migrated, or rewritten.
During creation, the admin page shows model output as it arrives and only allows confirmation after the complete output passes local compilation. The request reuses the buy/sell decision model's `DASHBOARD_DECISION_TIMEOUT`; there is no separate prompt-strategy timeout setting.

When NiuOne is active, the scanner uses every supported non-ST Shanghai/Shenzhen A-share (Main Board, ChiNext, and STAR Market) as its market and mainline reference universe. It fetches the complete quote set and sends every reference stock into uncapped K-line analysis, with no prefilter based on turnover amount, daily return, or limit-up-like movement. Final displayed candidates and new BUYs remain strictly limited by `DASHBOARD_STOCK_UNIVERSE` and require only a usable simulated-execution price. Existing positions may still execute stop-loss, take-profit, and other SELL controls after falling outside the setting, so changing the universe cannot trap a position. ST names enter final trading only when explicitly selected. Beijing Stock Exchange names remain excluded because the current quote, board-limit, and trading-permission models do not support them yet.

Local configuration is stored in `.local-data/dashboard.env` by default. This file may contain model keys and administrative credentials and must not be committed to Git or copied into public contexts.

The legacy `DASHBOARD_STRATEGY_SOURCE` and `DASHBOARD_ENABLED_PERSONA_STRATEGIES` settings are read only for seamless migration when `DASHBOARD_ACTIVE_STRATEGY` is absent.

## 6. Extending Independent Strategies

Strategy code is centralized under `app/strategies/`:

- `registry.py` owns metadata, groups, aliases, enablement, and settings options.
- `scoring/` owns indicators, hard gates, individual scorers, and the multi-strategy comparison engine.
- `selection.py` and `policy.py` own candidate eligibility, strategy-aware display selection, and position policy.
- `attribution.py` and `performance.py` own strategy marks, attribution, and performance summaries.
- `exits.py` and `prompts.py` own strategy-specific exit rules and model-prompt fragments.

`app/screening/multi_strategy.py` owns market-data retrieval, full-market scan orchestration, and caching. `app/trading/practice_trader.py` owns account, risk-control, and simulated-execution orchestration. Legacy module-name adapters are centralized under `app/compat/`; new code should import from the `strategies` package.

To add a built-in strategy:

1. Add its `label`, `color`, `desc`, `scorer`, `profile`, `position_limit_pct`, and `aliases` in `app/strategies/registry.py`.
2. Implement `score_xxx(rows)` in the appropriate file under `app/strategies/scoring/` and register it in the explicit map in `scoring/__init__.py`.
3. If it has dedicated exit or decision semantics, update only `exits.py` or `prompts.py`; do not put those rules back into the scanner or trader.
4. Add automated tests for scoring boundaries, empty data, and abnormal market data.
5. Run `./scripts/validate.sh` to complete validation.

The scanner iterates over the enabled scorers and outputs `strategy_meta` to the dashboard and simulated-review module.

## 7. Historical Stock-Selection Backtesting

`app/backtesting/` is an independent offline research package. It never reads or mutates simulated positions, orders, or fills. It supports fixed-horizon event studies and, for strategies with deterministic entry, risk, and exit rules, a run-local portfolio with its own cash, positions, and fills. Production scanning and simulated trading never import it.

Prompt strategies share one generic `PromptStrategyBacktestPolicy`, with exactly one frozen version loaded per run; KDJ, MACD, RSI, and future registered indicators therefore do not need separate backtest engines. The plan declares only the features that run needs, while its version snapshot, portfolio state, audit chain, and cache identity remain isolated. Historical daily bars cannot faithfully reproduce live-bar conditions, so the dedicated engine explicitly rejects `bar_status=live` versions instead of substituting close data for missing intraday evidence.

Every strategy-suite card in administrator settings has its own Backtest link. No ticker input is required: the job builds the candidate universe from the configured trading scope, while NiuOne additionally reconstructs its market/theme cross-section from all supported non-ST Shanghai and Shenzhen shares. Its Probe path first requires six strong stocks or a three-session Brewing streak, retains up to the two highest-ranked signals per day, and applies the same early-theme stage routing as paper trading. When one stock matches several sub-strategies, an actionable path takes precedence over a higher-scoring path rejected by its own hard gates. NiuOne starts with an isolated CNY 1,000,000 portfolio. At each next-open fill it sizes whole-board lots from the market regime, structural stop, and registered risk budget; it also enforces at most two new positions per trading day, five concurrent holdings, and the production single-name, theme, total-exposure, and cash-reserve limits. Probe and Launch positions can add only after their production stage-upgrade conditions confirm. Historical replay uses 100% of every accepted order's risk-permitted ceiling, whereas Practice uses the model's explicit share count and reduces only an otherwise-valid NiuOne BUY that exceeds a positive risk ceiling to that ceiling. The administrator result therefore reports `entry_sizing=maximum_permitted_risk_ceiling`, `entry_order_scale=1.0`, and `model_order_units_replayed=false`, and labels portfolio return and drawdown as a maximum-sizing scenario. Every buy lot has its own T+1 lock. The book then replays structure stops, mainline/leader deterioration, stage-specific time exits, first-profit protection resolved from the entry regime and current stage, next-session cost protection for the runner, and the 2ATR trailing exit. Once fully sold, the same stock may enter again on a later signal. The page shows portfolio return, maximum drawdown, completed holding-cycle returns, and entry/exit leg counts. Other strategies without deterministic portfolio rules retain fixed-horizon event-study results. Every run downloads its warmup, signal, and exit-follow-up range from Eastmoney, Tencent, or Sina without a local daily-K cache, requires at least 85% coverage, and persists server-side progress and results so refresh, cancellation, and restart recovery remain supported.

NiuOne backtesting is fixed to Aggressive parameters and no longer exposes a Balanced/Aggressive selector. It multiplies account-risk budgets by 1.35, total/theme exposure budgets by 1.15, and fixes daily-new/total/theme capacity at 3/6/3. Single-name caps, price patterns, structural stops, limit-up execution, and T+1 are not relaxed, and Practice portfolio controls are unchanged. v25 retains the v24 Markup Momentum Probe quality conditions: an ordinary entry requires Markup lifecycle, a cross-day-persistent `emerging` theme, the number-one industry leader, strength at least 90, score at least 8.1, theme score at least 70, and no more than 1 ATR of EMA20 extension. The 2.5–3.2 ATR range is reserved for exceptional acceleration with daily gain at least 9.5% and volume ratio no greater than 1.2. A qualified probe has a 4% initial cap, an 18%/3 ATR structural-stop limit, and a 3% next-open gap limit. Jobs and results carry `niuone-backtest-v25`; older protocols and persisted Balanced results are neither restored nor displayed as current-strategy evidence.

Backtest v26 and strict-forward v25 add a conditional Climax-remainder follower. It applies only after the Climax reduction has executed while the stock remains strong, the theme score is at least 55, and the theme is neither fading nor inactive. Relative leader-rank loss then waits for three consecutive sessions instead of two, and the remainder uses a 3 ATR rather than 2 ATR trail. Ordinary positions and runners that fail a health condition retain the original two-session/2 ATR controls; structural and break-even stops, mainline weakness, Fade, and the market hard stop are unchanged. Jobs and results carry `niuone-backtest-v26`; v25 results are not current-strategy evidence.

Strict-forward v26 and backtest v27 permit NiuOne entries in `defensive` while preserving the compound hard stop. The defensive mature-path per-trade/open/theme risk limits are 0.30%/0.90%/0.60%, with 20% total exposure and 12% theme exposure; Probe tightens per-trade/theme risk and theme exposure to 0.15%/0.30%/5%. Every lifecycle, leader, setup, stop, limit-up, and portfolio-capacity gate remains in force, and `market_hard_stop` still permits only SELL or HOLD. Evidence carries `niuone-strict-forward-v26` or `niuone-backtest-v27` and must not be pooled with older protocols.

Strict-forward v27 and backtest v28 separate factual industry from action theme. `industry/sector` carries only Eastmoney's `f100` industry, while `signal_theme` carries the action-selected `f103` concept. A multi-concept stock can no longer change trading attribution because of label order or later industry annotation: theme attribution combines 75% current theme/stock co-movement evidence with a 25% prior accumulated from preceding snapshots, and all concept weights for one stock sum to one. Among lifecycle-compatible branches, the stronger attribution wins; lifecycle, score, setup, structural-stop, and risk thresholds are unchanged. The first fill freezes `entry_theme`, and `active_theme` changes only after an alternative remains valid and leads by at least 10 points for two consecutive trading days. Forward performance clusters by entry date × entry theme and rejects lifecycles with missing theme evidence. Jobs and results carry `niuone-strict-forward-v27` or `niuone-backtest-v28`; older evidence must not be pooled.

Strict-forward v29 and backtest v30 move attribution ahead of aggregation. `f103` only creates candidate branches; current evidence combines leave-one-out peer resonance, cohort direction, same-day rank, and structural rank before applying the prior. Theme recognition performs no news search, and saved news cannot alter candidates, attribution, or theme totals. Multi-concept softmax weights retain residual unattributed mass. Theme score, strong stocks, amount, structural/intraday breadth, and leaders are recomputed from those weights; intraday breadth is also sample-size shrunk, and stocks below 15% attribution cannot lead the branch. Context schema v10 refuses v9 cross-day confirmation. Jobs and results carry `niuone-strict-forward-v29` or `niuone-backtest-v30` and must not be pooled with older evidence.

Strict-forward v30 and backtest v31 add 20-session market-neutral return-wave attribution. The stock and the leave-one-out median theme path are both expressed as excess returns over the daily market median, then correlated and shrunk by relative rank across that stock's `f103` candidates. Broad labels with similar aggregate direction but unrelated daily waves are therefore downweighted. Complete, minute, and ordinary NiuOne scans make no news-search or model call. Context/dedicated-cache schemas advance to v11/v9 and older evidence must not be pooled.

Strict-forward v31 and backtest v32 fix repeated leadership dilution for multi-concept stocks. The 15% attribution-weight floor remains for ordinary weak branches, but the stock's highest-scoring theme is leadership-eligible when its attribution score is at least 60 even if label count pushes its share below the floor. Qualified structural leaders rank by raw strength and qualified intraday leaders by same-day return rather than multiplying either value by attribution weight. The backtest checks structural eligibility against the actual next-session open, while 5bp synthetic slippage affects only the fill and risk sizing. Weighting still governs theme breadth, amount, concentration, and effective sample size; lifecycle, setup, stop, and portfolio-risk gates are unchanged. Context/dedicated-cache schemas advance to v12/v10 and older evidence must not be pooled.

Signal replay causally precomputes technical indicators once for each stock's complete historical series and exposes a read-only tail view on each date. This avoids copying historical rows or recomputing BBI, EMA, and KDJ from the first bar on every session. NiuOne's four actions also share one ATR, V-pattern, breakout, and structural-risk input, while only the compact theme fields read by the next close are retained across sessions. Built-in stateless scorers skip unneeded pre-signal scoring. NiuOne keeps at least 60 state-replay sessions before the signal and never starts later than the first session that could produce a valid theme member, so only early warmup closes that cannot create theme state are skipped. Sector Tide continues to replay its full warmup range.

The result's rejection-diagnostics section reports stock-session scoring volume, threshold passes, fully actionable candidates, leading hard blockers, and a bounded set of nearest misses by sub-strategy and calendar month. It also reports candidate-count sensitivity at production-threshold offsets `-1/-0.5/-0.25/0/+0.25/+0.5/+1`, one-at-a-time exact and family-level hard-gate rescue counts, and lifecycle/blocker coverage for each concept or industry leader branch. The branch table separates the best candidate's same-day blockers from monthly aggregate blocker-family counts, so a monthly total is not presented as one stock-day's explanation. These are read-only ablations: they do not automatically relax production thresholds, capacity, or risk gates.

`historical_data.py` tries Eastmoney, Tencent, and Sina in order with bounded timeouts, retries, concurrency, and source provenance. The default is forward-adjusted data; Sina is eligible only with `adjustment="none"`. With `strict=True`, any missing symbol aborts the run so a cross-sectional selector is not silently tested on an incomplete universe.

```python
from backtesting import (
    HistoricalFetchConfig,
    NiuOneStrategyBacktestPolicy,
    NiuOneHistoricalContextProvider,
    RegisteredScorerSelector,
    SelectionBacktestConfig,
    load_current_industry_map,
    load_current_theme_map,
    run_historical_selection_backtest,
)

selector = RegisteredScorerSelector(
    ["niu_leader", "niu_pullback", "niu_emerging"],
    context_provider=NiuOneHistoricalContextProvider(
        flow_provider=historical_industry_money_flow,
    ),
    max_signals_per_session=5,
)
run = run_historical_selection_backtest(
    historical_reference_symbols,
    "2026-05-01",
    "2026-06-30",
    selector,
    fetch_config=HistoricalFetchConfig(adjustment="qfq", minimum_rows=55),
    selection_config=SelectionBacktestConfig(cooldown_sessions=0),
    position_exit_strategy=NiuOneStrategyBacktestPolicy(),
    industry_loader=load_current_industry_map,
    theme_loader=load_current_theme_map,
)
```

NiuOne portfolio return starts from the isolated initial cash and includes every risk-sized entry, stage-upgrade add, partial exit, remaining position, and fee. Offensive/recovery-entry Probes first sell 50% at 0.75R, while rotation-entry Probes and mature paths first sell 45% at 1R. All entry and exit legs are combined before slippage, commission, transfer fee, and stamp duty are deducted; from the next session, the runner's structural stop is raised to at least fee-inclusive average cost. Only fully closed holding cycles enter average return and win rate; end-of-period positions remain marked into portfolio equity and show their floating return separately. Locked limit-up, suspended, zero-volume, sub-board-lot risk budgets, and missing-next-session entries retain rejection reasons. Daily bars cannot reconstruct exact intraday ordering: structural stops use the low and fill from the stop or a worse gap open, while the stage-specific R target uses the high and fills from the target or a better gap open; a bar hitting both is handled stop-first, and other completed-bar rules continue to fill at the close.

The administrator page builds its candidate universe from the current A-share listing interfaces. Historical bars prefer Eastmoney, then Tencent; Sina is the third source only for unadjusted requests. NiuOne clusters each stock across its Eastmoney `f103` concepts, while Sector Tide uses the same provider's `f100` industry. No SW or THS classification is read. NiuOne Probe uses only daily bars available at each historical close and is replayed together with the other NiuOne sub-strategies.

## 8. Usage Boundaries

- Do not treat model-generated content as factual. Verify the original data and information sources.
- Do not use this project as a substitute for licensed institutional services, professional risk assessment, or your own independent judgment.
- Historical replay, rule scores, and simulation results do not represent future performance.
- When adding data sources, verify data licensing, request-frequency limits, privacy requirements, and redistribution terms.
