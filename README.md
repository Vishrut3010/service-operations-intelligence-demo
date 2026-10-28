# IFB Service Operations Intelligence — Dark Mode

This package contains the Streamlit app with a dark UI theme, IFB branding, Branch-wise / Franchise-wise / Executive-wise views, age-only oldest-first pending-call list, reason-for-pendency analysis, CSS score, and daily productivity outputs.

## Files
- `app.py` — Streamlit interface and theme CSS
- `service_ops_engine_v3.py` — report parsing and analysis/image generation engine
- `requirements.txt` — Python dependencies
- `.streamlit/config.toml` — Streamlit dark theme settings

## Deploy/update
Upload `app.py`, `service_ops_engine_v3.py`, and `requirements.txt` to the repository root. Upload `.streamlit/config.toml` into a `.streamlit` folder in the repository. Commit to the branch used by Streamlit Community Cloud and wait for redeployment.

Generated report PNGs intentionally retain their existing print/share-friendly report styling; this update applies dark mode to the website interface itself.

Use real company data only in a company-approved hosting environment.
