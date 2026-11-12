"""Provider settings and HTTPS calls for the optional QC report."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from core import atomic_text
from platform_support import application_support_directory

OFFLINE = 'Offline preview'
OPENAI = 'OpenAI (ChatGPT)'
CLAUDE = 'Anthropic (Claude)'
GEMINI = 'Google (Gemini)'
PROVIDERS = (OPENAI, CLAUDE, GEMINI, OFFLINE)
MODELS = {OPENAI: 'gpt-5-mini', CLAUDE: 'claude-haiku-5-5', GEMINI: 'gemini-3.5-flash-lite', OFFLINE: ''}
KEY_ENV = {OPENAI: 'OPENAI_API_KEY', CLAUDE: 'ANTHROPIC_API_KEY', GEMINI: 'GEMINI_API_KEY'}
LANGUAGES = ('English', 'Japanese')
MAX_RESPONSE_BYTES = 512 * 1024
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

    def validate(self):
        if self.provider not in PROVIDERS or self.language not in LANGUAGES:
            raise ValueError('Choose a supported provider and report language.')
        if not isinstance(self.model, str) or (self.provider != OFFLINE and not valid_model(self.provider, self.model)):
            raise ValueError('Enter a valid API model ID.')
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
            if set(raw) == {'provider', 'model', 'language'}:
                settings = ReviewSettings(**raw)
                settings.validate()
                return settings
        except (OSError, ValueError, TypeError, RecursionError):
            pass
        return ReviewSettings()

    def save(self, settings):
        settings.validate()
        atomic_text(self.path, json.dumps(dict(provider=settings.provider, model=settings.model,
                                              language=settings.language), indent=2) + '\n')


class ReviewError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never send credentials or report data to a redirected destination.
        return None


def _request(url, headers, payload=None):
    if payload is None:
        request = Request(url, headers=headers)
    else:
        request = Request(url, data=json.dumps(payload, allow_nan=False).encode('utf-8'),
                          headers={**headers, 'Content-Type': 'application/json'}, method='POST')
    try:
        with build_opener(NoRedirect()).open(request, timeout=60) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        exc.close()
        hints = {
            400: 'Check the API key, model ID and API settings.',
            401: 'Check the API key in MCP Settings.',
            402: 'Check the billing or credit balance for the API account.',
            403: 'The API key may not have access to this model.',
            404: 'Check that the model ID is available to this API account.',
            429: 'Check API usage limits and billing, or try again later.',
        }
        raise ReviewError(f'Provider returned HTTP {exc.code}. '
                          + hints.get(exc.code, 'Try again later or check the provider status.')) from None
    except (URLError, OSError):
        raise ReviewError('Could not reach the provider. Check the internet connection and try again.') from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise ReviewError('The provider response was too large.')
    try:
        return json.loads(data)
    except (ValueError, RecursionError):
        raise ReviewError('The provider returned an unreadable response.') from None


def _post(url, headers, payload):
    return _request(url, headers, payload)


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


def _generate(settings, instructions, prompt):
    settings.validate()
    key = settings.require_key()
    if settings.provider == OPENAI:
        result = _post('https://api.openai.com/v1/responses', {'Authorization': 'Bearer ' + key},
                       dict(model=settings.model, input=prompt, instructions=instructions,
                            store=False, max_output_tokens=1600))
        try:
            if result['status'] != 'completed':
                raise ReviewError('OpenAI did not finish the report. Try again or select another model.')
            parts = [part['text'] for item in result['output'] if item['type'] == 'message'
                     for part in item['content'] if part['type'] == 'output_text']
        except (TypeError, KeyError):
            raise ReviewError('OpenAI returned an unexpected response.') from None
    elif settings.provider == CLAUDE:
        headers = {'Authorization': 'Bearer ' + key, 'anthropic-version': ANTHROPIC_VERSION}
        result = _post('https://api.anthropic.com/v1/messages', headers,
                       dict(model=settings.model, system=instructions, max_tokens=1200,
                            messages=[dict(role='user', content=prompt)]))
        try:
            if result['stop_reason'] != 'end_turn':
                raise ReviewError('Claude did not finish the report. Try again or select another model.')
            parts = [part['text'] for part in result['content'] if part['type'] == 'text']
        except (TypeError, KeyError):
            raise ReviewError('Claude returned an unexpected response.') from None
    else:
        url = f'{GEMINI_MODELS_URL}/{settings.model}:generateContent'
        result = _post(url, {'x-goog-api-key': key}, dict(
            systemInstruction=dict(parts=[dict(text=instructions)]),
            contents=[dict(role='user', parts=[dict(text=prompt)])],
            generationConfig=dict(maxOutputTokens=2400)))
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


MAX_REPORT_IDS = 5


def prioritize_contigs(settings, summary):
    """Ask the model only for review order. The app owns all facts and wording."""
    if settings.provider == OFFLINE or not summary['contigs_total']:
        return ()
    flagged = {row['id'] for row in summary['contigs'] if row['review_flags']}
    count = min(MAX_REPORT_IDS, len(flagged))
    response = _generate(settings,
        'Prioritize Sanger contigs for human review using only the provided quality metrics. '
        f'Return exactly one JSON object with the single key "priority_ids" and an array of {count} '
        'distinct contig IDs, highest priority first. Choose only IDs with review_flags above zero. '
        'Give preference to stale contigs, more flags and weak support. Do not include any prose, '
        'counts, base calls, or claims about accuracy.',
        json.dumps(summary, allow_nan=False))
    fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', response, flags=re.IGNORECASE | re.DOTALL)
    try:
        value = json.loads(fenced[1] if fenced else response)
    except (ValueError, RecursionError):
        value = None
    ids = value.get('priority_ids') if isinstance(value, dict) and set(value) == {'priority_ids'} else None
    if (not isinstance(ids, list) or len(ids) != count
            or any(not isinstance(item, str) or item not in flagged for item in ids)
            or len(set(ids)) != count):
        raise ReviewError('The model did not return a valid review order.')
    return tuple(ids)


def ordered_contigs(rows, priority):
    rank = {identifier: index for index, identifier in enumerate(priority)}
    return sorted(rows, key=lambda row: rank.get(row['id'], len(rank)))


def format_report(settings, summary, priority=(), *, failure=False):
    """Format measured counts and existing flags, including when the API fails."""
    reads, used, built = (summary[k] for k in ('reads', 'reads_used', 'new_contigs'))
    total, unused, single, failed = (summary[k] for k in
        ('contigs_total', 'unassembled_reads', 'single_read_contigs', 'failed_groups'))
    flags, flagged, stale = (summary[k] for k in ('review_flags', 'flagged_contigs', 'stale_contigs'))
    trimmed = summary['fully_trimmed_reads']
    targets = [r['id'] for r in ordered_contigs(summary['contigs'], priority)
               if r['review_flags']][:MAX_REPORT_IDS]
    more = flagged - len(targets)
    online = settings.provider != OFFLINE and not failure

    if settings.language == 'Japanese':
        if built:
            assembly = f'受け取った{reads}本のリードのうち{used}本を使い、{built}件のコンティグを作成しました。'
        elif total:
            assembly = f'読み込み済みの{reads}本を対象にしました。新規作成はなく、既存の{total}件を確認しました。'
        else:
            assembly = f'{reads}本のリードを受け取りましたが、コンティグを作成できませんでした。'
        notes = []
        if unused:
            detail = f'（うちトリミング後に塩基が残らなかったもの{trimmed}本）' if trimmed else ''
            notes.append(f'未組み立てリード{unused}本{detail}')
        if single:
            notes.append(f'単一リードのコンティグ{single}件')
        if failed:
            notes.append(f'組み立て失敗{failed}グループ')
        assembly += '、'.join(notes) + 'がありました。' if notes else '未組み立てリードや単一リードのコンティグはありませんでした。'
        if not total:
            return assembly
        lead = f'[{settings.model}] で品質指標を確認しました。' if online else ''
        if flags:
            ids = '、'.join(targets) + (f'（ほか{more}件）' if more else '')
            review = f'コンティグ {ids} の計{flags}か所を手動で確認してください。［Open Contig］でクロマトグラムを表示できます。'
        else:
            review = '塩基のレビューフラグはありません。' if stale or single or unused or failed else '手動確認が必要な警告はありません。'
        if stale:
            review += f'再組み立てが必要なコンティグが{stale}件あります。'
        fallback = '\nAIレビューを取得できなかったため、端末内の結果を表示しています。' if failure else ''
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
        if failed:
            notes.append(amount(failed, 'failed group'))
        assembly += ' ' + ('Found ' + ', '.join(notes) + '.' if notes else 'No unassembled reads or single-read contigs.')
        if not total:
            return assembly
        lead = f'[{settings.model}] reviewed the quality metrics. ' if online else ''
        if flags:
            ids = ', '.join(targets) + (f' (+{more} more)' if more else '')
            review = f'Manually check {amount(flags, "flagged site")} in {ids}. Choose Open Contig to view the chromatogram.'
        else:
            review = 'No base review flags.' if stale or single or unused or failed else 'No warnings require manual review.'
        if stale:
            review += f' Rebuild required for {amount(stale, "contig")}.'
        fallback = '\nAI review unavailable. Showing local results.' if failure else ''
    return assembly + '\n\n' + lead + review + fallback
