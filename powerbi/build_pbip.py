"""Generate the Power BI project (PBIP: text files, versioned in git) from the pipeline output.

    python src/pipeline.py && python src/transfers.py && python powerbi/build_pbip.py

Then open powerbi/Brighton.pbip in Power BI Desktop. Column types come from the schema files written
by the pipeline. The data folder is a model parameter (DataFolder).
"""
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "out"
OUT = ROOT / "powerbi"
NAME = "Brighton"
MODEL = OUT / f"{NAME}.SemanticModel"
REPORT = OUT / f"{NAME}.Report"
THEME_FILE = "Brighton.json"


# ---------------------------------------------------------------- measures (DAX)

def brighton(measure: str) -> str:
    return f'CALCULATE([{measure}], matches[team] = "Brighton")'


def league(measure: str) -> str:
    return f"CALCULATE([{measure}], REMOVEFILTERS(matches[team]))"


def latest(measure: str, extra: str = "") -> str:
    return ("VAR s = CALCULATE(MAX(matches[season]), REMOVEFILTERS(matches)) "
            f"RETURN CALCULATE([{measure}], matches[season] = s{extra})")


def millions(table: str, column: str) -> str:
    return f"DIVIDE(SUM({table}[{column}]), 1000000)"


MEASURES = {
    "matches": [
        ("Matches played", "COUNTROWS(matches)", "#,0"),
        ("Total points", "SUM(matches[points])", "#,0"),
        ("Points per match", "DIVIDE([Total points], [Matches played])", "0.00"),
        ("Shot share",
         "DIVIDE(SUM(matches[shots_for]), SUM(matches[shots_for]) + SUM(matches[shots_against]))", "0.0%"),
        ("Conversion",
         "DIVIDE(CALCULATE(SUM(matches[goals_for]), NOT ISBLANK(matches[shots_for])), SUM(matches[shots_for]))",
         "0.0%"),
        ("Shots per match", "AVERAGE(matches[shots_for])", "0.0"),
        ("Shots conceded per match", "AVERAGE(matches[shots_against])", "0.0"),
        ("Brighton points per match", brighton("Points per match"), "0.00"),
        ("League points per match", league("Points per match"), "0.00"),
        ("Brighton shot share", brighton("Shot share"), "0.0%"),
        ("Even share", "0.5", "0%"),
        ("Brighton conversion", brighton("Conversion"), "0.0%"),
        ("League conversion", league("Conversion"), "0.0%"),
        ("Brighton shots", brighton("Shots per match"), "0.0"),
        ("Brighton shots conceded", brighton("Shots conceded per match"), "0.0"),
        ("Latest season points per match", latest("Brighton points per match"), "0.00"),
        ("Latest season shot share", latest("Brighton shot share"), "0.0%"),
        ("Latest season conversion", latest("Brighton conversion"), "0.0%"),
        ("Latest season matches", latest("Matches played", ', matches[team] = "Brighton"'), "#,0"),
    ],
    "transfers": [
        ("Spent (€M)", millions("transfers", "spent_eur"), "#,0.0"),
        ("Received (€M)", millions("transfers", "received_eur"), "#,0.0"),
        ("Balance (€M)", "[Received (€M)] - [Spent (€M)]", "#,0.0"),
    ],
    "trades": [
        ("Bought for (€M)", millions("trades", "buy_fee_eur"), "#,0.0"),
        ("Sold for (€M)", millions("trades", "sell_fee_eur"), "#,0.0"),
        ("Gain (€M)", millions("trades", "gain_eur"), "#,0.0"),
        ("Number of trades", "COUNTROWS(trades)", "#,0"),
    ],
}


# ---------------------------------------------------------------- helpers

def tag(*parts: str) -> str:
    """Stable lineage tag, so regenerating the model does not change every line."""
    h = hashlib.sha1(("brighton-data/" + "/".join(parts)).encode()).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-5{h[13:16]}-a{h[17:20]}-{h[20:32]}"


def quote(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_]+", name) else f"'{name}'"


def compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def write(path: Path, content) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, indent=2)
    path.write_text(content, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- semantic model (TMDL)

TMDL_TYPES = {"string": "string", "int": "int64", "decimal": "double", "date": "dateTime"}
M_TYPES = {"string": "type text", "int": "Int64.Type", "decimal": "type number", "date": "type date"}


def table_tmdl(table: str, columns: list) -> str:
    lines = [f"table {table}", f"\tlineageTag: {tag(table)}", ""]
    for name, expression, fmt in MEASURES[table]:
        lines += [f"\tmeasure {quote(name)} = {expression}", f"\t\tformatString: {fmt}",
                  f"\t\tlineageTag: {tag(table, 'measure', name)}", ""]
    for name, kind in columns:
        lines += [f"\tcolumn {name}", f"\t\tdataType: {TMDL_TYPES[kind]}"]
        if kind == "date":
            lines.append("\t\tformatString: dd/mm/yyyy")
        lines += [f"\t\tlineageTag: {tag(table, name)}", "\t\tsummarizeBy: none", f"\t\tsourceColumn: {name}", ""]
    types = ", ".join(f'{{"{name}", {M_TYPES[kind]}}}' for name, kind in columns)
    lines += [
        f"\tpartition {table} = m", "\t\tmode: import", "\t\tsource =", "\t\t\t\tlet",
        f'\t\t\t\t    Source = Csv.Document(File.Contents(DataFolder & "{table}.csv"), '
        '[Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),',
        "\t\t\t\t    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),",
        f'\t\t\t\t    Typed = Table.TransformColumnTypes(Headers, {{{types}}}, "en-US")',
        "\t\t\t\tin", "\t\t\t\t    Typed", "",
    ]
    return "\n".join(lines)


def build_model(schemas: dict) -> None:
    definition = MODEL / "definition"
    write(MODEL / "definition.pbism", {"version": "4.0", "settings": {}})
    write(definition / "database.tmdl", "database\n\tcompatibilityLevel: 1600\n")
    model = ["model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
             "\tdiscourageImplicitMeasures", "\tsourceQueryCulture: en-US", ""]
    write(definition / "model.tmdl", "\n".join(model + [f"ref table {t}" for t in schemas] + [""]))
    write(definition / "expressions.tmdl",
          f'expression DataFolder = "{DATA}{os.sep}" meta [IsParameterQuery=true, Type="Text", '
          f"IsParameterQueryRequired=true]\n\tlineageTag: {tag('DataFolder')}\n\n\tannotation PBI_ResultType = Text\n")
    for table, columns in schemas.items():
        write(definition / "tables" / f"{table}.tmdl", table_tmdl(table, columns))


# ---------------------------------------------------------------- report (PBIR-Legacy report.json)

def col(prop: str, table: str = "matches") -> tuple:
    return (table, prop, False)


def mea(prop: str, table: str = "matches") -> tuple:
    return (table, prop, True)


def literal(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def expr(field: tuple) -> dict:
    _, prop, is_measure = field
    return {("Measure" if is_measure else "Column"): {"Expression": {"SourceRef": {"Source": "t"}}, "Property": prop}}


def visual(name: str, kind: str, position: tuple, roles: dict, title: str,
           order: tuple | None = None, ascending: bool = False, labels: bool = False,
           objects: dict | None = None) -> dict:
    x, y, w, h = position
    fields = list(dict.fromkeys(f for refs in roles.values() for f in refs))
    table = fields[0][0]  # one table per visual
    query = {"Version": 2, "From": [{"Name": "t", "Entity": table, "Type": 0}],
             "Select": [{**expr(f), "Name": f"{table}.{f[1]}"} for f in fields]}
    if order:
        query["OrderBy"] = [{"Direction": 1 if ascending else 2, "Expression": expr(order)}]
    projections = {
        role: [{"queryRef": f"{table}.{f[1]}", **({"active": True} if i == 0 and role == "Category" else {})}
               for i, f in enumerate(refs)]
        for role, refs in roles.items()}
    escaped = title.replace("'", "''")
    config = {"name": name, "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": 0, "width": w, "height": h}}],
              "singleVisual": {"visualType": kind, "projections": projections, "prototypeQuery": query,
                               "drillFilterOtherVisuals": True,
                               "vcObjects": {"title": [{"properties": {
                                   "show": literal("true"), "text": literal(f"'{escaped}'"),
                                   "fontSize": literal("13D"),
                                   "fontColor": {"solid": {"color": literal("'#1c2430'")}},
                                   "bold": literal("true")}}]}}}
    extra = {}
    if labels:
        extra["labels"] = [{"properties": {"show": literal("true"), "labelDisplayUnits": literal("1D")}}]
    extra.update(objects or {})
    if extra:
        config["singleVisual"]["objects"] = extra
    return {"x": x, "y": y, "z": 0, "width": w, "height": h, "config": compact(config), "filters": "[]"}


def card(name: str, position: tuple, measure: str, title: str, table: str = "matches") -> dict:
    # display units 1 = show the full number; the transfer amounts are already scaled to millions
    objects = {"labels": [{"properties": {"labelDisplayUnits": literal("1D")}}]} if table == "matches" else None
    return visual(name, "card", position, {"Values": [mea(measure, table)]}, title, objects=objects)


def trend(name: str, kind: str, position: tuple, measures: list, title: str) -> dict:
    """Axes start at zero so differences are not exaggerated; the 50 % reference line has no data labels."""
    labels = [{"properties": {"show": literal("true"), "labelDisplayUnits": literal("1D")}}]
    if "Even share" in measures:
        labels.append({"properties": {"showSeries": literal("false")},
                       "selector": {"metadata": "matches.Even share"}})
    return visual(name, kind, position, {"Category": [col("season")], "Y": [mea(m) for m in measures]}, title,
                  order=col("season"), ascending=True,
                  objects={"valueAxis": [{"properties": {"start": literal("0D")}}], "labels": labels})


def page(name: str, display: str, ordinal: int, visuals: list) -> dict:
    return {"name": name, "displayName": display, "displayOption": 1, "width": 1280, "height": 720,
            "ordinal": ordinal, "config": "{}", "filters": "[]", "visualContainers": visuals}


def pages() -> list:
    progression = page("progression", "Progress", 0, [
        card("c_ppm", (20, 20, 300, 120), "Latest season points per match", "Points per match, 2026/27"),
        card("c_share", (335, 20, 300, 120), "Latest season shot share", "Shot share, 2026/27"),
        card("c_conv", (650, 20, 300, 120), "Latest season conversion", "Shot conversion, 2026/27"),
        card("c_n", (965, 20, 295, 120), "Latest season matches", "Matches played, current season"),
        trend("l_ppm", "lineChart", (20, 155, 620, 545),
              ["Brighton points per match", "League points per match"], "Points per match: Brighton and the average"),
        trend("l_share", "lineChart", (655, 155, 605, 545),
              ["Brighton shot share", "Even share"], "Brighton's share of shots in its matches"),
    ])
    shots = page("tirs", "Shots", 1, [
        trend("b_shots", "clusteredColumnChart", (20, 20, 1240, 345),
              ["Brighton shots", "Brighton shots conceded"], "Brighton: shots per match, taken and conceded"),
        trend("l_conv", "lineChart", (20, 380, 1240, 320),
              ["Brighton conversion", "League conversion"], "Share of shots converted into goals"),
    ])
    clubs = page("clubs", "Club comparison", 2, [
        visual("s_season", "slicer", (20, 20, 280, 680), {"Values": [col("season")]}, "Choose a season"),
        visual("b_clubs", "clusteredBarChart", (315, 20, 460, 680),
               {"Category": [col("team")], "Y": [mea("Shot share")]}, "Shot share by club",
               order=mea("Shot share"), labels=True),
        visual("t_clubs", "tableEx", (790, 20, 470, 680),
               {"Values": [col("team"), mea("Matches played"), mea("Total points"), mea("Points per match"), mea("Conversion")]},
               "Points and efficiency by club", order=mea("Points per match")),
    ])
    t, d = "transfers", "trades"
    transfers = page("transferts", "Transfers", 3, [
        card("c_in", (20, 20, 300, 120), "Spent (€M)", "Spent 2017/18 to 2025/26 (€M)", t),
        card("c_out", (335, 20, 300, 120), "Received (€M)", "Received over the period (€M)", t),
        card("c_bal", (650, 20, 300, 120), "Balance (€M)", "Balance: received minus spent (€M)", t),
        card("c_gain", (965, 20, 295, 120), "Gain (€M)", "Bought then sold: gain (€M)", d),
        visual("b_season", "clusteredColumnChart", (20, 155, 620, 545),
               {"Category": [col("season", t)], "Y": [mea("Spent (€M)", t), mea("Received (€M)", t)]},
               "Spent and received by season (€M)", order=col("season", t), ascending=True, labels=True),
        visual("t_trades", "tableEx", (655, 155, 605, 545),
               {"Values": [col("player", d), col("sold_season", d), mea("Bought for (€M)", d), mea("Sold for (€M)", d),
                           mea("Gain (€M)", d)]},
               "Players bought then sold (€M)", order=mea("Gain (€M)", d)),
    ])
    return [progression, shots, clubs, transfers]


def build_report() -> None:
    write(REPORT / "definition.pbir",
          {"version": "1.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}})
    write(REPORT / "StaticResources" / "RegisteredResources" / THEME_FILE, {
        "name": "Brighton data",
        "dataColors": ["#0e7c7b", "#e8505b", "#7cc4c1", "#1c2430", "#eda100", "#5b6472"],
        "background": "#fbf8f5", "foreground": "#1c2430", "tableAccent": "#0e7c7b"})
    write(REPORT / "report.json", {
        "config": compact({"version": "5.59",
                           "themeCollection": {"customTheme": {"name": THEME_FILE, "version": "5.59", "type": 1}},
                           "activeSectionIndex": 0, "defaultDrillFilterOtherVisuals": True}),
        "layoutOptimization": 0,
        "resourcePackages": [{"resourcePackage": {
            "name": "RegisteredResources", "type": 1, "disabled": False,
            "items": [{"type": 202, "path": THEME_FILE, "name": THEME_FILE}]}}],
        "sections": pages(),
    })


def main() -> None:
    schemas = {"matches": json.loads((DATA / "schema.json").read_text(encoding="utf-8")),
               **json.loads((DATA / "transfer_schema.json").read_text(encoding="utf-8"))}
    for folder in (MODEL, REPORT):
        shutil.rmtree(folder, ignore_errors=True)
    build_model(schemas)
    build_report()
    write(OUT / f"{NAME}.pbip", {"version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
                                 "settings": {"enableAutoRecovery": True}})
    print(f"wrote powerbi/{NAME}.pbip ({sum(len(m) for m in MEASURES.values())} measures, {len(pages())} pages)")


if __name__ == "__main__":
    main()
