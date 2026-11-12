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
                        format_report, list_models, ordered_contigs, prioritize_contigs, test_connection)
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
    def test_ai_can_approve_a_peak_but_cannot_choose_the_base_or_clear_a_mixed_peak(self):
        project, _ = peak_fixture()
        config = ReviewSettings(key='test-credential', effort='high')
        with patch('llm_review._generate', side_effect=[
                '{"approved_ids":["P00001"]}', '{"priority_ids":["C001"]}']) as generate:
            made, errors, summary, _, _, failure = review_snapshot(project, unassembled_groups(project), config)
        self.assertEqual((errors, failure), ([], None))
        contig = next(iter(made.values()))
        self.assertEqual(contig.columns[3].call, 'N')
        self.assertEqual((contig.call(3), contig.call(6)), ('G', 'N'))
        self.assertEqual(contig.issues(), [6])
        self.assertTrue(contig.ai_unresolved(6))
        self.assertEqual((summary['replaced_n'], summary['red_flags']), (1, 1))
        payload = json.loads(generate.call_args_list[0].args[2])
        self.assertEqual(set(payload[0]), {'id', 'agreeing_traces', 'best_agreeing_phred', 'weakest_peak_ratio'})
        self.assertEqual(len(payload), 1)
        self.assertIn('Check the 1 red flag', format_report(config, summary, ('C001',)))
        key = next(iter(made))
        project.reverse_contig(key)
        self.assertEqual(contig.call(5), 'C')
        self.assertTrue(contig.ai_unresolved(2))

    def test_invalid_decision_preserves_local_assembly_and_flags(self):
        project, _ = peak_fixture()
        with patch('llm_review._generate', return_value='{"approved_ids":["P99999"]}'):
            made, _, _, _, _, failure = review_snapshot(project, unassembled_groups(project),
                                                         ReviewSettings(key='test-credential'))
        contig = next(iter(made.values()))
        self.assertEqual((contig.call(3), contig.call(6), contig.ai_review), ('N', 'N', {}))
        self.assertIn('valid base review', failure)

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
            self.assertEqual(prioritize_contigs(ReviewSettings(OFFLINE, '', 'Japanese'), summary), ())

    def test_report_shows_actionable_warning_and_honest_no_warning_result(self):
        _, _, summary, _ = assemble_snapshot(self.project, unassembled_groups(self.project))
        settings = ReviewSettings(GEMINI, 'gemini-3.5-flash-lite', 'Japanese')
        report = format_report(settings, summary, ('C001',))
        self.assertIn('4本のリードのうち4本を使い、2件', report)
        self.assertIn('[gemini-3.5-flash-lite]', report)
        self.assertIn('C001 の計1か所を手動で確認', report)
        self.assertIn('Open Contig', report)
        self.assertNotIn('C002', report)
        self.assertEqual(len(report.split('\n\n')), 2)

        clean = dict(summary, review_flags=0, flagged_contigs=0, contigs=summary['contigs'][1:])
        self.assertIn('手動確認が必要な警告はありません。', format_report(settings, clean))
        partial = dict(clean, reads_used=2, new_contigs=1, unassembled_reads=2, single_read_contigs=1, failed_groups=1)
        partial_report = format_report(settings, partial, failure=True)
        self.assertIn('4本のリードのうち2本を使い、1件', partial_report)
        self.assertIn('未組み立てリード2本、単一リードのコンティグ1件、組み立て失敗1グループ', partial_report)
        self.assertIn('端末内の結果', partial_report)
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
        order = ('C004', 'C002', 'C005', 'C001', 'C003')
        self.assertEqual(tuple(row['id'] for row in ordered_contigs(rows, order)[:5]), order)
        report = format_report(ReviewSettings(OFFLINE, '', 'Japanese'), summary, order)
        self.assertIn(f'C004、C002、C005、C001、C003（ほか{MAX_ROWS - 3}件）', report)
        self.assertIn(f'計{MAX_ROWS + 2}か所', report)

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

    def test_online_providers_parse_text_and_send_only_quality_summary(self):
        _, _, summary, _ = assemble_snapshot(self.project, unassembled_groups(self.project))
        reply = '{"priority_ids":["C001"]}'
        for provider, model, response, url in (
            (OPENAI, 'gpt-5-mini', {'status': 'completed', 'output': [
                {'type': 'reasoning'}, {'type': 'message', 'content': [{'type': 'output_text', 'text': reply}]}]},
             'https://api.openai.com/v1/responses'),
            (CLAUDE, 'claude-haiku-5-5', {'stop_reason': 'end_turn', 'content': [
                {'type': 'thinking'}, {'type': 'text', 'text': reply}]}, 'https://api.anthropic.com/v1/messages'),
            (GEMINI, 'gemini-3.5-flash-lite', {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
                {'thought': True, 'text': 'ignore'}, {'text': f'```json\n{reply}\n```'}]}}]},
             'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent'),
        ):
            with self.subTest(provider=provider), patch('llm_review._post', return_value=response) as send:
                settings = ReviewSettings(provider, model, 'Japanese', 'test-credential')
                self.assertEqual(prioritize_contigs(settings, summary), ('C001',))
                endpoint, headers, payload = send.call_args.args
                self.assertEqual(endpoint, url)
                if provider == GEMINI:
                    self.assertEqual(headers, {'x-goog-api-key': 'test-credential'})
                else:
                    self.assertEqual(headers['Authorization'], 'Bearer test-credential')
                self.assertNotIn('test-credential', json.dumps(payload))
                if provider == OPENAI:
                    self.assertFalse(payload['store'])
                    self.assertEqual(json.loads(payload['input']), summary)
                elif provider == CLAUDE:
                    self.assertEqual(json.loads(payload['messages'][0]['content']), summary)
                else:
                    self.assertEqual(json.loads(payload['contents'][0]['parts'][0]['text']), summary)

    def test_model_cannot_clear_a_flag_or_add_an_unknown_contig(self):
        _, _, summary, _ = assemble_snapshot(self.project, unassembled_groups(self.project))
        settings = ReviewSettings(key='test-credential')
        for answer in ('{"priority_ids":[]}', '{"priority_ids":["C002"]}', '{"priority_ids":["C999"]}', 'Looks good.'):
            with self.subTest(answer=answer), patch('llm_review._generate', return_value=answer):
                with self.assertRaisesRegex(ReviewError, 'valid review order'):
                    prioritize_contigs(settings, summary)
                self.assertIn('1 flagged site in C001', format_report(settings, summary, failure=True))

    def test_key_is_session_only_and_truncated_report_is_not_presented_as_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mcp.json'
            prefs = ReviewPreferences(path)
            prefs.save(ReviewSettings(key='example-secret'))
            self.assertNotIn('example-secret', path.read_text())
            self.assertEqual(prefs.load().key, '')
        with patch('llm_review._post', return_value={'status': 'incomplete', 'output': []}):
            with self.assertRaisesRegex(ReviewError, 'did not finish'):
                prioritize_contigs(ReviewSettings(key='example-secret'), {'contigs_total': 1, 'contigs': []})

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
