"""Provider settings and HTTPS calls for the optional base review."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
from socket import gaierror
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from core import atomic_text
from platform_support import application_support_directory

OFFLINE = 'This device only'
LEGACY_PROVIDERS = {'Offline preview': OFFLINE}
OPENAI = 'OpenAI (ChatGPT)'
CLAUDE = 'Anthropic (Claude)'
GEMINI = 'Google (Gemini)'
PROVIDERS = (OPENAI, CLAUDE, GEMINI, OFFLINE)
MODELS = {OPENAI: 'gpt-6-luna', CLAUDE: 'claude-haiku-5-5', GEMINI: 'gemini-3.5-flash-lite', OFFLINE: ''}
KEY_ENV = {OPENAI: 'OPENAI_API_KEY', CLAUDE: 'ANTHROPIC_API_KEY', GEMINI: 'GEMINI_API_KEY'}
LANGUAGES = ('English', 'Japanese')
DEFAULT_EFFORT = 'Default'
EFFORTS = {
    OPENAI: (DEFAULT_EFFORT, 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'),
    CLAUDE: (DEFAULT_EFFORT, 'low', 'medium', 'high', 'xhigh', 'max'),
    GEMINI: (DEFAULT_EFFORT, 'minimal', 'low', 'medium', 'high'),
    OFFLINE: (DEFAULT_EFFORT,),
}
MAX_RESPONSE_BYTES = 512 * 1024
MAX_REQUEST_BYTES = 8 * 1024 * 1024
TRACE_REVIEW_TIMEOUT_SECONDS = 300
# Reasoning counts against the output limit, and reading several traces takes
# more of it than a short report does at any effort level.
TRACE_REVIEW_TOKENS = 32768
ANTHROPIC_VERSION = '2026-10-21'
GEMINI_MODELS_URL = 'https://generativelanguage.googleapis.com/v1beta/models'


def valid_model(provider, model):
    pattern = r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}' if provider == GEMINI else r'[A-Za-z0-9_.:/-]{1,120}'
    return isinstance(model, str) and bool(re.fullmatch(pattern, model))


@dataclass(frozen=True)
class ReviewSettings:
    provider: str = OPENAI
    model: str = MODELS[OPENAI]
    language: str = LANGUAGES[0]
    key: str = field(default='', repr=False, compare=False)
    effort: str = DEFAULT_EFFORT

    def validate(self):
        if self.provider not in PROVIDERS or self.language not in LANGUAGES:
            raise ValueError('Choose a supported provider and report language.')
        if not isinstance(self.model, str) or (self.provider != OFFLINE and not valid_model(self.provider, self.model)):
            raise ValueError('Enter a valid API model ID.')
        if self.effort not in EFFORTS[self.provider]:
            raise ValueError('Choose a supported reasoning effort.')
        if self.key and not re.fullmatch(r'[\x21-\x7e]{1,512}', self.key):
            raise ValueError('The API key must be a single string with no spaces.')

    def api_key(self):
        env = KEY_ENV.get(self.provider)
        return (self.key or os.environ.get(env, '').strip()) if env else ''

    def require_key(self):
        key = self.api_key()
        if not key:
            raise ValueError(f'Enter an API key or set {KEY_ENV[self.provider]}.')
        if not re.fullmatch(r'[\x21-\x7e]{1,512}', key):
            raise ValueError('The API key must be a single string with no spaces.')
        return key


class ReviewPreferences:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('NAMIYOMI_MCP_SETTINGS_PATH') or
                         application_support_directory() / 'mcp-settings.json').expanduser()

    def load(self):
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            fields = {'provider', 'model', 'language'}
            if isinstance(raw, dict) and set(raw) in (fields, fields | {'effort'}):
                raw['provider'] = LEGACY_PROVIDERS.get(raw['provider'], raw['provider'])
                settings = ReviewSettings(**raw)
                settings.validate()
                return settings
        except (OSError, ValueError, TypeError, RecursionError):
            pass
        return ReviewSettings(OFFLINE, MODELS[OFFLINE])

    def save(self, settings):
        settings.validate()
        atomic_text(self.path, json.dumps(dict(provider=settings.provider, model=settings.model,
                                              language=settings.language, effort=settings.effort), indent=2) + '\n')


class ReviewError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never send credentials or report data to a redirected destination.
        return None


def _request(url, headers, payload=None, *, timeout=60):
    if payload is None:
        request = Request(url, headers=headers)
    else:
        body = json.dumps(payload, allow_nan=False).encode('utf-8')
        if len(body) > MAX_REQUEST_BYTES:
            raise ReviewError('The AI review request exceeds the request size limit.')
        request = Request(url, data=body,
                          headers={**headers, 'Content-Type': 'application/json'}, method='POST')
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        exc.close()
        hints = {
            400: 'Check the model ID and reasoning effort. Try Default effort if the model rejects the selected level.',
            401: 'Check the API key in AI Review Settings.',
            402: 'Check the billing or credit balance for the API account.',
            403: 'The API key may not have access to this model.',
            404: 'Check that the model ID is available to this API account.',
            429: 'Check API usage limits and billing, or try again later.',
        }
        raise ReviewError(f'Provider returned HTTP {exc.code}. '
                          + hints.get(exc.code, 'Try again later or check the provider status.')) from None
    except (URLError, OSError) as exc:
        reason = exc.reason if isinstance(exc, URLError) else exc
        if isinstance(reason, TimeoutError):
            raise ReviewError('The provider request timed out before the review completed.') from None
        if isinstance(reason, gaierror):
            raise ReviewError('Could not look up the provider address. Check your internet connection.') from None
        raise ReviewError('Could not reach the provider. Check the internet connection and try again.') from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise ReviewError('The provider response was too large.')
    try:
        return json.loads(data)
    except (ValueError, RecursionError):
        raise ReviewError('The provider returned an unreadable response.') from None


def _post(url, headers, payload, **options):
    return _request(url, headers, payload, **options)


def _catalog_pages(url, headers, *, rows_key, cursor_key, query_key, page_size, more_key=None):
    query = dict(page_size)
    seen = set()
    # Fixed hosts, encoded cursors and bounded pagination, even for bad responses.
    for _ in range(5):
        result = _request(f'{url}?{urlencode(query)}', headers)
        rows = result.get(rows_key) if isinstance(result, dict) else None
        if not isinstance(rows, list):
            raise ReviewError('The provider returned an unexpected model list.')
        yield from rows
        if more_key and not result.get(more_key):
            return
        cursor = result.get(cursor_key)
        if not cursor and not more_key:
            return
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            break
        seen.add(cursor)
        query[query_key] = cursor
    raise ReviewError('Could not finish loading the model list. Try again or enter a model ID.')


def list_models(settings):
    """Fetch the current provider catalog; no inference or project upload."""
    provider = settings.provider
    if provider == OFFLINE:
        return []
    key = settings.require_key()
    bearer = {'Authorization': 'Bearer ' + key}
    if provider == OPENAI:
        result = _request('https://api.openai.com/v1/models', bearer)
        rows = result.get('data') if isinstance(result, dict) else None
        if not isinstance(rows, list):
            raise ReviewError('OpenAI returned an unexpected model list.')
    elif provider == CLAUDE:
        rows = _catalog_pages('https://api.anthropic.com/v1/models',
                              {**bearer, 'anthropic-version': ANTHROPIC_VERSION}, rows_key='data',
                              cursor_key='last_id', query_key='after_id', page_size={'limit': 100}, more_key='has_more')
    else:
        rows = _catalog_pages(GEMINI_MODELS_URL, {'x-goog-api-key': key}, rows_key='models',
                              cursor_key='nextPageToken', query_key='pageToken', page_size={'pageSize': 1000})
    choices = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if provider == GEMINI:
            methods, name = row.get('supportedGenerationMethods'), row.get('name')
            if not isinstance(methods, list) or 'generateContent' not in methods or not isinstance(name, str):
                continue
            model = name.removeprefix('models/')
        else:
            model = row.get('id')
        if not valid_model(provider, model):
            continue
        # Some catalogs include models for speech, images, and embeddings.
        # Their metadata does not consistently describe text output support.
        words = set(model.lower().split('-'))
        if words & {'audio', 'dall', 'embedding', 'image', 'moderation', 'realtime', 'transcribe', 'tts', 'whisper', 'sora', 'veo', 'imagen'}:
            continue
        choices.append(model)
    return list(dict.fromkeys(choices)) if provider == CLAUDE else sorted(set(choices), reverse=True)


def _generate(settings, instructions, prompt, *, timeout=60, token_limit=None):
    settings.validate()
    key = settings.require_key()
    effort = settings.effort if settings.effort != DEFAULT_EFFORT else None
    if token_limit is None:
        token_limit = 16384 if effort in ('high', 'xhigh', 'max') else 4096
    request_options = {'timeout': timeout} if timeout != 60 else {}
    if settings.provider == OPENAI:
        payload = dict(model=settings.model, input=prompt, instructions=instructions,
                       store=False, max_output_tokens=token_limit)
        if effort:
            payload['reasoning'] = {'effort': effort}
        result = _post('https://api.openai.com/v1/responses', {'Authorization': 'Bearer ' + key},
                       payload, **request_options)
        try:
            if result['status'] != 'completed':
                raise ReviewError('OpenAI did not finish the report. Try again or select another model.')
            parts = [part['text'] for item in result['output'] if item['type'] == 'message'
                     for part in item['content'] if part['type'] == 'output_text']
        except (TypeError, KeyError):
            raise ReviewError('OpenAI returned an unexpected response.') from None
    elif settings.provider == CLAUDE:
        headers = {'Authorization': 'Bearer ' + key, 'anthropic-version': ANTHROPIC_VERSION}
        payload = dict(model=settings.model, system=instructions, max_tokens=token_limit,
                       messages=[dict(role='user', content=prompt)])
        if effort:
            payload['output_config'] = {'effort': effort}
        result = _post('https://api.anthropic.com/v1/messages', headers, payload, **request_options)
        try:
            if result['stop_reason'] != 'end_turn':
                raise ReviewError('Claude did not finish the report. Try again or select another model.')
            parts = [part['text'] for part in result['content'] if part['type'] == 'text']
        except (TypeError, KeyError):
            raise ReviewError('Claude returned an unexpected response.') from None
    else:
        url = f'{GEMINI_MODELS_URL}/{settings.model}:generateContent'
        generation = {'maxOutputTokens': token_limit}
        if effort:
            budgets = {'minimal': 512, 'low': 1024, 'medium': 4096, 'high': 8192}
            generation['thinkingConfig'] = (
                {'thinkingBudget': budgets[effort]} if settings.model.startswith('gemini-2.5-')
                else {'thinkingLevel': effort.upper()})
        result = _post(url, {'x-goog-api-key': key}, dict(
            systemInstruction=dict(parts=[dict(text=instructions)]),
            contents=[dict(role='user', parts=[dict(text=prompt)])],
            generationConfig=generation), **request_options)
        try:
            candidate = result['candidates'][0]
            if candidate['finishReason'] != 'STOP':
                raise ReviewError('Gemini did not finish the report. Try again or select another model.')
            parts = [part['text'] for part in candidate['content']['parts']
                     if 'text' in part and not part.get('thought')]
        except (TypeError, KeyError, IndexError):
            raise ReviewError('Gemini returned no usable report.') from None
    text = '\n'.join(part for part in parts if isinstance(part, str)).strip()
    if not text:
        raise ReviewError('The provider returned no report text.')
    return text


def test_connection(settings):
    return _generate(settings, 'This is a connection test. Reply with the single word OK.', 'Connection test.')


WAVEFORM_BATCH_SIZE = 4
WAVEFORM_WORKERS = 4
REMOVE = '-'          # reply value for "this column holds no base"
CALLS = ('A', 'C', 'G', 'T', REMOVE, 'N')
CONFIDENCE = ('high', 'low')

WAVEFORM_INSTRUCTIONS = (
    'You read Sanger chromatograms the way an experienced curator does. Each site is one consensus '
    'column that local rules could not settle. For every read that covers the column you receive '
    'the aligned base calls (base, Phred) and the four-channel signal sampled along the scan axis. '
    'Everything is already oriented to the consensus: x is the distance in scans from the column, '
    'negative x lies toward the consensus start. Intensities are scaled per read so that 100 is the '
    'typical height of a called peak near the site; compare heights only within one read. '
    '"calls at x" gives where the base caller placed each call.\n\n'
    'Decide what the column holds: A, C, G or T; "-" when it holds no base (a spurious call, or the '
    'read with the gap is right); N when the signal does not settle it.\n\n'
    'How to weigh the evidence:\n'
    '1. A base is supported by a summit in its own channel near x=0 that stands above the other '
    'channels at that scan. The rising or falling flank of a neighbouring peak is not a summit.\n'
    '2. Reads are not equal. A read whose nearby calls have high Phred and evenly spaced, well '
    'separated peaks outranks a read in a degraded stretch (broad merging peaks, falling Phred, '
    'uneven spacing), whatever the Phred of the single call in the column.\n'
    '3. When one read has a base and another has a gap, compare how many bases fit between the same '
    'flanking calls in each read. In the read with the gap, flanking calls about one spacing apart '
    'leave no room for another base; about two spacings apart, a base was probably missed. Prefer '
    'the count from the read whose peaks in that stretch are individually resolved, with valleys '
    'between summits.\n'
    '4. The base caller usually counts the bases of a run correctly even where the peaks merge into '
    'a plateau or sit closer than one spacing. In a column covered by a single read, do not answer '
    '"-" because two calls share a broad peak. Answer "-" only when another read with resolved, '
    'evenly spaced peaks shows there is no room for the base.\n'
    '5. Low-Phred call in a single read: confirm the called base when its channel has a distinct '
    'summit close to x=0 and no other channel has a summit of its own there above about half its '
    'height. Two channels with comparable summits at the same place mean a mixed signal: answer N. '
    'A different base may be called only when the called channel has no summit and exactly one other '
    'channel has a clear one.\n'
    '6. Use only the measurements given. Never infer a base from sequence patterns, motifs or '
    'reading frames.\n\n'
    'Give confidence "high" only when you would be surprised to be wrong; otherwise "low". '
    'Return only the requested JSON, without commentary.'
)


@dataclass(frozen=True)
class WaveformDecision:
    call: str
    confidence: str


def _waveform_decisions(response, site_ids):
    def unique_fields(pairs):
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError('Duplicate JSON field.')
        return fields

    fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', response, flags=re.IGNORECASE | re.DOTALL)
    try:
        value = json.loads(fenced[1] if fenced else response, object_pairs_hook=unique_fields)
    except (ValueError, RecursionError):
        value = None
    rows = value.get('decisions') if isinstance(value, dict) and set(value) == {'decisions'} else None
    if not isinstance(rows, list) or len(rows) != len(site_ids):
        raise ReviewError('The model did not return a valid decision for every chromatogram site.')
    decisions = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'id', 'call', 'confidence'}:
            raise ReviewError('The model returned an invalid chromatogram site decision.')
        site_id, call, confidence = row['id'], row['call'], row['confidence']
        if (not isinstance(site_id, str) or site_id not in site_ids or site_id in decisions
                or call not in CALLS or confidence not in CONFIDENCE):
            raise ReviewError('The model returned an invalid or duplicate chromatogram site decision.')
        decisions[site_id] = WaveformDecision(call, confidence)
    return decisions


def review_waveforms(settings, count, evidence, *, on_batch_done=None, is_cancelled=None):
    """Review every site using text evidence with a bounded number of requests in flight.

    Returns ``{site index: WaveformDecision}``. The caller decides which
    decisions to apply; nothing here changes a project.
    """
    if type(count) is not int or count < 0:
        raise ValueError('Chromatogram site count must be a non-negative integer.')
    if count and settings.provider == OFFLINE:
        raise ReviewError('Select an online model to review chromatogram evidence.')

    def check_cancelled():
        if is_cancelled is not None and is_cancelled():
            raise ReviewError('Chromatogram review cancelled.')

    total_batches = (count + WAVEFORM_BATCH_SIZE - 1) // WAVEFORM_BATCH_SIZE
    starts = iter(range(0, count, WAVEFORM_BATCH_SIZE))
    results = Queue()
    decisions = {}
    pending = completed = 0

    def request(site_ids, prompt):
        try:
            check_cancelled()
            reply = _generate(settings, WAVEFORM_INSTRUCTIONS, prompt, timeout=TRACE_REVIEW_TIMEOUT_SECONDS,
                              token_limit=TRACE_REVIEW_TOKENS)
            batch = {site_ids[site_id]: decision
                     for site_id, decision in _waveform_decisions(reply, site_ids).items()}
        except Exception as exc:
            results.put((None, exc))
        else:
            results.put((batch, None))

    def start_batch(start):
        site_ids = {f'P{index + 1:05}': index for index in range(start, min(start + WAVEFORM_BATCH_SIZE, count))}
        entries = []
        for site_id, index in site_ids.items():
            check_cancelled()
            text = evidence(index)
            if not isinstance(text, str) or not text.strip():
                raise ReviewError('Could not prepare chromatogram evidence for every requested site.')
            entries.append(f'Site {site_id}\n{text}')
        check_cancelled()
        example = json.dumps({'decisions': [{'id': next(iter(site_ids)), 'call': 'N', 'confidence': 'low'}]},
                             separators=(',', ':'))
        prompt = (f'Return JSON shaped like {example} with one entry for each site ID below, exactly once. '
                  '"call" is A, C, G, T, - or N. "confidence" is high or low.\n\n'
                  + '\n\n'.join(entries))
        Thread(target=request, args=(site_ids, prompt), daemon=True).start()

    while completed < total_batches:
        check_cancelled()
        while pending < WAVEFORM_WORKERS:
            start = next(starts, None)
            if start is None:
                break
            start_batch(start)
            pending += 1
        try:
            batch, error = results.get(timeout=0.25)
        except Empty:
            continue
        pending -= 1
        if error is not None:
            raise error
        decisions.update(batch)
        completed += 1
        if on_batch_done is not None:
            on_batch_done(completed, total_batches, len(decisions))
        check_cancelled()
    return decisions


def _format_review(review, model, *, online, japanese):
    """Summarize the local and model decisions and the total removals."""
    sites, local, removed = review['sites'], review['local_resolved'], review['removed']
    sent, settled = review['sent'], review['model_resolved']
    if japanese:
        if not sites:
            return '自動レビューの対象はありませんでした。' + ('AIへの送信はありません。' if online else '')
        text = f'ローカル波形ルール：フラッグ{sites}箇所のうち{local}箇所を確定。'
        removals = f'余分な塩基を合計{removed}箇所削除しました。' if removed else ''
        if not online:
            return text + removals
        if not sent:
            return text + 'AIへの送信はありません。' + removals
        if not review['completed']:
            return text + (f'{model}：残り{sent}箇所のうち{review["replied"]}箇所で応答したところで中断。'
                           'AIの判定は反映していません。') + removals
        return text + f'{model}：残り{sent}箇所を確認し、{settled}箇所を確定。' + removals

    def amount(number, noun):
        return f'{number} {noun}' + ('' if number == 1 else 's')

    if not sites:
        return 'No flagged columns were eligible for automatic review.' + (' No API request was sent.' if online else '')
    text = f'Local trace rules settled {local} of {amount(sites, "flagged column")}.'
    removals = f' Removed {amount(removed, "extra base")} in total.' if removed else ''
    if not online:
        return text + removals
    if not sent:
        return text + ' No API request was sent.' + removals
    if not review['completed']:
        return text + (f' [{model}] replied for {review["replied"]} of the other {sent} before the review stopped. '
                       'No model decisions were applied.') + removals
    return text + f' {model} reviewed the other {sent} and settled {settled}.' + removals


def format_report(settings, summary, *, failure=None):
    """Format measured counts and existing flags, including when the API fails."""
    reads, used, built = (summary[k] for k in ('reads', 'reads_used', 'new_contigs'))
    total, unused, single, failed = (summary[k] for k in
        ('contigs_total', 'unassembled_reads', 'single_read_contigs', 'failed_groups'))
    flags, stale = (summary[k] for k in ('review_flags', 'stale_contigs'))
    red = summary.get('red_flags', 0)
    trimmed = summary['fully_trimmed_reads']
    model_label = settings.model if settings.effort == DEFAULT_EFFORT else f'{settings.model} ({settings.effort})'
    counts = summary.get('review')
    base_report = (_format_review(counts, model_label, online=settings.provider != OFFLINE,
                                  japanese=settings.language == 'Japanese')
                   if counts is not None else None)

    if settings.language == 'Japanese':
        if built:
            assembly = f'{reads}本のリードのうち{used}本を使い、{built}件のコンティグを作成しました。'
        elif total:
            assembly = f'既存の{total}コンティグを確認しました。'
        else:
            assembly = f'{reads}本のリードを受け取りましたが、コンティグを作成できませんでした。'
        notes = []
        if unused:
            detail = ('（トリミング後に塩基なし）' if trimmed == unused else
                      f'（うち{trimmed}本はトリミング後に塩基なし）' if trimmed else '')
            notes.append(f'未組み立て{unused}本{detail}')
        if single:
            notes.append(f'単一リードのコンティグ{single}件')
        if failed and trimmed != unused:
            notes.append(f'組み立て失敗{failed}グループ')
        if notes:
            assembly += '、'.join(notes) + '。'
        if not total:
            return '\n'.join(line for line in (assembly, failure) if line)
        if flags:
            warning = f'赤フラッグ{red}箇所' if red == flags else f'{flags}箇所（うち赤フラッグ{red}箇所）' if red else f'{flags}箇所'
            review = f'要確認：{warning}。表のコンティグ名をクリックすると波形を表示します。'
        else:
            review = '手動確認が必要な塩基のフラッグはありません。' if stale or single or unused or failed else '手動確認が必要な警告はありません。'
        if stale:
            review += f'再組み立てが必要なコンティグが{stale}件あります。'
    else:
        def amount(number, noun):
            return f'{number} {noun}' + ('' if number == 1 else 's')

        if built:
            assembly = f'Used {used} of {amount(reads, "read")} to build {amount(built, "contig")}.'
        elif total:
            assembly = f'Checked {amount(total, "existing contig")} from {amount(reads, "loaded read")}. No new contigs were built.'
        else:
            assembly = f'Received {amount(reads, "read")} but could not build any contigs.'
        notes = []
        if unused:
            detail = f' ({trimmed} with no bases left after trimming)' if trimmed else ''
            notes.append(amount(unused, 'unassembled read') + detail)
        if single:
            notes.append(amount(single, 'single-read contig'))
        if failed and trimmed != unused:
            notes.append(amount(failed, 'failed group'))
        if notes:
            assembly += ' ' + ', '.join(notes).capitalize() + '.'
        if not total:
            return '\n'.join(line for line in (assembly, failure) if line)
        if flags:
            warning = (amount(red, 'red flag') if red == flags else
                       f'{amount(flags, "site")} ({amount(red, "red flag")})' if red else amount(flags, 'site'))
            review = f'To review: {warning}. Click a contig name to view the trace.'
        else:
            review = 'No base flags require manual review.' if stale or single or unused or failed else 'No warnings require manual review.'
        if stale:
            review += f' Rebuild required for {amount(stale, "contig")}.'
    return '\n'.join(line for line in (assembly, base_report, review, failure) if line)
