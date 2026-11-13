import unittest
from unittest.mock import patch

from auto_assembly import unassembled_groups
from llm_review import OFFLINE, ReviewError, ReviewSettings, format_report
from mcp_dialogs import review_snapshot
from test_mcp_quality import peak_fixture


def reply(*decisions):
    rows = ','.join('{"id":"P%05d","call":"%s","confidence":"%s"}' % (number, call, confidence)
                    for number, (call, confidence) in enumerate(decisions, 1))
    return '{"decisions":[' + rows + ']}'


class ReviewReportingTests(unittest.TestCase):
    def setUp(self):
        self.project, self.read = peak_fixture()
        self.config = ReviewSettings(language='Japanese', key='test-credential')

    def mix(self, index):
        """Give a second call a competing peak so the local rules leave it open."""
        trace = self.project.traces[self.read.id]
        trace.qualities[index] = 15
        other = 'A' if trace.sequence[index] != 'A' else 'G'
        centre = trace.peaks[index]
        trace.channels[other][centre - 1:centre + 2] = [240, 800, 240]

    def run_review(self, responses, config=None):
        config = config or self.config
        with patch('llm_review._generate', side_effect=responses) as generate:
            result = review_snapshot(self.project, unassembled_groups(self.project), config)
        made, _, summary, _, failure = result
        return made, summary, format_report(config, summary, failure=failure), generate

    def test_report_separates_local_rules_from_the_model(self):
        self.mix(1)
        _, summary, report, generate = self.run_review([reply(('A', 'high'), ('N', 'low'))])
        self.assertEqual(summary['review']['model_resolved'], 1)
        self.assertIn('ローカル波形ルール：フラッグ3箇所のうち1箇所を確定。', report)
        self.assertIn('gpt-6-luna：残り2箇所を確認し、1箇所を確定。', report)
        self.assertIn('赤フラッグ1箇所', report)
        generate.assert_called_once()

    def test_english_report_and_removed_bases(self):
        config = ReviewSettings(key='test-credential', effort='high')
        _, summary, report, _ = self.run_review([reply(('N', 'low'))], config)
        self.assertIn('Local trace rules settled 1 of 2 flagged columns. '
                      'gpt-6-luna (high) reviewed the other 1 and settled 0.', report)
        review = dict(summary['review'], removed=2)
        self.assertIn('settled 0. Removed 2 extra bases in total.', format_report(config, dict(summary, review=review)))
        self.assertIn('0箇所を確定。余分な塩基を合計2箇所削除しました。',
                      format_report(self.config, dict(summary, review=review)))

    def test_no_requests_when_nothing_is_flagged(self):
        self.project.traces[self.read.id].qualities[3] = 40
        self.project.traces[self.read.id].qualities[6] = 40
        key = self.project.assemble_reads([self.read.id], 'Demo')
        self.project.contigs[key].black_marks['6'] = True
        _, _, report, generate = self.run_review([])
        generate.assert_not_called()
        self.assertIn('自動レビューの対象はありませんでした。AIへの送信はありません。', report)

    def test_no_requests_when_local_rules_settle_everything(self):
        trace = self.project.traces[self.read.id]
        centre = trace.peaks[6]
        trace.channels['C'][centre - 1:centre + 2] = [0, 0, 0]
        made, summary, report, generate = self.run_review([])
        generate.assert_not_called()
        self.assertEqual(next(iter(made.values())).issues(), [])
        self.assertIn('ローカル波形ルール：フラッグ2箇所のうち2箇所を確定。AIへの送信はありません。', report)
        self.assertIn('手動確認が必要な塩基のフラッグはありません。', report)

    def test_failed_batch_keeps_local_decisions_and_reports_the_interruption(self):
        self.mix(1)
        with patch('llm_review.WAVEFORM_BATCH_SIZE', 1), patch('llm_review.WAVEFORM_WORKERS', 1):
            made, summary, report, generate = self.run_review([
                reply(('A', 'high')), ReviewError('Connection failed')])
        self.assertEqual(generate.call_count, 2)
        contig = next(iter(made.values()))
        self.assertEqual((contig.call(1), contig.call(3), contig.call(6)), ('N', 'G', 'N'))
        self.assertIn('残り2箇所のうち1箇所で応答したところで中断', report)
        self.assertIn('AIの判定は反映していません', report)
        self.assertIn('Connection failed', report)
        self.assertEqual((contig.flag_color(1), contig.flag_color(6)), ('black', 'black'))
        self.assertEqual(summary['red_flags'], 0)
        self.assertNotIn('赤フラッグ', report)

    def test_this_device_only_report_does_not_mention_a_model(self):
        made, summary, report, generate = self.run_review([], ReviewSettings(OFFLINE, '', 'Japanese'))
        generate.assert_not_called()
        self.assertEqual(next(iter(made.values())).flag_color(6), 'black')
        self.assertEqual(summary['red_flags'], 0)
        self.assertIn('ローカル波形ルール：フラッグ2箇所のうち1箇所を確定。', report)
        self.assertNotIn('AI', report.splitlines()[1])


if __name__ == '__main__':
    unittest.main()
