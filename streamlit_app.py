"""Free Streamlit Community Cloud entry point for the shared FinBERT service."""
import base64
from collections import Counter
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import threading

import pandas as pd
import streamlit as st

import dashboard

st.set_page_config(page_title='Portfolio News · FinBERT', page_icon='📊', layout='wide')


@st.cache_data(show_spinner=False)
def read_watchlist(fingerprints):
    return dashboard.load_assets([Path(path) for path, digest in fingerprints])


@st.cache_resource(show_spinner=False)
def shared_model(classification_mode):
    # Only one model copy, and one inference operation at a time across visitors.
    return dashboard.FinBERT(), threading.Lock()


def csv_cell(value):
    text = '' if value is None else str(value)
    if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
        text = "'" + text
    return text


def csv_report(assets, results, manual):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['Ticker', 'Name', 'Type', 'Period', 'Published UTC', 'Article', 'Source', 'URL', 'Sentiment', 'Model score', 'Text analyzed', 'Note'])
    by_id = {asset['id']: asset for asset in assets}
    def write(asset_id, period, articles):
        asset = by_id[asset_id]
        for article in articles:
            writer.writerow(map(csv_cell, [asset['ticker'], asset['name'], asset['type'], period, article.get('published'), article['title'], article['source'], article['url'], article['classification'], article['score'], article['basis'], article['note']]))
    for result in results.values():
        write(result['assetId'], result['window'], result['articles'])
    for asset_id, articles in manual.items():
        write(asset_id, 'Custom', articles)
    return ('\ufeff' + output.getvalue()).encode('utf-8')


def show_articles(articles):
    if not articles:
        st.info('No articles available for this selection.')
        return
    rows = [{
        'Published (UTC)': article.get('published') or 'Not supplied',
        'Article': article['title'],
        'Source': article['source'],
        'Sentiment': article['classification'],
        'Model score (%)': round(article['score'] * 100, 1) if article['score'] is not None else None,
        'Text analyzed': article['basis'],
        'Note': article['note'],
        'Article URL': article['url'],
    } for article in articles]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch',
                 column_config={'Article URL': st.column_config.LinkColumn(display_text='Open article')})


def main():
    st.title('Portfolio News · FinBERT')
    st.caption('Automatic financial news sentiment · Positive / Negative')
    folder = Path(os.environ.get('SENTIMENT_DATA_DIR', Path(__file__).resolve().parent))
    paths = sorted({
        path.resolve()
        for base in (folder, folder / 'data')
        if base.is_dir()
        for path in base.iterdir()
        if path.is_file() and path.suffix.lower() in dashboard.SUPPORTED and not path.name.startswith('~$')
    })
    fingerprints = tuple((str(path), hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths)
    assets, report = read_watchlist(fingerprints)
    with st.expander('Workbook import report'):
        st.text('\n'.join(report) or 'No spreadsheets uploaded.')
    if not assets:
        st.info('The site owner must upload the approved Excel files alongside streamlit_app.py in the GitHub repository, then reboot the app. Review any workbook errors above.')
        st.stop()

    dataset_key = (dashboard.CLASSIFICATION_MODE, fingerprints)
    if st.session_state.get('dataset') != dataset_key:
        st.session_state.update(dataset=dataset_key, results={}, manual={}, errors={})
    results, manual, errors = (st.session_state[key] for key in ('results', 'manual', 'errors'))
    by_id = {asset['id']: asset for asset in assets}
    st.sidebar.header(f'{len(assets)} uploaded assets')
    kind = st.sidebar.selectbox('Asset type', ['All', 'Stock', 'ETF', 'Bond', 'Other'])
    options = [asset['id'] for asset in assets if kind == 'All' or asset['type'] == kind]
    if not options:
        st.info('No assets of this type. Choose another asset type.')
        st.stop()
    asset_id = st.sidebar.selectbox('Select an asset (type to search)', options,
        format_func=lambda key: f"{by_id[key]['ticker']} — {by_id[key]['name']} ({by_id[key]['type']}) · {by_id[key]['sheet']} row {by_id[key]['row']}")
    period = st.sidebar.selectbox('News period', list(dashboard.WINDOWS), index=2,
        format_func=lambda key: '1 day · today (UTC)' if key == '1d' else dashboard.WINDOWS[key])
    refresh = st.sidebar.button('Fetch & analyze again', type='primary')
    selected = by_id[asset_id]
    st.header(f"{selected['ticker']} — {selected['name']}")
    st.caption('Select an asset or period to analyze its matching news automatically. The first analysis downloads FinBERT and can take several minutes. Analysis runs on the hosted server; Colab is not needed.')
    st.caption('Free English-language feeds are incomplete and cannot guarantee every article or at least ten articles. Headline-only results are labeled. Model scores are not calibrated certainty or forecasts.')
    st.caption('Every article is assigned positive or negative using the higher of those two FinBERT probabilities. Scores retain the original model probabilities.')
    key = f'{asset_id}:{period}'
    model, lock = shared_model(dashboard.CLASSIFICATION_MODE)
    service = dashboard.NewsService(assets, model=model)

    if refresh or (key not in results and key not in errors):
        if not lock.acquire(blocking=False):
            st.warning('Another visitor is running an analysis. Wait a moment, then click Fetch & analyze again.')
        else:
            try:
                with st.status('Searching news…', expanded=True) as status:
                    message = st.empty()
                    results[key] = service.news(asset_id, period, message.write)
                    errors.pop(key, None)
                    status.update(label='Analysis complete', state='complete', expanded=False)
            except Exception as error:
                errors[key] = str(error)
            finally:
                lock.release()
    if key in errors:
        st.error(errors[key])
        st.caption('Use Fetch & analyze again to retry.')
    result = results.get(key)
    if result:
        st.caption(f"Publication window: {result['start']} to {result['end']} · {len(result['articles'])} found · {result['analyzed']} classified")
        for warning in result['warnings']:
            st.warning(warning)
        counts = Counter(article['classification'] for article in result['articles'])
        labels = ['POSITIVE', 'NEGATIVE']
        classified = sum(counts[label] for label in labels)
        for column, label in zip(st.columns(len(labels)), labels):
            percent = f'{counts[label] / classified:.1%}' if classified else '0.0%'
            column.metric(label, f'{counts[label]} articles · {percent}')
        st.subheader('Sentiment distribution')
        st.bar_chart(pd.DataFrame({'Articles': [counts[label] for label in labels]}, index=labels))
        st.subheader('News analysis · one row per article')
        show_articles(result['articles'])

    st.subheader('Analyze your own article URL')
    st.caption('Custom articles are shown separately and are not filtered by the selected news period. If extraction is blocked, paste the article text.')
    with st.form('custom_article', clear_on_submit=False):
        url = st.text_input('Article URL', placeholder='https://publisher.com/article')
        pasted = st.text_area('Article text (optional)', max_chars=100000)
        submitted = st.form_submit_button('Analyze URL')
    if submitted:
        if not url.strip():
            st.error('Enter an article URL.')
        elif not lock.acquire(blocking=False):
            st.warning('Another analysis is running. Wait a moment, then click Analyze URL again.')
        else:
            try:
                with st.status('Reading your article…', expanded=True) as status:
                    message = st.empty()
                    article = service.custom(asset_id, url.strip(), pasted, message.write)
                    manual.setdefault(asset_id, []).insert(0, article)
                    status.update(label='Your article was analyzed', state='complete', expanded=False)
            except Exception as error:
                st.error(str(error))
            finally:
                lock.release()
    if manual.get(asset_id):
        show_articles(manual[asset_id])

    st.divider()
    st.caption('Export before closing or reloading: completed results are kept in this session, not in a permanent database.')
    payload = {'assets': assets, 'report': report, 'live': False, 'token': '', 'results': results, 'manual': manual, 'selectedId': asset_id, 'period': period}
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()
    html = dashboard.LIVE_TEMPLATE.replace('__PAYLOAD__', encoded).replace('Colab notebook', 'live website').replace('live Colab dashboard', 'live website')
    left, right = st.columns(2)
    left.download_button('Save analyzed report', html.encode('utf-8'), 'portfolio_news_report.html', 'text/html')
    right.download_button('Download CSV', csv_report(assets, results, manual), 'news_sentiment.csv', 'text/csv')


if __name__ == '__main__':
    main()
