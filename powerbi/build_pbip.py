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
        ("Matchs", "COUNTROWS(matches)", "#,0"),
        ("Total points", "SUM(matches[points])", "#,0"),
        ("Points par match", "DIVIDE([Total points], [Matchs])", "0.00"),
        ("Part des tirs",
         "DIVIDE(SUM(matches[shots_for]), SUM(matches[shots_for]) + SUM(matches[shots_against]))", "0.0%"),
        ("Conversion",
         "DIVIDE(CALCULATE(SUM(matches[goals_for]), NOT ISBLANK(matches[shots_for])), SUM(matches[shots_for]))",
         "0.0%"),
        ("Tirs par match", "AVERAGE(matches[shots_for])", "0.0"),
        ("Tirs concédés par match", "AVERAGE(matches[shots_against])", "0.0"),
        ("Brighton points par match", brighton("Points par match"), "0.00"),
        ("Championnat points par match", league("Points par match"), "0.00"),
        ("Brighton part des tirs", brighton("Part des tirs"), "0.0%"),
        ("Équilibre", "0.5", "0%"),
        ("Brighton conversion", brighton("Conversion"), "0.0%"),
        ("Championnat conversion", league("Conversion"), "0.0%"),
        ("Brighton tirs", brighton("Tirs par match"), "0.0"),
        ("Brighton tirs concédés", brighton("Tirs concédés par match"), "0.0"),
        ("Dernière saison points par match", latest("Brighton points par match"), "0.00"),
        ("Dernière saison part des tirs", latest("Brighton part des tirs"), "0.0%"),
        ("Dernière saison conversion", latest("Brighton conversion"), "0.0%"),
        ("Dernière saison matchs", latest("Matchs", ', matches[team] = "Brighton"'), "#,0"),
    ],
    "transfers": [
        ("Achats (M€)", millions("transfers", "spent_eur"), "#,0.0"),
        ("Ventes (M€)", millions("transfers", "received_eur"), "#,0.0"),
        ("Solde (M€)", "[Ventes (M€)] - [Achats (M€)]", "#,0.0"),
    ],
    "trades": [
        ("Achat (M€)", millions("trades", "buy_fee_eur"), "#,0.0"),
        ("Vente (M€)", millions("trades", "sell_fee_eur"), "#,0.0"),
        ("Plus-value (M€)", millions("trades", "gain_eur"), "#,0.0"),
        ("Opérations", "COUNTROWS(trades)", "#,0"),
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
    if "Équilibre" in measures:
        labels.append({"properties": {"showSeries": literal("false")},
                       "selector": {"metadata": "matches.Équilibre"}})
    return visual(name, kind, position, {"Category": [col("season")], "Y": [mea(m) for m in measures]}, title,
                  order=col("season"), ascending=True,
                  objects={"valueAxis": [{"properties": {"start": literal("0D")}}], "labels": labels})


def page(name: str, display: str, ordinal: int, visuals: list) -> dict:
    return {"name": name, "displayName": display, "displayOption": 1, "width": 1280, "height": 720,
            "ordinal": ordinal, "config": "{}", "filters": "[]", "visualContainers": visuals}


def pages() -> list:
    progression = page("progression", "La progression", 0, [
        card("c_ppm", (20, 20, 300, 120), "Dernière saison points par match", "Points par match, 2026/27"),
        card("c_share", (335, 20, 300, 120), "Dernière saison part des tirs", "Part des tirs, 2026/27"),
        card("c_conv", (650, 20, 300, 120), "Dernière saison conversion", "Tirs convertis en buts, 2026/27"),
        card("c_n", (965, 20, 295, 120), "Dernière saison matchs", "Matchs joués, saison en cours"),
        trend("l_ppm", "lineChart", (20, 155, 620, 545),
              ["Brighton points par match", "Championnat points par match"], "Points par match : Brighton et la moyenne"),
        trend("l_share", "lineChart", (655, 155, 605, 545),
              ["Brighton part des tirs", "Équilibre"], "Part des tirs de Brighton dans ses matchs"),
    ])
    shots = page("tirs", "Créer et concéder", 1, [
        trend("b_shots", "clusteredColumnChart", (20, 20, 1240, 345),
              ["Brighton tirs", "Brighton tirs concédés"], "Brighton : tirs par match, créés et concédés"),
        trend("l_conv", "lineChart", (20, 380, 1240, 320),
              ["Brighton conversion", "Championnat conversion"], "Part des tirs convertis en buts"),
    ])
    clubs = page("clubs", "Comparer les clubs", 2, [
        visual("s_season", "slicer", (20, 20, 280, 680), {"Values": [col("season")]}, "Choisir une saison"),
        visual("b_clubs", "clusteredBarChart", (315, 20, 460, 680),
               {"Category": [col("team")], "Y": [mea("Part des tirs")]}, "Part des tirs par club",
               order=mea("Part des tirs"), labels=True),
        visual("t_clubs", "tableEx", (790, 20, 470, 680),
               {"Values": [col("team"), mea("Matchs"), mea("Total points"), mea("Points par match"), mea("Conversion")]},
               "Points et efficacité par club", order=mea("Points par match")),
    ])
    t, d = "transfers", "trades"
    transfers = page("transferts", "Transferts", 3, [
        card("c_in", (20, 20, 300, 120), "Achats (M€)", "Achats 2017/18 à 2025/26 (M€)", t),
        card("c_out", (335, 20, 300, 120), "Ventes (M€)", "Ventes sur la période (M€)", t),
        card("c_bal", (650, 20, 300, 120), "Solde (M€)", "Solde : ventes moins achats (M€)", t),
        card("c_gain", (965, 20, 295, 120), "Plus-value (M€)", "Achetés puis revendus : gain (M€)", d),
        visual("b_season", "clusteredColumnChart", (20, 155, 620, 545),
               {"Category": [col("season", t)], "Y": [mea("Achats (M€)", t), mea("Ventes (M€)", t)]},
               "Achats et ventes par saison (M€)", order=col("season", t), ascending=True, labels=True),
        visual("t_trades", "tableEx", (655, 155, 605, 545),
               {"Values": [col("player", d), col("sold_season", d), mea("Achat (M€)", d), mea("Vente (M€)", d),
                           mea("Plus-value (M€)", d)]},
               "Joueurs achetés puis revendus (M€)", order=mea("Plus-value (M€)", d)),
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
