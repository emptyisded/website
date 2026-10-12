# Portfolio News · FinBERT — free hosting

## Deploy on Streamlit Community Cloud

1. Extract `sentiment_free_hosting.zip` on your computer.
2. Sign into https://github.com and create a repository named `portfolio-sentiment`.
3. In that repository, choose **Add file → Upload files**. Upload the extracted files to the repository root: `streamlit_app.py`, `dashboard.py`, `requirements.txt`, `README.md`, and the `.streamlit` folder. The `.streamlit/config.toml` theme file is optional if your file picker hides it. Upload the files, not the ZIP or an enclosing folder.
4. Upload your approved stock, ETF and bond Excel files next to `streamlit_app.py`. Commit the uploads. They are not included in this package because they are in your Colab session. Download them from Colab's Files panel if you need copies. Files placed directly in `data/` also work.
5. Open https://share.streamlit.io and sign in with GitHub. Choose **Create app** / **Deploy a public app from GitHub** (wording may vary).
6. Select your repository and branch (usually `main`). Set the main file path to **streamlit_app.py**. In **Advanced settings**, select **Python 3.11**. Deploy.
7. Wait for dependency installation. Open the resulting `https://YOUR-APP.streamlit.app` URL. The first analysis downloads FinBERT and can take several minutes. Select an asset and period using the sidebar; progress and article results appear in the main panel.
8. Share that URL. Colab and your computer do not need to remain running.

Streamlit Community Cloud is a free hosting service. Its official documentation says apps without traffic for 12 hours sleep; a visitor can wake them. A permanent URL does not mean guaranteed uninterrupted service. Hosting has CPU/memory limits; this package uses CPU-only PyTorch, one shared model and one analysis at a time to reduce resource use. Large watchlists or many visitors may exceed the free service's resources. If that happens, reboot the app and check **Manage app → Logs**; no paid service is enabled automatically.

Official references checked 2026-10-10:
- https://docs.streamlit.io/deploy/streamlit-community-cloud
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app
- https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app

## Your watchlist and results

A public GitHub repository exposes uploaded spreadsheets, and a public app displays their asset data to visitors. Only publish files intended for public access. Community Cloud also supports private GitHub repositories; use its access settings for data that should stay private.

The app loads every recognized workbook row. Inspect **Workbook import report** for skipped sheets or unusual headers. Stocks, ETFs, bonds and Other assets remain selectable; type in the asset dropdown to search. Time periods: 1 day (today UTC), 6 days, 1 month, 6 months and 1 year.

Changing the selected asset or time period automatically runs analysis. Results are cached in your session. **Fetch & analyze again** refreshes the selected period. When another visitor holds the shared analysis slot, wait and retry.

Free Google News/Yahoo feeds do not guarantee every article or ten results per period. All returned matching articles are processed. Search relevance needs review. Google News aggregation links and blocked publishers use explicitly labeled headline sentiment. Direct article URLs use readable article text where available. Long text is limited to 4096 tokens, with truncation labeled. Model scores are not calibrated certainty or forecasts.

**Analyze your own article URL** accepts a publisher URL and optional pasted text. Custom analyses are listed separately and are not filtered by the selected news period.

**Save analyzed report** and **Download CSV** export completed results. Results are session data and may be lost on reload, disconnect or server restart. There is no permanent history database. Workbook changes in GitHub become available after the app updates or is rebooted. Keep your original files.

## Local startup and packaging

Use Python 3.11, install `requirements.txt`, then run:

```sh
streamlit run streamlit_app.py
```

`dashboard.py` is generated from the existing shared Excel loader and FinBERT news backend. Its Colab helpers are not invoked by Streamlit. No Colab package, separate Flask server, Docker or news API key is required for this deployment.
