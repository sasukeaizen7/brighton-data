"""Download the source files into data/ (not committed).

    python src/download.py
"""
import urllib.request

from common import RAW, RAW_TRANSFERS, SEASONS

MATCHES_URL = "https://www.football-data.co.uk/mmz4281/{season}/E0.csv"
TRANSFERS_URL = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/transfers.csv.gz"


def download(url: str, target) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "brighton-data"})
    with urllib.request.urlopen(request, timeout=120) as response:
        target.write_bytes(response.read())
    print("downloaded", target.name)


def main() -> None:
    for season in SEASONS:
        download(MATCHES_URL.format(season=season), RAW / f"E0_{season}.csv")
    download(TRANSFERS_URL, RAW_TRANSFERS / "transfers.csv.gz")


if __name__ == "__main__":
    main()
