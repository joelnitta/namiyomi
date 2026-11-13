from copy import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from auto_assembly import unassembled_groups
from core import Project
from llm_review import (CLAUDE, GEMINI, OFFLINE, OPENAI, ReviewError, ReviewPreferences, ReviewSettings,
                        format_report, list_models, test_connection)
from mcp_dialogs import assemble_snapshot, review_snapshot
from mcp_server import MAX_ROWS, quality_snapshot
from test_core import synthetic

ROOT = Path(__file__).resolve().parents[1]


def peak_fixture():
    project = Project()
    read, trace = synthetic(project, 'TACGACTAC', 'F')
    trace.peaks = [10 + 12 * i for i in range(len(trace.sequence))]
    trace.channels = {base: [0] * 120 for base in 'ACGT'}
    for base, center in zip(trace.sequence, trace.peaks):
        trace.channels[base][center - 1:center + 2] = [300, 1000, 300]
    trace.qualities[3] = 19   # Clear G, narrowly below the default Phred threshold.
    summit = trace.peaks[3]
    trace.channels['G'][summit + 1:summit + 3] = [1000, 300]
    trace.qualities[6] = 15   # Two comparable channels, T and C.
    center = trace.peaks[6]
    trace.channels['C'][center - 1:center + 2] = [240, 800, 240]
    return project, read


class ConsensusReviewTests(unittest.TestCase):
    def review(self, reply, **settings):
        project, _ = peak_fixture()
        config = ReviewSettings(key='test-credential', **settings)
        with patch('llm_review._generate', return_value=reply) as generate:
            made, errors, summary, _, failure = review_snapshot(project, unassembled_groups(project), config)
        self.assertEqual(errors, [])
        return project, config, made, summary, failure, generate

    def test_local_rule_settles_the_clear_peak_and_only_the_mixed_peak_is_sent(self):
        project, config, made, summary, failure, generate = self.review(
            '{"decisions":[{"id":"P00001","call":"N","confidence":"low"}]}', effort='high')
        self.assertIsNone(failure)
        contig = next(iter(made.values()))
        self.assertEqual(contig.columns[3].call, 'N')
        self.assertEqual((contig.call(3), contig.call(6)), ('G', 'N'))
        self.assertEqual(contig.ai_notes['3'], 'local rule: clear peak')
        self.assertEqual(contig.issues(), [6])
        self.assertTrue(contig.ai_unresolved(6))
        review = summary['review']
        self.assertEqual((review['sites'], review['local_resolved'], review['sent'], review['model_resolved'],
                          review['unresolved']), (2, 1, 1, 0, 1))
        self.assertEqual(summary['red_flags'], 1)
        generate.assert_called_once()
        prompt = generate.call_args.args[2]
        self.assertIn('Site P00001', prompt)
        self.assertNotIn('P00002', prompt)
        self.assertIn('T15@+0<-target', prompt)
        self.assertNotIn('G19@+0<-target', prompt)
        self.assertIn('1 red flag', format_report(config, summary))
        key = next(iter(made))
        project.reverse_contig(key)
        self.assertEqual(contig.call(5), 'C')
        self.assertEqual(contig.ai_notes['5'], 'local rule: clear peak')
        self.assertTrue(contig.ai_unresolved(2))

    def test_model_answer_is_applied_only_when_confident_and_supported_by_the_signal(self):
        for call, confidence, expected, counted in (
                ('T', 'high', 'T', 'model_resolved'),       # confirms the read's call; a T summit is there
                ('C', 'high', 'N', 'model_suggested'),      # a C summit exists, but one read cannot overrule itself
                ('G', 'high', 'N', 'model_rejected'),       # no G signal at this column
                ('-', 'high', 'N', 'model_rejected'),       # one read cannot show its own call is spurious
                ('T', 'low', 'N', 'model_declined')):
            with self.subTest(call=call, confidence=confidence):
                reply = '{"decisions":[{"id":"P00001","call":"%s","confidence":"%s"}]}' % (call, confidence)
                _, _, made, summary, failure, _ = self.review(reply)
                contig = next(iter(made.values()))
                self.assertIsNone(failure)
                self.assertEqual(contig.call(6), expected)
                self.assertEqual(summary['review'][counted], 1)
                self.assertEqual(contig.ai_unresolved(6), expected == 'N')
                self.assertTrue(contig.ai_notes['6'].startswith('gpt-6-luna: '))

    def test_failed_online_step_keeps_the_local_decisions(self):
        _, _, made, summary, failure, _ = self.review('{"decisions":[{"id":"P99999","call":"A","confidence":"high"}]}')
        contig = next(iter(made.values()))
        self.assertEqual((contig.call(3), contig.call(6)), ('G', 'N'))
        self.assertEqual(contig.ai_review, {'3': 'G'})
        self.assertEqual(contig.flag_color(6), 'black')
        self.assertEqual(summary['red_flags'], 0)
        self.assertIn('chromatogram site decision', failure)
        self.assertFalse(summary['review']['completed'])

    def test_this_device_only_never_calls_the_network(self):
        project, _ = peak_fixture()
        with patch('llm_review._post', side_effect=AssertionError('offline called the network')):
            made, _, summary, _, failure = review_snapshot(project, unassembled_groups(project),
                                                           ReviewSettings(OFFLINE, ''))
        contig = next(iter(made.values()))
        self.assertIsNone(failure)
        self.assertEqual((contig.call(3), contig.call(6)), ('G', 'N'))
        self.assertEqual(contig.flag_color(6), 'black')
        self.assertEqual((summary['review']['local_resolved'], summary['review']['sent']), (1, 0))
        self.assertIn('Local trace rules settled 1 of 2 flagged columns.',
                      format_report(ReviewSettings(OFFLINE, ''), summary))

    def test_double_peak_setting_calls_iupac_and_keeps_a_review_flag(self):
        project, read = peak_fixture()
        key = project.assemble_reads([read.id], 'Off')
        self.assertEqual(project.contigs[key].call(6), 'N')
        project.settings['call_double_peaks'] = True
        key = project.assemble_reads([read.id], 'On')
        contig = project.contigs[key]
        self.assertEqual((contig.call(6), contig.columns[6].reason), ('Y', 'double_peak'))
        self.assertIn(6, contig.issues())
        self.assertFalse(contig.ai_unresolved(6))
        self.assertEqual(contig.call(3), 'N')
        project.reverse_contig(key)
        self.assertEqual(contig.call(2), 'R')
        self.assertIn(2, contig.issues())
        self.assertTrue(project.edit_read_bases(read.id, [6], 'C'))
        self.assertEqual(contig.call(2), 'G')

    def test_effort_uses_provider_specific_parameters(self):
        for provider, model, expected, response in (
                (OPENAI, 'gpt-6.1-sol', {'reasoning': {'effort': 'high'}},
                 {'status': 'completed', 'output': [{'type': 'message', 'content': [
                     {'type': 'output_text', 'text': 'OK'}]}]}),
                (CLAUDE, 'claude-haiku-5-5', {'output_config': {'effort': 'high'}},
                 {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'OK'}]}),
                (GEMINI, 'gemini-3.5-flash-lite',
                 {'generationConfig': {'maxOutputTokens': 16384, 'thinkingConfig': {'thinkingLevel': 'HIGH'}}},
                 {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': 'OK'}]}}]}),
        ):
            with self.subTest(provider=provider), patch('llm_review._post', return_value=response) as send:
                self.assertEqual(test_connection(ReviewSettings(provider, model, key='test', effort='high')), 'OK')
                for key, value in expected.items():
                    self.assertEqual(send.call_args.args[2][key], value)


class McpQualityTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        added, errors = self.project.add(sorted((ROOT / 'demo').glob('*.ab1')))
        self.assertEqual((len(added), errors), (4, []))

    def test_auto_uses_local_assembler_and_sends_only_metrics(self):
        baseline = self.project.snapshot()
        made, errors, summary, rows = assemble_snapshot(self.project, unassembled_groups(self.project))
        self.assertFalse(errors)
        self.assertEqual((summary['reads'], summary['reads_used'], summary['new_contigs'], summary['unassembled_reads']),
                         (4, 4, 2, 0))
        self.assertEqual((summary['needs_review'], summary['clean'], summary['review_flags']), (1, 1, 1))
        self.assertEqual([row['category'] for row in summary['contigs']], ['needs_review', 'clean'])
        self.assertEqual(set(made), {row['key'] for row in rows})
        wire = json.dumps(summary)
        self.assertNotIn('Demo', wire)
        self.assertNotIn('.ab1', wire)
        for contig in made.values():
            self.assertNotIn(contig.sequence()[:20], wire)
        # Only the controller installs new contigs, with Undo history. The
        # worker can do a second report without rebuilding previous contigs.
        self.project.contigs.update(made)
        first = self.project.snapshot()
        made_again, _, second, _ = assemble_snapshot(self.project, unassembled_groups(self.project))
        self.assertEqual(made_again, {})
        self.assertEqual((second['reads_used'], second['new_contigs']), (0, 0))
        self.assertEqual(self.project.snapshot(), first)
        self.assertEqual(first[0], baseline[0])
        with patch('llm_review._post', side_effect=AssertionError('offline called the network')):
            self.assertIsNone(review_snapshot(self.project, {}, ReviewSettings(OFFLINE, '', 'Japanese'))[-1])

    def test_report_shows_actionable_warning_and_honest_no_warning_result(self):
        _, _, summary, _ = assemble_snapshot(self.project, unassembled_groups(self.project))
        settings = ReviewSettings(GEMINI, 'gemini-3.5-flash-lite', 'Japanese')
        report = format_report(settings, summary)
        self.assertIn('4本のリードのうち4本を使い、2件', report)
        self.assertNotIn('gemini-3.5-flash-lite', report)
        self.assertIn('要確認：1箇所', report)
        self.assertIn('コンティグ名をクリック', report)
        self.assertNotIn('C001', report)
        self.assertEqual(len(report.splitlines()), 2)

        clean = dict(summary, review_flags=0, flagged_contigs=0, contigs=summary['contigs'][1:])
        self.assertIn('手動確認が必要な警告はありません。', format_report(settings, clean))
        partial = dict(clean, reads_used=2, new_contigs=1, unassembled_reads=2, single_read_contigs=1, failed_groups=1)
        partial_report = format_report(settings, partial, failure='Connection failed')
        self.assertIn('4本のリードのうち2本を使い、1件', partial_report)
        self.assertIn('未組み立て2本、単一リードのコンティグ1件、組み立て失敗1グループ', partial_report)
        self.assertIn('Connection failed', partial_report)
        self.assertNotIn('[gemini', partial_report)
        self.assertNotIn('手動確認が必要な警告はありません', partial_report)

    def test_report_counts_all_flags_and_keeps_omitted_contigs_in_local_table(self):
        made, _, _, _ = assemble_snapshot(self.project, unassembled_groups(self.project))
        flagged = next(c for c in made.values() if c.issues())
        self.project.contigs = {}
        for index in range(MAX_ROWS + 2):
            row = copy(flagged)
            row.name = row.sample = f'LocalName{index:03}'
            self.project.contigs[self.project.key(row.sample, row.marker)] = row
        summary, rows = quality_snapshot(self.project)
        self.assertEqual(len(summary['contigs']), MAX_ROWS)
        self.assertEqual(len(rows), MAX_ROWS + 2)
        self.assertNotIn('LocalName', json.dumps(summary))
        report = format_report(ReviewSettings(OFFLINE, '', 'Japanese'), summary)
        self.assertNotIn('C001', report)
        self.assertIn(f'要確認：{MAX_ROWS + 2}箇所', report)

    def test_stdio_inspects_only_the_project_selected_at_launch(self):
        messages = [
            dict(id=1, method='initialize', params=dict(protocolVersion='2025-11-25',
                 capabilities={}, clientInfo=dict(name='test', version='1'))),
            dict(method='notifications/initialized'),
            dict(id=2, method='tools/list'),
            dict(id=3, method='tools/call', params=dict(name='get_quality_summary', arguments={})),
            dict(id=4, method='tools/call', params=dict(name='get_quality_summary', arguments={'project': 'other.json'})),
            dict(id=5, method='tools/call', params=dict(name='apply_review_edits', arguments={})),
        ]
        program = [sys.executable, str(ROOT / 'src/mcp_server.py'), '--project', str(ROOT / 'demo/Demo.json')]
        run = subprocess.run(program, input=''.join(json.dumps(dict(jsonrpc='2.0', **m)) + '\n'
                             for m in messages), capture_output=True, text=True, timeout=15, check=True)
        self.assertEqual(run.stderr, '')
        init, tools, report, wrong_path, write = map(json.loads, run.stdout.splitlines())
        self.assertEqual(init['result']['protocolVersion'], '2025-11-25')
        self.assertEqual([t['name'] for t in tools['result']['tools']], ['get_quality_summary'])
        self.assertTrue(tools['result']['tools'][0]['annotations']['readOnlyHint'])
        self.assertEqual(report['result']['structuredContent']['contigs_total'], 2)
        self.assertNotIn('Demo01', run.stdout)
        self.assertEqual(wrong_path['error']['code'], -32602)
        self.assertEqual(write['error']['code'], -32602)

    def test_key_is_session_only_and_truncated_report_is_not_presented_as_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mcp.json'
            prefs = ReviewPreferences(path)
            prefs.save(ReviewSettings(key='example-secret'))
            self.assertNotIn('example-secret', path.read_text())
            self.assertEqual(prefs.load().key, '')
        with patch('llm_review._post', return_value={'status': 'incomplete', 'output': []}):
            with self.assertRaisesRegex(ReviewError, 'did not finish'):
                test_connection(ReviewSettings(key='example-secret'))

    def test_fresh_preferences_default_to_this_device_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs = ReviewPreferences(Path(tmp) / 'mcp.json')
            self.assertEqual(prefs.load(), ReviewSettings(OFFLINE, ''))
            saved = ReviewSettings(OPENAI, 'my-model', 'Japanese', effort='high')
            prefs.save(saved)
            self.assertEqual(prefs.load(), saved)

    def test_model_picker_loads_future_models_without_sending_project_data(self):
        with patch('llm_review._request', return_value={'data': [
            {'id': 'gpt-future-mini'}, {'id': 'text-embedding-3-small'}]}) as request:
            models = list_models(ReviewSettings(key='test-credential'))
            self.assertEqual(models, ['gpt-future-mini'])
            request.assert_called_once_with('https://api.openai.com/v1/models',
                                            {'Authorization': 'Bearer test-credential'})

        with patch('llm_review._request', side_effect=[
            {'data': [{'id': 'claude-next'}], 'has_more': True, 'last_id': 'page 2'},
            {'data': [{'id': 'claude-next-small'}], 'has_more': False},
        ]) as request:
            models = list_models(ReviewSettings(CLAUDE, 'ignored', key='test-credential'))
            self.assertEqual(models, ['claude-next', 'claude-next-small'])
            self.assertIn('after_id=page+2', request.call_args.args[0])

        with patch('llm_review._request', side_effect=[
            {'models': [{'name': 'models/gemini-next', 'supportedGenerationMethods': ['generateContent']},
                        {'name': 'models/gemini-embed', 'supportedGenerationMethods': ['embedContent']}],
             'nextPageToken': 'next&token'},
            {'models': [{'name': 'models/gemini-new', 'supportedGenerationMethods': ['generateContent']}]},
        ]) as request:
            models = list_models(ReviewSettings(GEMINI, 'ignored', key='test-credential'))
            self.assertEqual(set(models), {'gemini-next', 'gemini-new'})
            self.assertIn('pageToken=next%26token', request.call_args.args[0])
            self.assertEqual(request.call_args.args[1], {'x-goog-api-key': 'test-credential'})
