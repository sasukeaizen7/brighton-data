"""Brighton transfers from transfermarkt-datasets (CC0 repository, snapshot to 6 July 2026).

    python src/transfers.py
"""
import csv
import gzip
import re
import sys

from common import OUT, RAW_TRANSFERS, write_csv, write_json

BRIGHTON = "1237"
SNAPSHOT = "2026-07-06"
# Complete seasons only: 2026/27 had barely started when the snapshot was taken.
SEASONS = ["17/18", "18/19", "19/20", "20/21", "21/22", "22/23", "23/24", "24/25", "25/26"]

TABLES = {
    "transfers": [["season", "string"], ["transfer_date", "date"], ["player", "string"], ["direction", "string"],
                  ["other_club", "string"], ["fee_eur", "int"], ["spent_eur", "int"], ["received_eur", "int"]],
    "trades": [["player", "string"], ["bought_season", "string"], ["buy_fee_eur", "int"], ["sold_season", "string"],
               ["sold_to", "string"], ["sell_fee_eur", "int"], ["gain_eur", "int"]],
}


def read() -> list[dict]:
    with gzip.open(RAW_TRANSFERS / "transfers.csv.gz", "rt", encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f)
                if (r["from_club_id"] == BRIGHTON) != (r["to_club_id"] == BRIGHTON) and r["transfer_season"] in SEASONS]


def is_fee(value: str) -> bool:
    try:
        return float(value) >= 0
    except ValueError:
        return False


def quality(records: list[dict]) -> list[dict]:
    def check(rule: str, severity: str, failed: int) -> dict:
        return {"rule": rule, "severity": severity, "failed": failed}

    keys = {(r["player_id"], r["transfer_date"], r["from_club_id"], r["to_club_id"]) for r in records}
    return [
        check("invalid_date", "blocking",
              sum(not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["transfer_date"]) for r in records)),
        check("negative_or_non_numeric_fee", "blocking",
              sum(r["transfer_fee"] != "" and not is_fee(r["transfer_fee"]) for r in records)),
        check("duplicate_transfer", "blocking", len(records) - len(keys)),
        check("dated_after_snapshot", "excluded", sum(r["transfer_date"] > SNAPSHOT for r in records)),
        check("fee_unknown", "info", sum(r["transfer_fee"] == "" for r in records)),
        check("fee_zero_free_or_loan", "info",
              sum(r["transfer_fee"] != "" and float(r["transfer_fee"]) == 0 for r in records)),
    ]


def to_row(record: dict) -> dict:
    arrival = record["to_club_id"] == BRIGHTON
    fee = "" if record["transfer_fee"] == "" else round(float(record["transfer_fee"]))
    return {
        "season": f"20{record['transfer_season']}", "transfer_date": record["transfer_date"],
        "player_id": record["player_id"], "player": record["player_name"],
        "direction": "In" if arrival else "Out",
        "other_club": record["from_club_name"] if arrival else record["to_club_name"],
        "fee_eur": fee,
        "spent_eur": fee if arrival and fee != "" else 0,
        "received_eur": fee if not arrival and fee != "" else 0,
    }


def match_trades(rows: list[dict]) -> list[dict]:
    """A trade = a paid departure matched with the latest earlier paid arrival of the same player."""
    trades = []
    for sale in rows:
        if sale["direction"] != "Out" or not sale["received_eur"]:
            continue
        buys = [b for b in rows if b["player_id"] == sale["player_id"] and b["direction"] == "In"
                and b["spent_eur"] and b["transfer_date"] < sale["transfer_date"]]
        if buys:
            buy = buys[-1]
            trades.append({
                "player": sale["player"], "bought_season": buy["season"], "buy_fee_eur": buy["spent_eur"],
                "sold_season": sale["season"], "sold_to": sale["other_club"], "sell_fee_eur": sale["received_eur"],
                "gain_eur": sale["received_eur"] - buy["spent_eur"],
            })
    return sorted(trades, key=lambda t: t["gain_eur"], reverse=True)


def summary(rows: list[dict], trades: list[dict]) -> None:
    spent = sum(r["spent_eur"] for r in rows)
    received = sum(r["received_eur"] for r in rows)
    print(f"spent {spent / 1e6:.1f} | received {received / 1e6:.1f} | balance {(received - spent) / 1e6:.1f} M EUR")
    print(f"matched trades {len(trades)} | with gain {sum(t['gain_eur'] > 0 for t in trades)}"
          f" | total gain {sum(t['gain_eur'] for t in trades) / 1e6:.1f} M EUR")


def main() -> None:
    records = read()
    checks = quality(records)
    write_csv("transfer_quality", ["rule", "severity", "failed"], checks)
    blocking = [c for c in checks if c["severity"] == "blocking" and c["failed"]]
    if blocking:
        sys.exit(f"Blocked, nothing published: {blocking}")
    rows = sorted((to_row(r) for r in records if r["transfer_date"] <= SNAPSHOT), key=lambda r: r["transfer_date"])
    trades = match_trades(rows)
    for name, data in (("transfers", rows), ("trades", trades)):
        write_csv(name, [column for column, _ in TABLES[name]], data)
    write_json(OUT / "transfer_schema.json", TABLES)
    summary(rows, trades)


if __name__ == "__main__":
    main()
