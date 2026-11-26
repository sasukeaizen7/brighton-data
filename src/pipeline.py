"""Raw match files -> one row per team per match, published only if every blocking check passes.

    python src/pipeline.py
"""
import csv
import re
import sys

from common import CURRENT, OUT, RAW, SEASONS, write_csv, write_json

# Single source of truth for the published columns; the Power BI model is generated from it.
SCHEMA = [
    ["season", "string"], ["match_date", "date"], ["team", "string"], ["opponent", "string"], ["venue", "string"],
    ["goals_for", "int"], ["goals_against", "int"], ["points", "int"],
    ["shots_for", "int"], ["shots_against", "int"], ["sot_for", "int"], ["sot_against", "int"],
    ["xg_for", "decimal"], ["xg_against", "decimal"],
]
STATS = ["FTHG", "FTAG", "HS", "AS", "HST", "AST"]


def label(season: str) -> str:
    return f"20{season[:2]}/{season[2:]}" + (" (in progress)" if season == CURRENT else "")


def read(season: str) -> list[dict]:
    with open(RAW / f"E0_{season}.csv", encoding="utf-8-sig", newline="") as f:
        return [row for row in csv.DictReader(f) if row.get("HomeTeam")]


def number(value: str) -> int:
    return int(value) if value.isdigit() else 0


def bad_shots(match: dict) -> bool:
    return (number(match["HST"]) > number(match["HS"])) or (number(match["AST"]) > number(match["AS"]))


def result(match: dict) -> str:
    home, away = number(match["FTHG"]), number(match["FTAG"])
    return "H" if home > away else "A" if home < away else "D"


def quality(season: str, matches: list[dict]) -> list[dict]:
    def check(rule: str, severity: str, failed: int) -> dict:
        return {"season": season, "rule": rule, "severity": severity, "failed": failed}

    checks = [
        check("missing_or_non_numeric_stat", "blocking",
              sum(any(not (m.get(k) or "").isdigit() for k in STATS) for m in matches)),
        check("invalid_date", "blocking",
              sum(not re.fullmatch(r"\d{2}/\d{2}/\d{4}", m["Date"]) for m in matches)),
        check("duplicate_fixture", "blocking",
              len(matches) - len({(m["HomeTeam"], m["AwayTeam"]) for m in matches})),
        check("result_contradicts_score", "blocking", sum(m["FTR"] != result(m) for m in matches)),
        # Impossible shot counts: the score is kept, the shot statistics are blanked (never corrected by hand).
        check("shots_on_target_above_shots", "quarantine", sum(bad_shots(m) for m in matches)),
        # Own goals can push goals above shots on target: reported, not blocking.
        check("goals_above_shots_on_target", "info", sum(
            not bad_shots(m) and (number(m["FTHG"]) > number(m["HST"]) or number(m["FTAG"]) > number(m["AST"]))
            for m in matches)),
    ]
    if season != CURRENT:
        checks.append(check("season_incomplete", "blocking", 0 if len(matches) == 380 else 1))
    return checks


def team_row(season: str, match: dict, home: bool) -> dict:
    def pick(if_home: str, if_away: str) -> str:
        return match[if_home] if home else match[if_away]

    def shot(if_home: str, if_away: str):
        return "" if bad_shots(match) else number(pick(if_home, if_away))

    day, month, year = match["Date"].split("/")
    goals_for, goals_against = number(pick("FTHG", "FTAG")), number(pick("FTAG", "FTHG"))
    return {
        "season": label(season), "match_date": f"{year}-{month}-{day}",
        "team": pick("HomeTeam", "AwayTeam"), "opponent": pick("AwayTeam", "HomeTeam"),
        "venue": "Home" if home else "Away",
        "goals_for": goals_for, "goals_against": goals_against,
        "points": 3 if goals_for > goals_against else 1 if goals_for == goals_against else 0,
        "shots_for": shot("HS", "AS"), "shots_against": shot("AS", "HS"),
        "sot_for": shot("HST", "AST"), "sot_against": shot("AST", "HST"),
        "xg_for": (match.get("HxG" if home else "AxG") or ""), "xg_against": (match.get("AxG" if home else "HxG") or ""),
    }


def summary(rows: list[dict]) -> None:
    """Figures used to verify the dashboard and the post."""
    print("season | Brighton points per match | shot share | conversion")
    for season in SEASONS:
        brighton = [r for r in rows if r["season"] == label(season) and r["team"] == "Brighton"]
        with_shots = [r for r in brighton if r["shots_for"] != ""]
        shots_for = sum(r["shots_for"] for r in with_shots)
        shots_against = sum(r["shots_against"] for r in with_shots)
        print(f"{label(season)} | {sum(r['points'] for r in brighton) / len(brighton):.2f}"
              f" | {shots_for / (shots_for + shots_against):.1%}"
              f" | {sum(r['goals_for'] for r in with_shots) / shots_for:.1%}")


def main() -> None:
    checks, rows = [], []
    for season in SEASONS:
        matches = read(season)
        checks += quality(season, matches)
        for match in matches:
            if re.fullmatch(r"\d{2}/\d{2}/\d{4}", match["Date"]):
                rows += [team_row(season, match, True), team_row(season, match, False)]
    write_csv("quality", ["season", "rule", "severity", "failed"], checks)
    blocking = [c for c in checks if c["severity"] == "blocking" and c["failed"]]
    if blocking:
        sys.exit(f"Blocked, nothing published: {blocking}")
    write_csv("matches", [name for name, _ in SCHEMA], rows)
    write_json(OUT / "schema.json", SCHEMA)
    summary(rows)
    print(f"rows {len(rows)} | checks {len(checks)}")


if __name__ == "__main__":
    main()
