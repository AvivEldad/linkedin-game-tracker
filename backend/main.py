"""
Backend for the LinkedIn Games tracker.

Reads games_log.xlsx (the "Log" sheet) and serves it as JSON for the
frontend. Re-reads the file on every request, so you can just keep editing
the Excel file and refresh the browser to see updates -- no restart needed.

Run:
    pip install -r requirements.txt
    python main.py

Then open http://localhost:8000
"""

import datetime
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

BASE_DIR = Path(__file__).resolve().parent.parent
EXCEL_PATH = BASE_DIR / "games_log.xlsx"
FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(title="LinkedIn Games Tracker")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_log() -> pd.DataFrame:
    if not EXCEL_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Excel file not found at {EXCEL_PATH}")

    df = pd.read_excel(EXCEL_PATH, sheet_name="Log", engine="openpyxl")
    df = df.dropna(subset=["Date", "Game"])  # ignore blank template rows

    def to_seconds(value):
        if pd.isna(value):
            return None
        if isinstance(value, datetime.time):
            return value.hour * 3600 + value.minute * 60 + value.second
        if isinstance(value, datetime.datetime):
            return value.hour * 3600 + value.minute * 60 + value.second
        return None

    df["my_time_seconds"] = df["My Time"].apply(to_seconds)
    df["avg_time_seconds"] = df["Avg Time"].apply(to_seconds)
    df["diff_seconds"] = df["my_time_seconds"] - df["avg_time_seconds"]
    # Excel formula caches may be empty until Excel recalculates the workbook.
    df["Diff vs Avg (sec)"] = df["diff_seconds"]

    df["Date"] = pd.to_datetime(df["Date"]).dt.date.astype(str)
    df["Place"] = pd.to_numeric(df["Place"], errors="coerce")

    return df


def json_records(df: pd.DataFrame) -> list[dict]:
    """Represent missing spreadsheet values as JSON null instead of NaN."""
    return df.astype(object).where(pd.notna(df), None).to_dict(orient="records")


@app.get("/api/games")
def list_games():
    df = load_log()
    return sorted(df["Game"].dropna().unique().tolist())


@app.get("/api/summary")
def summary():
    """Latest entry per game, for the top-of-page cards."""
    df = load_log()
    latest = df.sort_values("Date").groupby("Game").tail(1)
    return json_records(latest.sort_values("Game"))


@app.get("/api/history")
def history(game: str | None = None):
    """Full time series, optionally filtered to one game."""
    df = load_log()
    if game:
        df = df[df["Game"] == game]
    df = df.sort_values("Date")
    return json_records(df)


@app.get("/api/stats")
def stats():
    """Per-game aggregates: best place, avg place, avg diff vs LinkedIn average."""
    df = load_log()
    grouped = df.groupby("Game").agg(
        entries=("Date", "count"),
        best_place=("Place", "min"),
        avg_place=("Place", "mean"),
        avg_diff_seconds=("diff_seconds", "mean"),
    ).reset_index()
    return json_records(grouped.round(1))


# Serve the frontend (static files) at the root
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
