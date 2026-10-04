#!/usr/bin/env python3
"""
LinkedIn Games results collector.

Collects from the LinkedIn Games UI:
- game name / game number
- your completion time
- today's average time (from Achievements)
- number of connections who played today
- your rank
- streak
- hints / mistakes when visible
- number of players sharing your rank

Notes
-----
LinkedIn does not expose a public Games-results API. This script automates the
website UI and may break if LinkedIn changes its UI. It does not bypass CAPTCHA,
checkpoints, or anti-bot protections.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request, error

from playwright.sync_api import sync_playwright, Page, Locator, TimeoutError as PlaywrightTimeoutError

DEFAULT_GAMES = [
    "zip",
    "patches",
    "mini-sudoku",
    "tango",
    "queens",
    "wend",
]

CSV_BASE_COLUMNS = [
    "Date",
    "Game",
    "Place",
    "My Time",
    "Avg Time",
    "Diff vs Avg (sec)",
]
CSV_EXTRA_COLUMNS = ["Connections Played", "Same Rank"]

GAME_DISPLAY_NAMES = {
    "zip": "Zip",
    "patches": "Patches",
    "mini-sudoku": "Mini Sudoku",
    "tango": "Tango",
    "queens": "Queens",
    "wend": "Wend",
}

GAME_URL = "https://www.linkedin.com/games/{game}/"
PROFILE_DIR = Path(".linkedin_profile")
OUTPUT_DIR = Path("results")


def clean_text(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value.replace("\xa0", " ")).strip()


def first_match(patterns: list[str], text: str, flags: int = re.I | re.M) -> str | None:
    for pattern in patterns:
        m = re.search(pattern, text, flags)
        if m:
            return clean_text(m.group(1))
    return None


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value.replace(",", ""))
    except ValueError:
        return None


def parse_main_result_text(game: str, text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}

    result["game_number"] = parse_int(first_match(
        [
            rf"\b{re.escape(game)}\s*#\s*(\d+)\b",
            rf"\b{re.escape(game)}\s+(?:No\.?|#)\s*(\d+)\b",
        ],
        text,
    ))

    result["my_time"] = first_match(
        [
            r"\bSolved in\s+(\d{1,2}:\d{2}(?::\d{2})?)\b",
            r"\b(?:My|Your)\s+time\s*[:\-]?\s*(\d{1,2}:\d{2}(?::\d{2})?)\b",
            r"\b(\d{1,2}:\d{2}(?::\d{2})?)\s+Today[\u2019']s\s+avg",
        ],
        text,
    )

    result["connections_played_today"] = parse_int(first_match(
        [
            r"\b([\d,]+)\s+connections?\s+played\s+today\b",
            r"\b([\d,]+)\s+connection[s]?\s+played\s+today\b",
        ],
        text,
    ))

    result["streak_days"] = parse_int(first_match(
        [
            r"\b(\d+)[-\s]?day\s+streak\b",
            r"\b(\d+)[-\s]?day\s+win\s+streak\b",
            r"\bstreak\s*[:\-]?\s*(\d+)\b",
        ],
        text,
    ))

    hints_text = first_match(
        [
            r"\b(\d+)\s+hints?\b",
            r"\bHints?\s*[:\-]?\s*(\d+)\b",
        ],
        text,
    )
    if re.search(r"\bNo hints!?\b", text, re.I):
        result["hints"] = 0
    else:
        result["hints"] = parse_int(hints_text)

    mistakes = first_match(
        [
            r"\b(\d+)\s+mistakes?\b",
            r"\bMistakes?\s*[:\-]?\s*(\d+)\b",
        ],
        text,
    )
    result["mistakes"] = parse_int(mistakes)

    return result


def parse_achievements_text(text: str) -> dict[str, Any]:
    return {
        "today_average_time": first_match(
            [
                r"\bToday['’]s\s+avg\.?\s*[:\-]?\s*(\d{1,2}:\d{2}(?::\d{2})?)\b",
                r"\bToday['’]s\s+average\s*[:\-]?\s*(\d{1,2}:\d{2}(?::\d{2})?)\b",
            ],
            text,
        ),
        "streak_days": parse_int(first_match(
            [
                r"\b(\d+)[-\s]?day\s+streak\b",
                r"\bOn fire\s+(\d+)[-\s]?day\s+streak\b",
            ],
            text,
        )),
        "hints": 0 if re.search(r"\bNo hints\b", text, re.I) else parse_int(first_match(
            [
                r"\b(\d+)\s+hints?\b",
                r"\bHints?\s*[:\-]?\s*(\d+)\b",
            ],
            text,
        )),
    }


def body_text(page: Page) -> str:
    return clean_text(page.locator("body").inner_text(timeout=10_000))


def looks_logged_out(page: Page) -> bool:
    # Public game pages can show no sign-in text even without an account session.
    if not any(
        cookie["name"] == "li_at" and cookie["value"]
        for cookie in page.context.cookies("https://www.linkedin.com/")
    ):
        return True

    url_l = page.url.lower()
    if "/login" in url_l or "/checkpoint/" in url_l:
        return True

    try:
        text = body_text(page).lower()
    except Exception:
        return False

    markers = ("sign in to linkedin", "join linkedin", "email or phone")
    return sum(marker in text for marker in markers) >= 2


def wait_for_manual_login(page: Page) -> None:
    print("\nLinkedIn login is required.")
    page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    print("Log in manually in the browser window.")
    while True:
        input("When you are fully logged in, press Enter here... ")
        if not looks_logged_out(page):
            break
        print("Login is not complete yet. Finish logging in in the browser.")
    page.goto("https://www.linkedin.com/games/", wait_until="domcontentloaded")
    page.wait_for_timeout(1200)

    if looks_logged_out(page):
        raise RuntimeError("LinkedIn still appears to be logged out.")


def click_text_like(page: Page, labels: list[str], timeout_ms: int = 3000) -> bool:
    """
    Try accessible role / text / button matches. Avoid brittle CSS selectors.
    """
    for label in labels:
        candidates = [
            page.get_by_role("button", name=re.compile(re.escape(label), re.I)),
            page.get_by_role("link", name=re.compile(re.escape(label), re.I)),
            page.get_by_text(re.compile(re.escape(label), re.I), exact=False),
        ]
        for loc in candidates:
            try:
                if loc.count() > 0:
                    target = loc.first
                    target.scroll_into_view_if_needed(timeout=timeout_ms)
                    target.click(timeout=timeout_ms)
                    return True
            except Exception:
                continue
    return False


def go_back_safely(page: Page) -> None:
    try:
        page.go_back(wait_until="domcontentloaded", timeout=10_000)
        page.wait_for_timeout(800)
    except Exception:
        pass


def read_achievements(page: Page, debug: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "today_average_time": None,
        "streak_days": None,
        "hints": None,
    }

    clicked = click_text_like(page, ["Achievements"])
    if not clicked:
        return result

    page.wait_for_timeout(1200)
    text = body_text(page)
    parsed = parse_achievements_text(text)
    result.update(parsed)

    if debug:
        result["_achievements_raw_text"] = text[:15000]

    # Prefer an explicit close/back control, otherwise browser history.
    if not click_text_like(page, ["Close", "Back"], timeout_ms=1200):
        go_back_safely(page)
    page.wait_for_timeout(700)
    return result


def extract_rank_entries_from_visible_text(text: str) -> list[tuple[int, str]]:
    """
    Extract visible leaderboard rank/time pairs from rendered text.
    This is deliberately generic because names and badge text vary.
    """
    entries: list[tuple[int, str]] = []
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]

    # Common mobile layout: rank on one line, name on next, time shortly after.
    for i, line in enumerate(lines):
        m_rank = re.fullmatch(r"(\d+)", line)
        if not m_rank:
            continue

        rank = int(m_rank.group(1))
        window = " ".join(lines[i:i+5])
        m_time = re.search(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\b", window)
        if m_time:
            entries.append((rank, m_time.group(1)))

    return entries


def find_you_rank(text: str) -> int | None:
    """
    Looks for a rank near the 'You' row.
    """
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]
    for i, line in enumerate(lines):
        if re.fullmatch(r"You", line, re.I):
            for j in range(max(0, i - 3), i):
                m = re.fullmatch(r"(\d+)", lines[j])
                if m:
                    return int(m.group(1))
    return None


def leaderboard_container(page: Page) -> Locator:
    """
    Pick the most likely scrollable leaderboard container.
    Fallback to body.
    """
    candidates = page.locator("div").filter(has_text=re.compile(r"Leaderboard", re.I))
    try:
        count = min(candidates.count(), 25)
        best = None
        best_area = 0
        for i in range(count):
            loc = candidates.nth(i)
            try:
                box = loc.bounding_box()
                if not box:
                    continue
                area = box["width"] * box["height"]
                if area > best_area:
                    best = loc
                    best_area = area
            except Exception:
                pass
        if best is not None:
            return best
    except Exception:
        pass
    return page.locator("body")


def summarize_leaderboard(rows: list[dict[str, Any]], expected_count: int | None) -> dict[str, Any]:
    timed_rows = [row for row in rows if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", row["score"])]
    result: dict[str, Any] = {
        "my_rank": None, "same_rank_total": None, "same_rank_others": None,
        "leaderboard_rows_seen": len(timed_rows),
        "rank_source": None,
        "leaderboard_complete": bool(expected_count and len(rows) >= expected_count),
        "connections_played_today": None,
    }
    if result["leaderboard_complete"]:
        result["connections_played_today"] = sum(row["name"].lower() != "you" for row in timed_rows)
    mine = next((row for row in rows if row["name"].lower() == "you"), None)
    if not mine:
        return result
    explicit_rank = parse_int(mine["rank"])
    if explicit_rank is not None:
        result["my_rank"] = explicit_rank
        result["rank_source"] = "displayed"
    # Missing rows could contain faster players or additional ties.
    if not result["leaderboard_complete"]:
        return result
    rows = timed_rows
    if mine not in rows:
        return result
    def seconds(score: str) -> int:
        total = 0
        for part in score.split(":"):
            total = total * 60 + int(part)
        return total
    my_seconds = seconds(mine["score"])
    if explicit_rank is None:
        # LinkedIn uses dense ranks: equal times share a rank, then the next
        # distinct time receives the next rank (as shown by numeric ranks 4+).
        result["my_rank"] = 1 + len({seconds(row["score"]) for row in rows if seconds(row["score"]) < my_seconds})
        result["rank_source"] = "calculated_from_connection_times"
    tied = sum(seconds(row["score"]) == my_seconds for row in rows)
    result["same_rank_total"] = tied
    result["same_rank_others"] = tied - 1
    return result


def collect_full_leaderboard(page: Page, expected_count: int | None, debug: bool) -> dict[str, Any]:
    result = summarize_leaderboard([], expected_count)
    if not click_text_like(page, ["See full leaderboard"]):
        return result
    selector = ".pr-connections-leaderboard-player__container"
    page.locator(selector).first.wait_for(timeout=15_000)
    seen: dict[str, dict[str, Any]] = {}
    stable_rounds = 0
    for _ in range(100):
        rows = page.locator(selector).evaluate_all("""elements => elements.map(el => {
            const text = suffix => el.querySelector(
                '.pr-connections-leaderboard-player__' + suffix)?.innerText.trim() || '';
            const image = el.querySelector('.pr-connections-leaderboard-player__image-container img');
            return {
                name: text('name-text'), score: text('score'), rank: text('ranking'),
                identity: (image?.getAttribute('src') || '') + '|' + text('name-text')
            };
        })""")
        previous_count = len(seen)
        for row in rows:
            if row["name"] and row["score"]:
                seen[row["identity"]] = row
        stable_rounds = stable_rounds + 1 if len(seen) == previous_count else 0
        if stable_rounds >= 5:
            break
        # Resolve and scroll in one synchronous browser evaluation. LinkedIn
        # can replace rows while Playwright waits for an element to stabilize.
        page.locator(selector).evaluate_all("""elements => {
            const el = elements[elements.length - 1];
            if (!el || !el.isConnected) return;
            let parent = el.parentElement;
            while (parent && parent !== document.body) {
                if (/auto|scroll/.test(getComputedStyle(parent).overflowY)
                    && parent.scrollHeight > parent.clientHeight) {
                    parent.scrollTop = parent.scrollHeight;
                    return;
                }
                parent = parent.parentElement;
            }
            window.scrollTo(0, document.documentElement.scrollHeight);
        }""")
        page.wait_for_timeout(1000)
    result = summarize_leaderboard(list(seen.values()), expected_count)
    go_back_safely(page)
    return result


def open_results(page: Page, game_url: str) -> None:
    """Use a full navigation to avoid stalled client-side results transitions."""
    results_url = game_url.rstrip("/") + "/results/"
    for attempt in range(2):
        try:
            page.goto(results_url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_function(
                """() => /Copy score|Today['\u2019]s\\s+(?:avg|average)|connections? played today/i
                    .test(document.body?.innerText || '')""",
                timeout=30_000,
            )
            return
        except PlaywrightTimeoutError:
            if attempt == 1:
                raise
            print(f"Results page stalled; retrying {results_url}")


def collect_game(page: Page, game: str, debug: bool) -> dict[str, Any]:
    url = GAME_URL.format(game=game)

    item: dict[str, Any] = {
        "game": game,
        "url": url,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "unknown",
        "game_number": None,
        "my_time": None,
        "today_average_time": None,
        "connections_played_today": None,
        "my_rank": None,
        "same_rank_total": None,
        "same_rank_others": None,
        "streak_days": None,
        "hints": None,
        "mistakes": None,
        "leaderboard_rows_seen": 0,
    }

    try:
        print(f"[{game}] Opening {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(2200)

        if looks_logged_out(page):
            item["status"] = "logged_out"
            return item

        landing_text = body_text(page)
        if debug:
            item["_landing_raw_text"] = landing_text[:30000]
        if re.search(r"\b(?:See|View) results\b", landing_text, re.I):
            open_results(page, url)

        main_text = body_text(page)
        main = parse_main_result_text(game, main_text)
        item.update({k: v for k, v in main.items() if v is not None})
        item["today_average_time"] = first_match(
            [r"\bToday[\u2019']s\s+(?:avg\.?|average)\s*[:\-]?\s*(\d{1,2}:\d{2}(?::\d{2})?)\b"],
            main_text,
        )

        # Achievements contains today's average.
        achievements = read_achievements(page, debug)
        if achievements.get("today_average_time") is not None:
            item["today_average_time"] = achievements["today_average_time"]
        if item["streak_days"] is None and achievements.get("streak_days") is not None:
            item["streak_days"] = achievements["streak_days"]
        if item["hints"] is None and achievements.get("hints") is not None:
            item["hints"] = achievements["hints"]

        # Full leaderboard gives rank and tie count.
        leaderboard = collect_full_leaderboard(
            page,
            expected_count=item.get("connections_played_today"),
            debug=debug,
        )
        # Keep the displayed total: leaderboard rows with timed scores can
        # represent fewer connections than LinkedIn's "played today" count.
        for key in ("my_rank", "same_rank_total", "same_rank_others", "leaderboard_rows_seen", "rank_source", "leaderboard_complete"):
            if leaderboard.get(key) is not None:
                item[key] = leaderboard[key]

        meaningful = any(
            item.get(key) is not None
            for key in (
                "my_time",
                "today_average_time",
                "connections_played_today",
                "my_rank",
            )
        )

        if meaningful:
            item["status"] = "collected"
        elif re.search(r"\bstart game\b", main_text, re.I):
            item["status"] = "not_played_or_result_not_visible"
        elif re.search(r"\bcontinue game\b|\bresume\b", main_text, re.I):
            item["status"] = "in_progress"
        else:
            item["status"] = "page_loaded_no_known_metrics"

        if debug:
            item["_main_raw_text"] = main_text[:30000]
            if "_achievements_raw_text" in achievements:
                item["_achievements_raw_text"] = achievements["_achievements_raw_text"]

        print(
            f"[{game}] {item['status']} | "
            f"time={item.get('my_time')} | "
            f"avg={item.get('today_average_time')} | "
            f"connections={item.get('connections_played_today')} | "
            f"rank={item.get('my_rank')} | "
            f"same-rank-others={item.get('same_rank_others')}"
        )
        return item

    except Exception as exc:
        item["status"] = "error"
        if debug:
            try:
                item["_error_url"] = page.url
                item["_error_raw_text"] = body_text(page)[:30000]
            except Exception:
                pass
        item["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[{game}] ERROR: {exc}", file=sys.stderr)
        return item



def time_to_seconds(value: str | None) -> int | None:
    """Convert m:ss or h:mm:ss to total seconds."""
    if not value:
        return None
    parts = value.strip().split(":")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def csv_game_name(slug: str) -> str:
    return GAME_DISPLAY_NAMES.get(slug, slug.replace("-", " ").title())


def build_csv_values(game: dict[str, Any], date_value: str) -> dict[str, str]:
    my_time = game.get("my_time")
    avg_time = game.get("today_average_time")
    my_seconds = time_to_seconds(my_time)
    avg_seconds = time_to_seconds(avg_time)
    diff = ""
    if my_seconds is not None and avg_seconds is not None:
        diff = str(my_seconds - avg_seconds)

    return {
        "Date": date_value,
        "Game": csv_game_name(str(game.get("game", ""))),
        "Place": "" if game.get("my_rank") is None else str(game["my_rank"]),
        "My Time": "" if my_time is None else str(my_time),
        "Avg Time": "" if avg_time is None else str(avg_time),
        "Diff vs Avg (sec)": diff,
        "Connections Played": (
            "" if game.get("connections_played_today") is None
            else str(game["connections_played_today"])
        ),
        "Same Rank": (
            "" if game.get("same_rank_others") is None
            else str(game["same_rank_others"])
        ),
    }


def update_csv(csv_path: Path, payload: dict[str, Any]) -> tuple[int, int, int]:
    """
    Upsert completed game results into CSV using Date + Game as the key.

    Existing extra columns (for example Note) are preserved. Existing non-empty
    values are not erased when a scraper field is temporarily unavailable.
    """
    csv_path = csv_path.resolve()
    rows: list[dict[str, str]] = []
    fieldnames: list[str] = []

    if csv_path.exists() and csv_path.stat().st_size > 0:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = [dict(row) for row in reader]

    if not fieldnames:
        fieldnames = CSV_BASE_COLUMNS.copy()

    # Preserve all current columns and append only our new columns when missing.
    for column in CSV_BASE_COLUMNS + CSV_EXTRA_COLUMNS:
        if column not in fieldnames:
            fieldnames.append(column)

    # The CSV is daily, so use the machine's local date. On the Linux host, set
    # the system timezone to Asia/Jerusalem so the date matches the LinkedIn day.
    date_value = datetime.now().strftime("%d/%m/%Y")

    index: dict[tuple[str, str], int] = {}
    for i, row in enumerate(rows):
        key = ((row.get("Date") or "").strip(), (row.get("Game") or "").strip().lower())
        if key[0] and key[1]:
            index[key] = i

    inserted = 0
    updated = 0
    skipped = 0

    for game in payload.get("games", []):
        # Do not create a row before the user has actually completed the game.
        if game.get("status") != "collected" or not game.get("my_time"):
            skipped += 1
            continue

        values = build_csv_values(game, date_value)
        key = (date_value, values["Game"].lower())

        if key in index:
            row = rows[index[key]]
            changed = False
            for column, value in values.items():
                # Never erase a useful value because one scrape was partial.
                if value != "" and row.get(column, "") != value:
                    row[column] = value
                    changed = True
            if changed:
                updated += 1
        else:
            row = {column: "" for column in fieldnames}
            row.update(values)
            rows.append(row)
            index[key] = len(rows) - 1
            inserted += 1

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return inserted, updated, skipped


def post_to_api(api_url: str, api_token: str | None, payload: dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    req = request.Request(api_url, data=data, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=20) as response:
            print(f"API POST: HTTP {response.status}")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API returned HTTP {exc.code}: {body[:500]}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach API: {exc}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect your LinkedIn Games result stats.")
    parser.add_argument("--games", nargs="+", default=DEFAULT_GAMES)
    parser.add_argument("--profile-dir", default=str(PROFILE_DIR))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without a visible browser. Use after a session is saved.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Include raw rendered text for parser tuning.",
    )
    parser.add_argument(
        "--csv",
        help="Optional path to games_log.csv. Completed games are upserted by Date + Game.",
    )
    parser.add_argument("--api-url")
    parser.add_argument("--api-token")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    profile_dir = Path(args.profile_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "source": "linkedin_games",
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "games": [],
    }

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=args.headless,
            viewport={"width": 1440, "height": 1000},
            locale="en-US",
        )
        page = context.pages[0] if context.pages else context.new_page()

        page.goto("https://www.linkedin.com/games/", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)

        if looks_logged_out(page):
            if args.headless:
                print(
                    "Saved LinkedIn session is missing/expired. "
                    "Run once without --headless and log in manually.",
                    file=sys.stderr,
                )
                context.close()
                return 2
            wait_for_manual_login(page)

        for game in args.games:
            payload["games"].append(
                collect_game(page, game.strip().lower(), args.debug)
            )
            time.sleep(0.5)

        context.close()

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = output_dir / f"linkedin_games_{stamp}.json"
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved: {output_path}")

    if args.csv:
        inserted, updated, skipped = update_csv(Path(args.csv), payload)
        print(
            f"CSV updated: {Path(args.csv).resolve()} "
            f"({inserted} inserted, {updated} updated, {skipped} skipped)"
        )

    if args.api_url:
        post_to_api(args.api_url, args.api_token, payload)

    collected = sum(1 for g in payload["games"] if g["status"] == "collected")
    print(f"Collected known metrics for {collected}/{len(payload['games'])} games.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
