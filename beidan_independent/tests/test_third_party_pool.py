import unittest
from beidan_bd1.third_party_pool import parse_pool_html


def page(line='-1', period='26103', row_count=1):
    row = f'''<tr class="vs_lines" fid="100" value="{{index:'18',leagueName:'德乙',homeTeam:'甲',guestTeam:'乙',endTime:'2026-10-10 00:20',rangqiuNum:'{line}'}}">
      <td title="比赛时间：00:30"></td>
      <td class="tr"><a href="https://liansai.500.com/team/1/" title="甲">甲</a></td>
      <td class="tl"><a href="https://liansai.500.com/team/2/" title="乙">乙</a></td>
      <td><span class="pjoz">2.50</span><span class="sp_value">4.00</span></td></tr>'''
    return (f'<select id="expect_select"><option selected value="26103">当前期</option></select>'
            f'<input id="expect" value="{period}">'+row*row_count).encode('gbk')


class ThirdPartyPoolTest(unittest.TestCase):
    def test_cutoff_is_not_kickoff_or_official_identity(self):
        rows, report = parse_pool_html(page())
        row=rows[0]
        self.assertEqual(row['sale_cutoff_local'], '2026-10-10 00:20')
        self.assertEqual(row['kickoff_clock_only'], '00:30')
        self.assertIsNone(row['kickoff_at'])
        self.assertEqual(row['reference_sp_observed'], ['4.00'])
        self.assertEqual(row['average_odds_observed'], ['2.50'])
        self.assertFalse(row['official_source'])
        self.assertFalse(row['canonical_identity_approved'])
        self.assertEqual(report['model_imported_n'], 0)

    def test_period_duplicate_and_half_goal_market_reject(self):
        for raw in [page(period='26102'),page(row_count=2),page(line='-0.5')]:
            with self.assertRaises(ValueError): parse_pool_html(raw)

    def test_corrupt_encoding_or_script_metadata_is_not_repaired(self):
        with self.assertRaises(UnicodeDecodeError):parse_pool_html(page()+b'\xff')
        with self.assertRaises(ValueError):
            parse_pool_html(page().replace(b"rangqiuNum:'-1'",b"rangqiuNum:execute()"))
