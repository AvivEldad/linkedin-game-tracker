# Games Log

A tiny local app: you fill in `games_log.csv` each day, the backend reads
it, and the frontend shows your stats and trends.

## Structure

```
games-tracker-app/
├── games_log.csv      <- you edit this daily
├── backend/
│   ├── main.py          <- FastAPI: reads the CSV, serves JSON + the frontend
│   └── requirements.txt
└── frontend/
    └── index.html        <- the dashboard (served automatically by the backend)
```

## The CSV file

Open `games_log.csv` in a text editor or spreadsheet app. It has these columns:

| Date | Game | Place | My Time | Avg Time | Diff vs Avg (sec) |
|------|------|-------|---------|----------|--------------------|

- One row per game per day.
- **Date**: use `YYYY-MM-DD` (e.g. `2026-09-14`).
- **My Time** / **Avg Time**: use `m:ss` (e.g. `1:05`). `h:mm:ss` is also supported.
- **Diff vs Avg (sec)**: you can leave this blank; the backend calculates it
  and saves it back to column F when the dashboard loads or refreshes
  (positive = slower than average). Save your edits before refreshing.
  Missing or invalid times leave the difference blank.
- Leave missing times or places empty.
- Save as a comma-separated UTF-8 CSV, keeping the header row. CSV files
  do not store Excel formatting, formulas, or additional sheets.
- Keep game names spelled exactly the same each time (e.g. always
  "Queens", not "queens") — the app groups by this text.

## Running the app

```bash
cd backend
pip install -r requirements.txt
python main.py
```

Then open **http://localhost:8000** in your browser.

Each time you add rows to the CSV file and refresh the browser tab, the
dashboard updates — no restart needed.

## What the dashboard shows

- A card per game with today's place and how you compared to the average.
- Click a card to see that game's time trend (you vs. average) over time.
- An all-time stats table: entries logged, best place, average place,
  average time vs. the daily average.

## Where this goes next

This is intentionally the simple version. When you're ready to swap in the
automated LinkedIn scraper + Postgres setup from earlier, only `main.py`'s
`load_log()` function needs to change (read from the database instead of
the CSV file) — the API shape and the whole frontend stay the same.
