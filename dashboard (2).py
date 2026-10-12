"""Run in Colab after installing pandas, openpyxl, xlrd, and pyxlsb."""
import base64
import json
import os
import re
from collections import Counter
from pathlib import Path

import pandas as pd

# Files uploaded in Colab's Files panel live under /content.
# Set FILE_PATHS to an explicit list if you want only particular files.
FILE_PATHS = []
SEARCH_DIRS = [Path('/content'), Path.cwd()]
# For Drive files, mount Drive first and add their folder to SEARCH_DIRS.
SUPPORTED = {'.xlsx', '.xls', '.xlsm', '.xlsb', '.csv'}
ALIASES = {
    'ticker': ['ticker', 'ticker symbol', 'symbol', 'stock symbol', 'security ticker', 'bloomberg ticker', 'code'],
    'identifier': ['isin', 'isin code', 'cusip', 'cusip number', 'sedol', 'security id', 'bond code'],
    'name': ['name', 'company', 'company name', 'security', 'security name', 'security description', 'description', 'asset name', 'issuer', 'issuer name', 'fund', 'fund name', 'bond name', 'stock name', 'etf name'],
    'type': ['type', 'asset type', 'asset class', 'class', 'security type', 'instrument type'],
    'price': ['price', 'last price', 'market price', 'reference price', 'close', 'closing price'],
}


def normalized(value):
    return re.sub(r'[^a-z0-9]+', ' ', str(value).lower()).strip()


def clean(value):
    return '' if pd.isna(value) else str(value).strip()


def column_map(row):
    result = {}
    values = [normalized(clean(value)) for value in row]
    for field, aliases in ALIASES.items():
        for alias in aliases:
            if alias in values:
                result[field] = values.index(alias)
                break
    return result


def asset_type(value):
    text = normalized(value)
    if re.search(r'\b(etfs?|etds?|exchange traded funds?)\b', text):
        return 'ETF'
    if re.search(r'\b(bonds?|fixed income|debt|treasur(?:y|ies))\b', text):
        return 'Bond'
    if re.search(r'\b(stocks?|equities|equity|shares?)\b', text):
        return 'Stock'
    return None


def discover_files():
    if FILE_PATHS:
        return sorted({Path(p).expanduser().resolve() for p in FILE_PATHS})
    paths = set()
    for folder in SEARCH_DIRS:
        if folder.is_dir():
            # Scan uploaded files and subfolders, excluding Colab samples and mounted Drive.
            for directory, subfolders, filenames in os.walk(folder):
                subfolders[:] = [name for name in subfolders if not name.startswith('.') and name not in {'sample_data', 'drive', 'node_modules'}]
                for filename in filenames:
                    path = Path(directory) / filename
                    if path.suffix.lower() in SUPPORTED and not filename.startswith('~$'):
                        paths.add(path.resolve())
    return sorted(paths)


def parse_sheet(raw, filename, sheet_name):
    if raw.empty:
        return [], 'Empty worksheet.'
    # Locate a header even when a title or notes precede the table.
    candidates = []
    for row_index in range(min(50, len(raw))):
        mapping = column_map(raw.iloc[row_index])
        if any(key in mapping for key in ('ticker', 'identifier', 'name')):
            candidates.append((len(mapping), -row_index, mapping))
    if not candidates:
        return [], 'No recognizable asset header in the first 50 rows. Update ALIASES to match your columns.'
    _, negative_index, mapping = max(candidates, key=lambda item: (item[0], item[1]))
    header_index = -negative_index
    assets = []
    skipped = 0
    for row_index in range(header_index + 1, len(raw)):
        row = raw.iloc[row_index]
        if all(not clean(value) for value in row):
            continue
        # Ignore repeated table headers without discarding assets with the same ticker.
        if all(normalized(clean(row[col])) == normalized(clean(raw.iloc[header_index, col])) for col in mapping.values()):
            continue
        values = {key: clean(row[col]) for key, col in mapping.items()}
        ticker = values.get('ticker', '')
        identifier = values.get('identifier', '')
        name = values.get('name', '')
        if not (ticker or identifier or name):
            skipped += 1
            continue
        kind = asset_type(values.get('type', '')) or asset_type(sheet_name) or asset_type(Path(filename).stem) or 'Other'
        assets.append({
            'ticker': ticker or identifier or f'ROW-{row_index + 1}',
            'identifier': identifier,
            'name': name or ticker or identifier,
            'type': kind,
            'originalType': values.get('type', ''),
            'price': values.get('price', '') or 'Not supplied',
            'file': str(filename), 'sheet': str(sheet_name), 'row': row_index + 1,
            'articles': [],
        })
    note = f'{skipped} nonempty rows without an asset name or identifier skipped.' if skipped else ''
    return assets, note


def load_assets(paths):
    assets, report = [], []
    for path in paths:
        path = Path(path)
        try:
            if path.suffix.lower() == '.csv':
                sheets = {'CSV': pd.read_csv(path, header=None, dtype=str, keep_default_na=False)}
                workbook = None
            else:
                workbook = pd.ExcelFile(path)
                sheets = dict.fromkeys(workbook.sheet_names)
            try:
                for sheet_name, raw in sheets.items():
                    try:
                        if raw is None:
                            raw = pd.read_excel(workbook, sheet_name=sheet_name, header=None, dtype=str, keep_default_na=False)
                        imported, note = parse_sheet(raw, path.name, sheet_name)
                        assets.extend(imported)
                        report.append(f'{path.name} / {sheet_name}: {len(imported)} assets. {note}'.strip())
                    except Exception as error:
                        report.append(f'WARNING — {path.name} / {sheet_name}: {error}')
            finally:
                if workbook is not None:
                    workbook.close()
        except Exception as error:
            report.append(f'WARNING — {path.name}: {error}')
    for index, asset in enumerate(assets):
        asset['id'] = str(index)
    return assets, report




"""Live Colab news dashboard. Run after installing news-requirements.txt."""
import base64
import calendar
import hashlib
import ipaddress
import json
import secrets
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import feedparser
import requests
import trafilatura
import urllib3
from flask import Flask, jsonify, request


UTC = timezone.utc
MODEL_NAME = 'ProsusAI/finbert'
CLASSIFICATION_MODE = 'positive-negative-v1'
WINDOWS = {'1d': '1 day', '6d': '6 days', '1m': '1 month', '6m': '6 months', '1y': '1 year'}
MAX_TEXT_TOKENS = 4096
MAX_FEED_QUERIES = 24
MAX_DOWNLOAD_BYTES = 3_000_000


def window_bounds(window, now=None):
    now = now or datetime.now(UTC)
    now = now.astimezone(UTC)
    if window in {'1d', '6d'}:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start - timedelta(days=5 if window == '6d' else 0), now
    months = {'1m': 1, '6m': 6, '1y': 12}.get(window)
    if months is None:
        raise ValueError('Choose 1 day, 6 days, 1 month, 6 months or 1 year.')
    month_index = now.year * 12 + now.month - 1 - months
    year, month = divmod(month_index, 12)
    month += 1
    day = min(now.day, calendar.monthrange(year, month)[1])
    return now.replace(year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0), now


def safe_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Use a public http:// or https:// article URL without credentials.')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if port not in {80, 443}:
        raise ValueError('Article URLs must use port 80 or 443.')
    addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('Private and local network URLs are not supported.')
    return parsed, port, addresses


def fetch_public_html(url):
    """Pin the connection to a checked public IP; validate every redirect."""
    for _ in range(6):
        parsed, port, addresses = safe_url(url)
        hostname = parsed.hostname.encode('idna').decode('ascii')
        kwargs = {'host': addresses[0], 'port': port, 'timeout': urllib3.Timeout(connect=6, read=12)}
        if parsed.scheme == 'https':
            pool = urllib3.HTTPSConnectionPool(**kwargs, server_hostname=hostname, assert_hostname=hostname, cert_reqs='CERT_REQUIRED')
        else:
            pool = urllib3.HTTPConnectionPool(**kwargs)
        response = None
        try:
            path = parsed.path or '/'
            if parsed.query:
                path += '?' + parsed.query
            response = pool.urlopen('GET', path, headers={'Host': parsed.netloc, 'User-Agent': 'Mozilla/5.0 (compatible; PortfolioNewsReader/1.0)', 'Accept-Encoding': 'identity'}, redirect=False, retries=False, preload_content=False)
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get('Location')
                if not location:
                    raise ValueError('The publisher returned an empty redirect.')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f'Publisher returned HTTP {response.status}.')
            content_type = response.headers.get('Content-Type', '').lower()
            if 'text/html' not in content_type and 'application/xhtml+xml' not in content_type:
                raise ValueError('The URL did not return an HTML article.')
            data = bytearray()
            for chunk in response.stream(65536, decode_content=True):
                data.extend(chunk)
                if len(data) > MAX_DOWNLOAD_BYTES:
                    raise ValueError('Article exceeds the 3 MB download limit.')
            return bytes(data), url
        finally:
            if response is not None:
                response.close()
            pool.close()
    raise ValueError('Too many redirects.')


def extract_article(url):
    data, final_url = fetch_public_html(url)
    result = trafilatura.bare_extraction(data, url=final_url, with_metadata=True, include_comments=False, include_tables=False)
    if result is None or len((result.text or '').strip()) < 200:
        raise ValueError('Readable article text was not available. Paste the article text below instead.')
    return {'text': result.text.strip(), 'title': result.title or urlsplit(final_url).hostname, 'url': final_url}


def get_feed(url):
    response = requests.get(url, timeout=(6, 20), headers={'User-Agent': 'PortfolioNewsReader/1.0'})
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if feed.bozo and not feed.entries:
        raise ValueError('The news source returned an unreadable feed.')
    if not feed.entries and not feed.get('feed'):
        raise ValueError('The news source did not return an RSS feed.')
    return feed.entries


def collect_news(asset, window, progress=lambda message: None, now=None, fetch_feed=None):
    fetch_feed = fetch_feed or get_feed
    start, end = window_bounds(window, now)
    articles, seen, warnings = [], set(), []
    successful_feeds = 0
    invalid_dates = 0
    name = asset['name'].replace('"', '').strip()
    ticker = asset['ticker'].replace('"', '').strip()
    terms = f'"{name}"'
    if ticker and not ticker.startswith('ROW-') and ticker != name:
        terms = f'({terms} OR "{ticker}")'
    context = '(bond OR debt OR yield)' if asset['type'] == 'Bond' else '(stock OR fund OR shares OR earnings)'
    urls = []
    # Monthly-sized searches improve historical reach, without implying exhaustiveness.
    cursor = start
    while cursor <= end and len(urls) < MAX_FEED_QUERIES - 1:
        stop = min(cursor + timedelta(days=30), end + timedelta(seconds=1))
        query = f'{terms} {context} after:{(cursor - timedelta(days=1)):%Y-%m-%d} before:{(stop + timedelta(days=1)):%Y-%m-%d}'
        urls.append('https://news.google.com/rss/search?' + urlencode({'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'}))
        cursor = stop
    if ticker and not ticker.startswith('ROW-'):
        urls.append('https://feeds.finance.yahoo.com/rss/2.0/headline?' + urlencode({'s': ticker, 'region': 'US', 'lang': 'en-US'}))
    for index, url in enumerate(urls):
        progress(f'Searching news feed {index + 1}/{len(urls)}…')
        try:
            entries = fetch_feed(url)
            successful_feeds += 1
            if len(entries) >= 100:
                warnings.append('A feed reached 100 results; the source may have omitted additional articles.')
            for entry in entries:
                parsed_date = entry.get('published_parsed')
                if not parsed_date:
                    invalid_dates += 1
                    continue
                published = datetime(*parsed_date[:6], tzinfo=UTC)
                if not start <= published <= end:
                    continue
                title, link = entry.get('title', '').strip(), entry.get('link', '').strip()
                if not title or urlsplit(link).scheme not in {'http', 'https'}:
                    continue
                # Same URL or normalized title represents one article across feeds.
                key = ' '.join(title.casefold().split())
                if key in seen or link in seen:
                    continue
                seen.update((key, link))
                articles.append({'title': title, 'url': link, 'published': published.isoformat(), 'source': entry.get('source', {}).get('title') or urlsplit(link).hostname, 'origin': 'News feed'})
        except Exception as error:
            warnings.append(f'{urlsplit(url).hostname}: {type(error).__name__}: {error}')
    if invalid_dates:
        warnings.append(f'{invalid_dates} feed entries without publication dates were excluded from the date filter.')
    if not successful_feeds:
        raise RuntimeError('News feeds could not be loaded. ' + ' '.join(warnings))
    articles.sort(key=lambda article: article['published'], reverse=True)
    if len(articles) < 10:
        warnings.append(f'Only {len(articles)} matching articles were returned for this period; the target is at least 10 when available.')
    warnings.append('Free feeds provide limited English-language search results, not a complete news archive. Relevance follows the issuer/ticker search and may need review.')
    return articles, list(dict.fromkeys(warnings)), start, end


class FinBERT:
    def __init__(self):
        self.classifier = None

    def load(self):
        if self.classifier is None:
            import torch
            from transformers import pipeline
            torch.set_num_threads(2)
            self.classifier = pipeline('text-classification', model=MODEL_NAME, tokenizer=MODEL_NAME, device=0 if torch.cuda.is_available() else -1, top_k=None)

    def analyze(self, text):
        self.load()
        tokenizer = self.classifier.tokenizer
        tokens = tokenizer.encode(text, add_special_tokens=False, truncation=True, max_length=MAX_TEXT_TOKENS + 1)
        if not tokens:
            raise ValueError('No text to analyze.')
        truncated = len(tokens) > MAX_TEXT_TOKENS
        tokens = tokens[:MAX_TEXT_TOKENS]
        chunks = [tokens[index:index + 384] for index in range(0, len(tokens), 384)]
        texts = [tokenizer.decode(chunk, skip_special_tokens=True) for chunk in chunks]
        predictions = self.classifier(texts, batch_size=8, truncation=True, max_length=512, top_k=None)
        scores = {'positive': 0.0, 'negative': 0.0}
        for chunk, prediction in zip(chunks, predictions):
            for item in prediction:
                label = item['label'].lower()
                if label in scores:
                    scores[label] += item['score'] * len(chunk) / len(tokens)
        # Force two labels; keep the original model probability (no renormalization).
        # Exact ties select positive, the first label in scores.
        label = max(scores, key=scores.get)
        return {'classification': label.upper(), 'score': round(scores[label], 4), 'truncated': truncated}


class NewsService:
    def __init__(self, assets, model=None):
        self.assets = {asset['id']: asset for asset in assets}
        self.model = model or FinBERT()

    def news(self, asset_id, window, progress):
        asset = self.assets[asset_id]
        articles, warnings, start, end = collect_news(asset, window, progress)
        if articles:
            progress('Loading FinBERT (the first run downloads the model)…')
            self.model.load()
        completed = []
        for index, article in enumerate(articles):
            progress(f'Analyzing article {index + 1}/{len(articles)} with FinBERT…')
            text = article['title']
            basis = 'Headline only'
            note = ''
            try:
                if urlsplit(article['url']).hostname == 'news.google.com':
                    note = 'Google News aggregation link; publisher text was not available. Paste the publisher URL to analyze its text.'
                else:
                    extracted = extract_article(article['url'])
                    text, basis = extracted['text'], 'Article text'
            except Exception as error:
                note = f'Publisher text unavailable: {error}'
            try:
                prediction = self.model.analyze(text)
                if prediction['truncated']:
                    note += f' Analyzed the first {MAX_TEXT_TOKENS} tokens.'
                completed.append({**article, **prediction, 'basis': basis, 'note': note})
            except Exception as error:
                completed.append({**article, 'classification': 'UNAVAILABLE', 'score': None, 'basis': basis, 'note': f'Analysis failed: {error}'})
        analyzed = sum(article['classification'] != 'UNAVAILABLE' for article in completed)
        if analyzed < len(completed):
            warnings.append(f'{len(completed) - analyzed} articles could not be classified. Refresh to retry.')
        return {'articles': completed, 'warnings': warnings, 'start': start.isoformat(), 'end': end.isoformat(), 'fetchedAt': datetime.now(UTC).isoformat(), 'assetId': asset_id, 'window': window, 'analyzed': analyzed}

    def custom(self, asset_id, url, pasted_text, progress):
        safe_url(url)  # Also validate reference URLs when text is supplied.
        if len(pasted_text) > 100_000:
            raise ValueError('Please limit pasted article text to 100,000 characters.')
        progress('Reading your article…')
        if pasted_text.strip():
            article = {'text': pasted_text.strip(), 'title': pasted_text.strip().splitlines()[0][:180], 'url': url}
            basis = 'Pasted article text'
        else:
            article = extract_article(url)
            basis = 'Article text'
        progress('Analyzing your article with FinBERT…')
        prediction = self.model.analyze(article['text'])
        return {**prediction, 'title': article['title'], 'url': article['url'], 'published': None, 'source': urlsplit(article['url']).hostname, 'origin': 'Custom URL', 'basis': basis, 'note': f'Analyzed the first {MAX_TEXT_TOKENS} tokens.' if prediction['truncated'] else '', 'assetId': asset_id, 'analyzedAt': datetime.now(UTC).isoformat()}


def create_app(assets, report, service=None):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 150_000
    service = service or NewsService(assets)
    token = secrets.token_urlsafe(32)
    jobs, lock = {}, threading.Lock()
    executor = ThreadPoolExecutor(max_workers=1)
    app.extensions['news_executor'] = executor

    @app.before_request
    def authorize():
        if request.path.startswith('/api/') and not secrets.compare_digest(request.headers.get('X-Dashboard-Token', ''), token):
            return jsonify(error='Please reload the dashboard to reconnect.'), 403

    @app.get('/')
    def index():
        payload = {'assets': assets, 'report': report, 'token': token, 'live': True, 'results': {}, 'manual': {}}
        encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()
        return LIVE_TEMPLATE.replace('__PAYLOAD__', encoded), 200, {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}

    @app.post('/api/jobs')
    def start_job():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(error='Expected a JSON object.'), 400
        asset_id, kind = data.get('assetId'), data.get('kind')
        if not isinstance(asset_id, str) or asset_id not in service.assets or kind not in ('news', 'custom'):
            return jsonify(error='Select a valid asset and action.'), 400
        if kind == 'news' and (not isinstance(data.get('window'), str) or data['window'] not in WINDOWS):
            return jsonify(error='Invalid time period.'), 400
        if kind == 'custom' and (not isinstance(data.get('url'), str) or not isinstance(data.get('text', ''), str)):
            return jsonify(error='Provide an article URL and optional text.'), 400
        signature = hashlib.sha256(json.dumps(
            [kind, asset_id, data.get('window') if kind == 'news' else [data['url'].strip(), data.get('text', '')]],
            ensure_ascii=False,
        ).encode()).hexdigest()
        with lock:
            # Multiple tabs may open the same initial selection concurrently.
            for existing_id, job in jobs.items():
                if job['state'] in {'queued', 'running'} and job['signature'] == signature:
                    return jsonify(id=existing_id), 202
            while len(jobs) >= 8:
                terminal = next((key for key, job in jobs.items() if job['state'] in {'complete', 'error'}), None)
                if terminal is None:
                    return jsonify(error='Eight analyses are already queued. Wait for one to finish, then retry.'), 429
                del jobs[terminal]
            job_id = secrets.token_urlsafe(16)
            jobs[job_id] = {'state': 'queued', 'message': 'Waiting for the previous analysis to finish…', 'signature': signature}

        def progress(message):
            with lock:
                jobs[job_id]['message'] = message

        def run():
            with lock:
                jobs[job_id].update(state='running', message='Starting analysis…')
            try:
                result = service.news(asset_id, data['window'], progress) if kind == 'news' else service.custom(asset_id, data['url'].strip(), data.get('text', ''), progress)
                with lock:
                    jobs[job_id].update(state='complete', result=result)
            except Exception as error:
                with lock:
                    jobs[job_id].update(state='error', error=str(error))
        executor.submit(run)
        return jsonify(id=job_id), 202

    @app.get('/api/jobs/<job_id>')
    def get_job(job_id):
        with lock:
            job = jobs.get(job_id)
            return (jsonify({key: value for key, value in job.items() if key != 'signature'}), 200) if job else (jsonify(error='Analysis expired; run it again.'), 404)

    return app


LIVE_TEMPLATE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Portfolio News · FinBERT</title><style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#080e19;color:#e2e8f0}*{box-sizing:border-box}body{margin:0}header,main{max-width:1500px;margin:auto;padding:22px}header{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}h1{font-size:23px;color:#34d399;margin:0}h2{font-size:17px;margin-top:0}p,small{color:#94a3b8;line-height:1.6}p{font-size:13px}button,input,select,textarea{font:inherit;color:inherit;background:#080e19;border:1px solid #334155;border-radius:8px;padding:10px}button{cursor:pointer}button:hover,button:focus-visible{border-color:#34d399}button:disabled,select:disabled{opacity:.5;cursor:wait}.panel{border:1px solid #243044;background:#101a2a;padding:18px;border-radius:12px;margin-bottom:18px}.layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:20px}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.row input{flex:1;min-width:160px}#search,#type{width:100%;margin-bottom:10px}#holdings{max-height:710px;overflow:auto;display:flex;flex-direction:column;gap:8px}.asset{text-align:left;overflow-wrap:anywhere}.asset small{display:block;font-size:11px}.asset[aria-pressed=true]{border-color:#34d399;background:#10352d}#selected{overflow-wrap:anywhere}#status{color:#67e8f9}#warnings{color:#fbbf24;font-size:12px;white-space:pre-wrap}#report{font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}.scroll{overflow:auto;max-height:660px}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:12px;text-align:left;vertical-align:top;border-bottom:1px solid #27364b}th{position:sticky;top:0;background:#172237}td:nth-child(2){min-width:240px}td:last-child{min-width:170px}a{color:#67e8f9;overflow-wrap:anywhere}.positive{color:#34d399}.negative{color:#fb7185}#summary{display:flex;gap:16px;flex-wrap:wrap;padding:14px 0}.count{padding:10px;border:1px solid #334155;border-radius:8px}.note{font-size:11px;display:block;color:#94a3b8;margin-top:6px}textarea{width:100%;min-height:110px;margin:10px 0}label{font-size:13px}.distribution-row{display:grid;grid-template-columns:100px minmax(50px,1fr) 95px;gap:12px;align-items:center;margin:10px 0;font-size:12px}.distribution-row progress{width:100%;height:18px;accent-color:currentColor}summary{cursor:pointer}.primary{background:#075e4d;border-color:#34d399}#mode{margin-bottom:0}@media(max-width:800px){.layout{grid-template-columns:1fr}#holdings{max-height:220px}header,main{padding:14px}}
</style></head><body>
<header><div><h1>Portfolio News · FinBERT</h1><p id="asset-count"></p></div><div class="row"><button id="download-html">Save analyzed report</button><button id="download-csv">Download CSV</button></div></header>
<main><div class="panel"><p id="mode"></p><details><summary>Workbook import report</summary><div id="report"></div></details></div><div class="layout">
<aside class="panel"><h2>Uploaded assets</h2><input id="search" type="search" placeholder="Ticker, company, ISIN…" aria-label="Search assets"><select id="type" aria-label="Asset type"><option value="">All asset types</option><option>Stock</option><option>ETF</option><option>Bond</option><option>Other</option></select><p id="holding-count"></p><div id="holdings"></div></aside>
<section><div class="panel"><h2 id="selected"></h2><div class="row"><label for="window">News period</label><select id="window"><option value="1d">1 day · today (UTC)</option><option value="6d">6 days</option><option value="1m" selected>1 month</option><option value="6m">6 months</option><option value="1y">1 year</option></select><button class="primary" id="refresh">Fetch & analyze</button></div><p>Changing the asset or period automatically analyzes available matching news. The target is at least 10 articles when available. Free feeds can omit articles, especially older news. All returned matches in the selected period are processed.</p><p id="status" role="status" aria-live="polite"></p><p id="coverage"></p><div id="warnings"></div><div id="summary"></div><h2>Sentiment distribution</h2><div id="distribution" aria-live="polite"></div></div>
<div class="panel"><h2>News analysis · one row per article</h2><p>Each article is assigned positive or negative using the higher of those two FinBERT probabilities. Scores retain the original model probabilities and are not calibrated certainty or forecasts. The Text analyzed column identifies headline-only results and truncation.</p><div class="scroll"><table><thead><tr><th>Published (UTC)</th><th>Article</th><th>Source</th><th>Sentiment</th><th>Model score</th><th>Text analyzed</th></tr></thead><tbody id="news-rows"></tbody></table></div></div>
<div class="panel"><h2>Analyze your own article URL</h2><p>Custom articles are kept in a separate table for the selected asset. They are not filtered by the news period. If the publisher blocks extraction, paste the article text.</p><form id="custom-form"><div class="row"><input id="url" type="url" required placeholder="https://publisher.com/article" aria-label="Article URL"><button id="custom-submit" type="submit">Analyze URL</button></div><details><summary>Paste article text (optional)</summary><textarea id="custom-text" maxlength="100000" aria-label="Article text" placeholder="Paste the article text here if its URL cannot be read…"></textarea></details></form><div class="scroll"><table><thead><tr><th>Published (UTC)</th><th>Article</th><th>Source</th><th>Sentiment</th><th>Model score</th><th>Text analyzed</th></tr></thead><tbody id="custom-rows"></tbody></table></div></div>
</section></div></main>
<script id="payload" type="text/plain">__PAYLOAD__</script><script>
'use strict';
const decode = value => JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(value), c=>c.charCodeAt(0))));
const state = decode(document.getElementById('payload').textContent);
const $ = id=>document.getElementById(id);
let selectedId = state.selectedId || state.assets[0]?.id || null;
let period = state.period || '1m';
let busy = false;
const asset = ()=>state.assets.find(a=>a.id===selectedId);
const cacheKey = ()=>`${selectedId}:${period}`;
const node = (tag,text,cls)=>{const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;return el;};
function setBusy(value){busy=value;for(const id of ['window','refresh','custom-submit'])$(id).disabled=value||!state.live||!asset();$('window').disabled=value;for(const button of $('holdings').querySelectorAll('button'))button.disabled=value;}
function renderHoldings(){
 const query=$('search').value.trim().toLowerCase(), kind=$('type').value;
 const rows=state.assets.filter(a=>(!kind||a.type===kind)&&[a.name,a.ticker,a.identifier,a.file,a.sheet].join(' ').toLowerCase().includes(query));
 $('holding-count').textContent=`${rows.length} of ${state.assets.length} assets`;
 const fragment=document.createDocumentFragment();
 for(const a of rows){const button=node('button',`${a.ticker} · ${a.type}`,'asset');button.disabled=busy;button.setAttribute('aria-pressed',String(a.id===selectedId));button.append(node('small',a.name),node('small',`${a.file} / ${a.sheet} / row ${a.row}`));button.addEventListener('click',()=>{selectedId=a.id;renderHoldings();showResult();loadNews();});fragment.append(button);}
 if(!rows.length)fragment.append(node('p','No matching assets.'));
 $('holdings').replaceChildren(fragment);
}
function renderTable(id,rows){
 const fragment=document.createDocumentFragment();
 for(const a of rows){const row=document.createElement('tr');row.append(node('td',a.published?new Date(a.published).toISOString().slice(0,16).replace('T',' '):'Not supplied'));
 const title=document.createElement('td'), link=node('a',a.title);link.href=a.url;link.target='_blank';link.rel='noopener noreferrer';title.append(link);row.append(title,node('td',a.source),node('td',a.classification,a.classification.toLowerCase()),node('td',a.score==null?'—':`${(a.score*100).toFixed(1)}%`));const basis=node('td',a.basis);if(a.note)basis.append(node('small',a.note,'note'));row.append(basis);fragment.append(row);}
 if(!rows.length){const row=document.createElement('tr'),cell=node('td','No articles analyzed for this selection yet.');cell.colSpan=6;row.append(cell);fragment.append(row);}
 $(id).replaceChildren(fragment);
}
function renderDistribution(articles){
 const classified=articles.filter(a=>['POSITIVE','NEGATIVE'].includes(a.classification));
 if(!classified.length){$('distribution').replaceChildren(node('p','Select an asset and period to collect news. The distribution appears after analysis completes.'));return;}
 const fragment=document.createDocumentFragment();
 for(const label of ['POSITIVE','NEGATIVE']){
  const count=classified.filter(a=>a.classification===label).length;
  const percent=(count/classified.length*100).toFixed(1);
  const row=node('div','',`distribution-row ${label.toLowerCase()}`),bar=document.createElement('progress');
  bar.max=classified.length;bar.value=count;bar.setAttribute('aria-label',`${label}: ${count} articles, ${percent}%`);
  row.append(node('span',label),bar,node('span',`${count} · ${percent}%`));fragment.append(row);
 }
 $('distribution').replaceChildren(fragment);
}
function showResult(){
 const a=asset(),result=state.results[cacheKey()];
 $('selected').textContent=a?`${a.ticker} — ${a.name} (${a.type})`:'No assets loaded';
 $('coverage').textContent=result?`Publication window: ${result.start.slice(0,10)} to ${result.end.slice(0,16).replace('T',' ')} UTC · ${result.articles.length} found · ${result.analyzed} classified · fetched ${result.fetchedAt.slice(0,16).replace('T',' ')} UTC`:'';
 $('warnings').textContent=result?result.warnings.join('\n'):'';
 renderDistribution(result?.articles||[]);
 renderTable('news-rows',result?.articles||[]);renderTable('custom-rows',state.manual[selectedId]||[]);
 $('summary').replaceChildren(...['POSITIVE','NEGATIVE'].map(label=>node('span',`${label}: ${(result?.articles||[]).filter(a=>a.classification===label).length}`,`count ${label.toLowerCase()}`)));
}
async function api(path,options={}){
 const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Dashboard-Token':state.token}});
 let body;try{body=await response.json();}catch{throw new Error('Colab disconnected or returned an unreadable response. Reconnect and rerun the dashboard cell.');}
 if(!response.ok)throw new Error(body.error||`Request failed (${response.status}).`);return body;
}
async function runJob(data){
 const job=await api('api/jobs',{method:'POST',body:JSON.stringify(data)});
 for(;;){const status=await api(`api/jobs/${job.id}`);if(status.state==='complete')return status.result;if(status.state==='error')throw new Error(status.error);$('status').textContent=status.message;await new Promise(resolve=>setTimeout(resolve,1000));}
}
async function loadNews(force=false){
 if(busy||!asset())return;
 const key=cacheKey();
 if(state.results[key]&&!force){$('status').textContent='Showing saved analysis. Use Fetch & analyze to refresh while connected.';return;}
 if(!state.live){$('status').textContent='This HTML report has no saved analysis for this selection. Open the live Colab dashboard to fetch it.';return;}
 setBusy(true);$('status').textContent='Searching news and preparing FinBERT…';
 try{state.results[key]=await runJob({kind:'news',assetId:selectedId,window:period});showResult();$('status').textContent='Analysis complete. Use Save analyzed report to keep your results.';}
 catch(error){$('status').textContent=error.message;}
 finally{setBusy(false);}
}
$('window').value=period;
$('window').addEventListener('change',()=>{period=$('window').value;showResult();loadNews();});
$('refresh').addEventListener('click',()=>loadNews(true));
$('search').addEventListener('input',renderHoldings);$('type').addEventListener('change',renderHoldings);
$('custom-form').addEventListener('submit',async event=>{
 event.preventDefault();if(busy||!state.live||!asset())return;setBusy(true);$('status').textContent='Reading your article…';
 try{const result=await runJob({kind:'custom',assetId:selectedId,url:$('url').value,text:$('custom-text').value});state.manual[selectedId] ||= [];state.manual[selectedId].unshift(result);showResult();$('status').textContent='Your article was analyzed and added to the custom table.';$('custom-form').reset();}
 catch(error){$('status').textContent=error.message;}finally{setBusy(false);}
});
function download(content,type,filename){const url=URL.createObjectURL(new Blob([content],{type})),link=document.createElement('a');link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function snapshotHTML(){
 const saved={...state,live:false,token:'',selectedId,period};
 const bytes=new TextEncoder().encode(JSON.stringify(saved));let binary='';for(const b of bytes)binary+=String.fromCharCode(b);
 const copy=document.documentElement.cloneNode(true);copy.querySelector('#payload').textContent=btoa(binary);
 // Do not retain a draft URL/article in the exported HTML.
 copy.querySelector('#url').removeAttribute('value');copy.querySelector('#custom-text').textContent='';
 return '<!doctype html>\n'+copy.outerHTML;
}
$('download-html').addEventListener('click',()=>download(snapshotHTML(),'text/html;charset=utf-8','portfolio_news_report.html'));
function csvCell(value){let text=String(value??'');if(/^\s*[=+@-]/.test(text)||/^[\t\r\n]/.test(text))text="'"+text;return '"'+text.replace(/"/g,'""')+'"';}
function buildCSV(){
 const rows=[['Ticker','Name','Type','Period','Published UTC','Article','Source','URL','Sentiment','Model score','Text analyzed','Note','Fetched / analyzed UTC']];
 for(const result of Object.values(state.results)){const a=state.assets.find(a=>a.id===result.assetId);for(const article of result.articles)rows.push([a.ticker,a.name,a.type,result.window,article.published,article.title,article.source,article.url,article.classification,article.score,article.basis,article.note,result.fetchedAt]);}
 for(const [id,articles] of Object.entries(state.manual)){const a=state.assets.find(a=>a.id===id);for(const article of articles)rows.push([a.ticker,a.name,a.type,'Custom',article.published,article.title,article.source,article.url,article.classification,article.score,article.basis,article.note,article.analyzedAt]);}
 return '\uFEFF'+rows.map(row=>row.map(csvCell).join(',')).join('\r\n');
}
$('download-csv').addEventListener('click',()=>download(buildCSV(),'text/csv;charset=utf-8','news_sentiment.csv'));
$('asset-count').textContent=`${state.assets.length} uploaded assets · FinBERT positive / negative`;
$('mode').textContent=state.live?'Live analysis · Keep Colab connected. Results are held in browser memory; download HTML or CSV before reloading.':'Saved HTML report · Browse completed results offline. Live fetching and URL analysis require the Colab notebook.';
$('report').textContent=state.report.join('\n');
renderHoldings();showResult();setBusy(false);loadNews();
</script></body></html>
'''


def live_launcher_html(url):
    """An HTML shortcut that opens the live backend instead of an empty report."""
    import html
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('Expected the live dashboard HTTP(S) URL.')
    target = json.dumps(url).replace('<', '\\u003c')
    link = html.escape(url, quote=True)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Open live sentiment analysis</title></head>
<body style="background:#080e19;color:#e2e8f0;font:18px system-ui;padding:40px">
<h1>Opening your live sentiment dashboard…</h1>
<p>Keep your Colab runtime connected. If it has stopped, rerun the notebook and download a new launcher.</p>
<p><a style="color:#34d399" href="{link}">Open LIVE FinBERT dashboard</a></p>
<script>window.location.replace({target});</script></body></html>"""


def download_live_launcher(url):
    from google.colab import files
    destination = Path('/content/OPEN_LIVE_SENTIMENT.html')
    destination.write_text(live_launcher_html(url), encoding='utf-8')
    try:
        files.download(str(destination))
    except Exception as error:
        print(f'Dashboard is running. Download {destination.name} from the Colab Files panel: {error}')
    return destination


def open_live_dashboard():
    """Open the running FinBERT dashboard, with an actionable version check."""
    import html
    import requests
    from google.colab import output
    from IPython.display import HTML, IFrame, display

    try:
        response = requests.get('http://127.0.0.1:8765/', timeout=5)
        response.raise_for_status()
    except requests.RequestException as error:
        raise RuntimeError(
            'The live FinBERT dashboard is not running. Run both cells in '
            'news_sentiment_dashboard.ipynb, then run this cell again. '
            'An old downloaded index.html cannot fetch news.'
        ) from error
    if 'Portfolio News · FinBERT' not in response.text or 'id="window"' not in response.text:
        raise RuntimeError(
            'This is not the automatic FinBERT dashboard. Run the latest '
            'news_sentiment_dashboard.ipynb and reopen its live dashboard.'
        )
    url = output.eval_js('google.colab.kernel.proxyPort(8765)')
    display(HTML(
        '<p><a target="_blank" rel="noopener noreferrer" href="'
        + html.escape(url, quote=True)
        + '"><b>Open LIVE FinBERT dashboard</b></a></p>'
        '<p>Select a ticker and a news period. Keep Colab connected. '
        'Save analyzed report keeps a snapshot of completed results.</p>'
    ))
    display(IFrame(url, width='100%', height=1000))
    return url


def start_colab():
    from werkzeug.serving import make_server
    paths = discover_files()
    if not paths:
        raise FileNotFoundError('Upload your Excel files using the Colab Files panel, then rerun this cell.')
    assets, report = load_assets(paths)
    print('\n'.join(report))
    if not assets:
        raise ValueError('No assets imported. Check the sheet headers in the import report.')
    # Rerunning the cell reuses the port after closing the previous local server.
    previous = globals().get('_news_server')
    if previous is not None:
        previous.shutdown()
        previous.server_close()
        globals()['_news_app'].extensions['news_executor'].shutdown(wait=True)
    app = create_app(assets, report)
    server = make_server('0.0.0.0', 8765, app, threaded=True)
    globals()['_news_server'], globals()['_news_app'] = server, app
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f'{len(assets)} assets loaded. Keep this Colab runtime connected for live analysis.')
    print('The first analysis downloads FinBERT. Use Save analyzed report in the dashboard to save completed results.')
    url = open_live_dashboard()
    download_live_launcher(url)
    print('Open the downloaded OPEN_LIVE_SENTIMENT.html for automatic analysis. Save analyzed report creates an offline snapshot.')


if __name__ == '__main__':
    start_colab()
