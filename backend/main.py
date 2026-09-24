"""
Backend for the LinkedIn Games tracker.

Reads games_log.csv and serves it as JSON for the
frontend. Re-reads the file on every request, so you can just keep editing
CSV file and refresh the browser to see updates -- no restart needed.

Run:
    pip install -r requirements.txt
    python main.py

Then open http://localhost:8000
"""

import re
import io
import tempfile
from pathlib import Path
from threading import Lock

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

BASE_DIR = Path(__file__).resolve().parent.parent
CSV_PATH = BASE_DIR / "games_log.csv"
FRONTEND_DIR = BASE_DIR / "frontend"
LOG_LOCK = Lock()

app = FastAPI(title="LinkedIn Games Tracker")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_log() -> pd.DataFrame:
    # Dashboard requests may arrive concurrently; serialize read/update cycles.
    with LOG_LOCK:
        return _load_log()


def _load_log() -> pd.DataFrame:
    if not CSV_PATH.exists():
        raise HTTPException(status_code=500, detail=f"CSV file not found at {CSV_PATH}")

    original = CSV_PATH.read_bytes()
    source = pd.read_csv(io.BytesIO(original), encoding="utf-8-sig", dtype=str,
                         keep_default_na=False)
    df = source.replace("", None).copy()
    df = df.dropna(subset=["Date", "Game"])  # ignore blank template rows

    def to_seconds(value):
        if pd.isna(value):
            return None
        value = str(value).strip()
        if re.fullmatch(r"\d+:[0-5]\d", value):
            minutes, seconds = map(int, value.split(":"))
            return minutes * 60 + seconds
        if re.fullmatch(r"\d+:[0-5]\d:[0-5]\d", value):
            hours, minutes, seconds = map(int, value.split(":"))
            return hours * 3600 + minutes * 60 + seconds
        return None

    df["my_time_seconds"] = df["My Time"].apply(to_seconds)
    df["avg_time_seconds"] = df["Avg Time"].apply(to_seconds)
    df["diff_seconds"] = df["my_time_seconds"] - df["avg_time_seconds"]
    # Recalculate differences so the CSV's derived column can be left blank.
    df["Diff vs Avg (sec)"] = df["diff_seconds"]

    df["Date"] = pd.to_datetime(df["Date"]).dt.date.astype(str)
    df["Place"] = pd.to_numeric(df["Place"], errors="coerce")

    # Only persist the derived column, preserving input text and template rows.
    differences = df["diff_seconds"].apply(
        lambda value: "" if pd.isna(value) else str(int(value))
    ).reindex(source.index, fill_value="")
    column = "Diff vs Avg (sec)"
    if column not in source or not source[column].equals(differences):
        source[column] = differences
        temporary_path = None
        try:
            encoding = "utf-8-sig" if original.startswith(b"\xef\xbb\xbf") else "utf-8"
            with tempfile.NamedTemporaryFile(
                mode="w", encoding=encoding, newline="", dir=CSV_PATH.parent,
                suffix=".tmp", delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                source.to_csv(temporary, index=False)
            if CSV_PATH.read_bytes() != original:
                raise HTTPException(status_code=409, detail="CSV changed while loading; refresh to retry.")
            temporary_path.replace(CSV_PATH)
        except OSError as error:
            raise HTTPException(status_code=500, detail="Could not update CSV differences. Check that the file is writable and not locked.") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

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
