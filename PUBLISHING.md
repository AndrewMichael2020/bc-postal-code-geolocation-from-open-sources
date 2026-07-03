# Publishing Checklist

Target repository:

```text
AndrewMichael2020/bc-postal-code-geolocation-from-open-sources
```

This folder is prepared for upload, but no Git remote operation has been performed.

Before publishing:

1. Review generated-data policy in `.gitignore`.
2. Confirm no `.env`, raw API responses, or generated CSVs are staged.
3. Run:

```bash
python scripts/test_postal_reconstruction.py
python -m py_compile scripts/*.py
```

Suggested first-push commands:

```bash
cd /Users/antvibe/Documents/Dev/work/BC_Postal_codes/bc-postal-code-geolocation-from-open-sources
git init
git branch -M main
git remote add origin git@github.com:AndrewMichael2020/bc-postal-code-geolocation-from-open-sources.git
git add .
git commit -m "Initial greenfield BC postal geolocation workflow"
git push -u origin main
```
