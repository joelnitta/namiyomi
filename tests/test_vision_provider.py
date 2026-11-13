import json
from socket import gaierror
from threading import Event
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from llm_review import (CLAUDE, GEMINI, OPENAI, ReviewError, ReviewSettings, WaveformDecision, _request,
                        review_waveforms)


EVIDENCE = 'Read 1; target AB1 A Q55; center A/C/G/T=600/2/0/5'
REPLY = '{"decisions":[{"id":"P00001","call":"A","confidence":"high"}]}'


def decision(site_id, call, confidence='high'):
    return {'id': site_id, 'call': call, 'confidence': confidence}


class VisionProviderTests(unittest.TestCase):
    def test_each_provider_sends_only_text_measurements(self):
        openai_reply = {'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': REPLY}]}]}
        claude_reply = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': REPLY}]}
        gemini_reply = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': REPLY}]}}]}
        for provider, model, response in (
                (OPENAI, 'gpt-5-mini', openai_reply),
                (CLAUDE, 'claude-haiku-5-5', claude_reply),
                (GEMINI, 'gemini-3.5-flash-lite', gemini_reply)):
            with self.subTest(provider=provider), patch('llm_review._post', return_value=response) as post:
                settings = ReviewSettings(provider, model, key='fake-test-key')
                self.assertEqual(review_waveforms(settings, 1, lambda _: EVIDENCE), {0: WaveformDecision('A', 'high')})
                self.assertEqual(post.call_args.kwargs['timeout'], 300)
                payload = post.call_args.args[2]
                limit = (payload.get('max_output_tokens') or payload.get('max_tokens')
                         or payload['generationConfig']['maxOutputTokens'])
                self.assertEqual(limit, 32768)      # room for reasoning at the default effort too
                if provider == OPENAI:
                    self.assertFalse(payload['store'])
                    prompt = payload['input']
                elif provider == CLAUDE:
                    prompt = payload['messages'][0]['content']
                else:
                    part, = payload['contents'][0]['parts']
                    self.assertEqual(set(part), {'text'})
                    prompt = part['text']
                self.assertIsInstance(prompt, str)
                self.assertIn('Site P00001\n' + EVIDENCE, prompt)
                self.assertNotIn('fake-test-key', json.dumps(payload))

    def test_request_distinguishes_timeout_from_dns_failure(self):
        for error, message in ((TimeoutError('private detail'), 'timed out'),
                               (URLError(TimeoutError('private detail')), 'timed out'),
                               (URLError(gaierror('private detail')), 'look up')):
            with self.subTest(error=type(error)), patch('llm_review.build_opener') as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaisesRegex(ReviewError, message) as caught:
                    _request('https://example.com', {})
                self.assertNotIn('private detail', str(caught.exception))

    def test_batch_requires_one_valid_decision_for_each_exact_id(self):
        first = '{"id":"P00001","call":"A","confidence":"high"}'
        for second in (
                None,
                '{"id":"P00001","call":"N","confidence":"low"}',
                '{"id":"P00003","call":"N","confidence":"low"}',
                '{"id":"P00002","call":"a","confidence":"high"}',
                '{"id":"P00002","call":"AA","confidence":"high"}',
                '{"id":"P00002","call":"R","confidence":"high"}',
                '{"id":"P00002","call":"A","confidence":"certain"}',
                '{"id":"P00002","call":"A"}',
                '{"id":"P00002","call":"N","confidence":"low","reason":"mixed"}',
                '{"id":"P00002","call":"N","call":"G","confidence":"low"}'):
            reply = '{"decisions":[' + ','.join(row for row in (first, second) if row) + ']}'
            with self.subTest(reply=reply), patch('llm_review._generate', return_value=reply):
                progress = Mock()
                with self.assertRaisesRegex(ReviewError, 'chromatogram site'):
                    review_waveforms(ReviewSettings(), 2, lambda _: EVIDENCE, on_batch_done=progress)
                progress.assert_not_called()

    def test_removal_and_confidence_are_returned_as_given(self):
        reply = json.dumps({'decisions': [decision('P00001', '-'), decision('P00002', 'T', 'low')]})
        with patch('llm_review._generate', return_value=reply):
            self.assertEqual(review_waveforms(ReviewSettings(), 2, lambda _: EVIDENCE),
                             {0: WaveformDecision('-', 'high'), 1: WaveformDecision('T', 'low')})

    def test_batches_render_lazily_and_report_only_valid_responses(self):
        rendered = []
        progress = []

        def evidence(index):
            rendered.append(index)
            return EVIDENCE

        def generate(settings, instructions, prompt, *, timeout, token_limit):
            site_id = prompt.split('\n\n')[1].splitlines()[0].removeprefix('Site ')
            self.assertEqual(len(rendered), len(progress) + 1)
            return json.dumps({'decisions': [decision(site_id, 'N', 'low')]})

        with (patch('llm_review.WAVEFORM_BATCH_SIZE', 1), patch('llm_review.WAVEFORM_WORKERS', 1),
              patch('llm_review._generate', side_effect=generate)):
            decisions = review_waveforms(ReviewSettings(), 2, evidence,
                                         on_batch_done=lambda *update: progress.append(update))
        self.assertEqual(decisions, {0: WaveformDecision('N', 'low'), 1: WaveformDecision('N', 'low')})
        self.assertEqual(rendered, [0, 1])
        self.assertEqual(progress, [(1, 2, 1), (2, 2, 2)])

    def test_short_batch_can_finish_first_and_reports_exact_site_count(self):
        second_reported = Event()
        progress = []

        def generate(settings, instructions, prompt, *, timeout, token_limit):
            ids = [part.splitlines()[0].removeprefix('Site ') for part in prompt.split('\n\n')[1:]]
            if len(ids) == 2:
                self.assertTrue(second_reported.wait(2), 'the second batch should finish independently')
            return json.dumps({'decisions': [decision(site_id, 'A') for site_id in ids]})

        def update(done, total, sites):
            progress.append((done, total, sites))
            second_reported.set()

        with (patch('llm_review.WAVEFORM_BATCH_SIZE', 2), patch('llm_review.WAVEFORM_WORKERS', 2),
              patch('llm_review._generate', side_effect=generate)):
            self.assertEqual(review_waveforms(ReviewSettings(), 3, lambda _: EVIDENCE, on_batch_done=update),
                             {index: WaveformDecision('A', 'high') for index in range(3)})
        self.assertEqual(progress, [(1, 2, 1), (2, 2, 3)])

    def test_cancellation_stops_later_rendering_and_requests(self):
        progress = []
        render = Mock(return_value=EVIDENCE)
        with (patch('llm_review.WAVEFORM_BATCH_SIZE', 1), patch('llm_review.WAVEFORM_WORKERS', 1),
              patch('llm_review._generate', return_value=REPLY) as send):
            with self.assertRaisesRegex(ReviewError, 'cancelled'):
                review_waveforms(ReviewSettings(), 2, render,
                                 on_batch_done=lambda *args: progress.append(args),
                                 is_cancelled=lambda: bool(progress))
        render.assert_called_once_with(0)
        send.assert_called_once()

    def test_size_limit_rejects_the_batch_before_network(self):
        with patch('llm_review.MAX_REQUEST_BYTES', len(EVIDENCE)), patch('llm_review.build_opener') as send:
            with self.assertRaisesRegex(ReviewError, 'size limit'):
                review_waveforms(ReviewSettings(key='fake-test-key'), 1, lambda _: EVIDENCE)
        send.assert_not_called()

    def test_a_later_invalid_batch_does_not_return_partial_decisions(self):
        replies = [REPLY, '{"decisions":[{"id":"P00001","call":"G","confidence":"high"}]}']
        progress = Mock()
        with (patch('llm_review.WAVEFORM_BATCH_SIZE', 1), patch('llm_review.WAVEFORM_WORKERS', 1),
              patch('llm_review._generate', side_effect=replies)):
            with self.assertRaisesRegex(ReviewError, 'chromatogram site'):
                review_waveforms(ReviewSettings(), 2, lambda _: EVIDENCE, on_batch_done=progress)
        progress.assert_called_once_with(1, 2, 1)
