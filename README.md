# Universal College Course & Instructor Research Agent

## Install
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## Run
```powershell
streamlit run app.py
```

## Important limitations
This version preserves the known UNCW Banner workflow and adds a best-effort generic
HTML schedule form/table workflow. It does not guarantee compatibility with every
college portal. Some portals use custom APIs, authentication, CAPTCHA, complex
JavaScript widgets, or result layouts that require a dedicated adapter.

The generic parser maps only recognizable table columns and does not fabricate
missing values. Instructor research fetches the supplied official URLs and provides
source text for review; it does not automatically infer every instructor field.
