#!/usr/bin/env python3
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHART_UTILS_PATH = ROOT / 'web' / 'src' / 'utils' / 'practiceChart.js'
CHART_COMPONENT_PATH = (
    ROOT / 'web' / 'src' / 'components' / 'practice' / 'PracticeEquityChart.vue'
)
CHART_CSS_PATH = ROOT / 'frontend' / 'dashboard.css'


class PracticeChartFrontendTests(unittest.TestCase):
    def build_chart(self, equity_values, mode='intraday'):
        if mode == 'daily':
            payload = {
                'initial_cash': 1000,
                'current_date': '2026-07-23',
                'equity_history': [],
                'daily_equity_history': [
                    {
                        'time': f'2026-07-{21 + index:02d} 15:00:00',
                        'equity': equity,
                    }
                    for index, equity in enumerate(equity_values)
                ],
            }
        else:
            payload = {
                'initial_cash': 1000,
                'current_date': '2026-07-23',
                'equity_history': [
                    {
                        'time': f'2026-07-23 09:{31 + index:02d}:00',
                        'equity': equity,
                    }
                    for index, equity in enumerate(equity_values)
                ],
                'daily_equity_history': [
                    {'time': '2026-07-22 15:00:00', 'equity': 1000},
                ],
            }
        scenario = f"""
import {{ buildPracticeChartModel }} from {json.dumps(CHART_UTILS_PATH.as_uri())};
const chart = buildPracticeChartModel({json.dumps(payload)}, {json.dumps(mode)});
process.stdout.write(JSON.stringify({{
  bounds: chart.bounds,
  zeroY: chart.zeroY,
  ticks: chart.yTicks,
}}));
"""
        result = subprocess.run(
            ['node', '--input-type=module', '-e', scenario],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_intraday_axis_does_not_expose_padding_across_zero(self):
        positive = self.build_chart([1000.3, 1000.7, 1000.5])
        self.assertEqual(positive['bounds']['min'], 0)
        self.assertTrue(all(tick['value'] >= 0 for tick in positive['ticks']))
        self.assertEqual(sum(tick['isZero'] for tick in positive['ticks']), 1)
        self.assertAlmostEqual(positive['ticks'][-1]['y'], positive['zeroY'])

        negative = self.build_chart([999.7, 999.3, 999.5])
        self.assertEqual(negative['bounds']['max'], 0)
        self.assertTrue(all(tick['value'] <= 0 for tick in negative['ticks']))
        self.assertEqual(sum(tick['isZero'] for tick in negative['ticks']), 1)
        self.assertAlmostEqual(negative['ticks'][0]['y'], negative['zeroY'])

    def test_intraday_axis_keeps_one_zero_tick_for_crossing_and_flat_data(self):
        crossing = self.build_chart([999.5, 1000.6, 1000.2])
        self.assertLess(crossing['bounds']['min'], 0)
        self.assertGreater(crossing['bounds']['max'], 0)
        self.assertEqual(sum(tick['isZero'] for tick in crossing['ticks']), 1)
        zero_tick = next(tick for tick in crossing['ticks'] if tick['isZero'])
        self.assertAlmostEqual(zero_tick['y'], crossing['zeroY'])

        flat = self.build_chart([1000, 1000, 1000])
        self.assertLess(flat['bounds']['min'], 0)
        self.assertGreater(flat['bounds']['max'], 0)
        self.assertEqual(sum(tick['isZero'] for tick in flat['ticks']), 1)

    def test_daily_axis_preserves_padding_for_single_sided_data(self):
        positive = self.build_chart([1000.1, 1000.2], 'daily')
        self.assertLess(positive['bounds']['min'], 0)

        negative = self.build_chart([999.9, 999.8], 'daily')
        self.assertGreater(negative['bounds']['max'], 0)

    def test_axis_labels_and_grid_lines_share_chart_tick_coordinates(self):
        component = CHART_COMPONENT_PATH.read_text(encoding='utf-8')
        css = CHART_CSS_PATH.read_text(encoding='utf-8')

        self.assertGreaterEqual(component.count('v-for="tick in chart.yTicks"'), 2)
        self.assertIn(':style="`top:${tick.y / chart.height * 100}%`"', component)
        self.assertIn(':y1="tick.y"', component)
        self.assertIn(':y2="tick.y"', component)
        self.assertNotIn('practice-zero-axis-label', component)
        self.assertNotIn('practice-axis-label bot', component)
        self.assertNotIn('.practice-axis-label.bot', css)


if __name__ == '__main__':
    unittest.main()
