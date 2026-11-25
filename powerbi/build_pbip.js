// Generate the Power BI project (PBIP: text files) from the pipeline output:
//   node src/pipeline.js && node powerbi/build_pbip.js     then open powerbi/Brighton.pbip
// Column types come from data/out/schema.json, written by the pipeline.
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const ROOT = path.join(__dirname, '..'), DATA = path.join(ROOT, 'data', 'out'), OUT = __dirname;
const NAME = 'Brighton', MODEL = path.join(OUT, `${NAME}.SemanticModel`), REPORT = path.join(OUT, `${NAME}.Report`);
const schema = JSON.parse(fs.readFileSync(path.join(DATA, 'schema.json'), 'utf8'));

const brighton = m => `CALCULATE([${m}], matches[team] = "Brighton")`;
const league = m => `CALCULATE([${m}], REMOVEFILTERS(matches[team]))`;
const latest = m => `VAR s = CALCULATE(MAX(matches[season]), REMOVEFILTERS(matches)) RETURN CALCULATE([${m}], matches[season] = s)`;
const MEASURES = [
  ['Matchs', 'COUNTROWS(matches)', '#,0'],
  ['Total points', 'SUM(matches[points])', '#,0'],
  ['Points par match', 'DIVIDE([Total points], [Matchs])', '0.00'],
  ['Part des tirs', 'DIVIDE(SUM(matches[shots_for]), SUM(matches[shots_for]) + SUM(matches[shots_against]))', '0.0%'],
  ['Conversion', 'DIVIDE(CALCULATE(SUM(matches[goals_for]), NOT ISBLANK(matches[shots_for])), SUM(matches[shots_for]))', '0.0%'],
  ['Tirs par match', 'AVERAGE(matches[shots_for])', '0.0'],
  ['Tirs concédés par match', 'AVERAGE(matches[shots_against])', '0.0'],
  ['Brighton points par match', brighton('Points par match'), '0.00'],
  ['Championnat points par match', league('Points par match'), '0.00'],
  ['Brighton part des tirs', brighton('Part des tirs'), '0.0%'],
  ['Équilibre', '0.5', '0%'],
  ['Brighton conversion', brighton('Conversion'), '0.0%'],
  ['Championnat conversion', league('Conversion'), '0.0%'],
  ['Brighton tirs', brighton('Tirs par match'), '0.0'],
  ['Brighton tirs concédés', brighton('Tirs concédés par match'), '0.0'],
  ['Dernière saison points par match', latest('Brighton points par match'), '0.00'],
  ['Dernière saison part des tirs', latest('Brighton part des tirs'), '0.0%'],
  ['Dernière saison conversion', latest('Brighton conversion'), '0.0%'],
  ['Dernière saison matchs', latest('Matchs') .replace('CALCULATE([Matchs], matches[season] = s)', 'CALCULATE([Matchs], matches[season] = s, matches[team] = "Brighton")'), '#,0'],
];

const tag = (...p) => {
  const h = crypto.createHash('sha1').update('brighton-data/' + p.join('/')).digest('hex');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-5${h.slice(13, 16)}-a${h.slice(17, 20)}-${h.slice(20, 32)}`;
};
const quote = n => (/^\w+$/.test(n) ? n : `'${n}'`);
const write = (file, content) => {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, typeof content === 'string' ? content : JSON.stringify(content, null, 2));
};

// ---------------------------------------------------------------- semantic model (TMDL)
const TMDL = { string: 'string', int: 'int64', decimal: 'double', date: 'dateTime' };
const M = { string: 'type text', int: 'Int64.Type', decimal: 'type number', date: 'type date' };

function buildModel() {
  const def = path.join(MODEL, 'definition');
  write(path.join(MODEL, 'definition.pbism'), { version: '4.0', settings: {} });
  write(path.join(def, 'database.tmdl'), 'database\n\tcompatibilityLevel: 1600\n');
  write(path.join(def, 'model.tmdl'), ['model Model', '\tculture: en-US', '\tdefaultPowerBIDataSourceVersion: powerBI_V3',
    '\tdiscourageImplicitMeasures', '\tsourceQueryCulture: en-US', '', 'ref table matches', ''].join('\n'));
  write(path.join(def, 'expressions.tmdl'),
    `expression DataFolder = "${DATA + path.sep}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n` +
    `\tlineageTag: ${tag('DataFolder')}\n\n\tannotation PBI_ResultType = Text\n`);
  const t = [`table matches`, `\tlineageTag: ${tag('matches')}`, ''];
  for (const [n, e, f] of MEASURES) t.push(`\tmeasure ${quote(n)} = ${e}`, `\t\tformatString: ${f}`, `\t\tlineageTag: ${tag('measure', n)}`, '');
  for (const [n, type] of schema) {
    t.push(`\tcolumn ${n}`, `\t\tdataType: ${TMDL[type]}`);
    if (type === 'date') t.push('\t\tformatString: dd/mm/yyyy');
    t.push(`\t\tlineageTag: ${tag(n)}`, '\t\tsummarizeBy: none', `\t\tsourceColumn: ${n}`, '');
  }
  const types = schema.map(([n, type]) => `{"${n}", ${M[type]}}`).join(', ');
  t.push('\tpartition matches = m', '\t\tmode: import', '\t\tsource =', '\t\t\t\tlet',
    '\t\t\t\t    Source = Csv.Document(File.Contents(DataFolder & "matches.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.None]),',
    '\t\t\t\t    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),',
    `\t\t\t\t    Typed = Table.TransformColumnTypes(Headers, {${types}}, "en-US")`,
    '\t\t\t\tin', '\t\t\t\t    Typed', '');
  write(path.join(def, 'tables', 'matches.tmdl'), t.join('\n'));
}

// ---------------------------------------------------------------- report (PBIR-Legacy report.json)
const col = p => ({ p, m: false }), mea = p => ({ p, m: true });
const literal = value => ({ expr: { Literal: { Value: value } } });
const expr = f => ({ [f.m ? 'Measure' : 'Column']: { Expression: { SourceRef: { Source: 't' } }, Property: f.p } });

function visual(name, type, [x, y, w, h], roles, title, { order, ascending = false, labels = false, objects } = {}) {
  const fields = [...new Map(Object.values(roles).flat().map(f => [f.p, f])).values()];
  const query = { Version: 2, From: [{ Name: 't', Entity: 'matches', Type: 0 }],
    Select: fields.map(f => ({ ...expr(f), Name: `matches.${f.p}` })) };
  if (order) query.OrderBy = [{ Direction: ascending ? 1 : 2, Expression: expr(order) }];
  const projections = Object.fromEntries(Object.entries(roles).map(([role, refs]) =>
    [role, refs.map((f, i) => ({ queryRef: `matches.${f.p}`, ...(i === 0 && role === 'Category' ? { active: true } : {}) }))]));
  const config = { name, layouts: [{ id: 0, position: { x, y, z: 0, width: w, height: h } }],
    singleVisual: { visualType: type, projections, prototypeQuery: query, drillFilterOtherVisuals: true,
      vcObjects: { title: [{ properties: { show: literal('true'), text: literal(`'${title.replace(/'/g, "''")}'`),
        fontSize: literal('13D'), fontColor: { solid: { color: literal("'#1c2430'") } }, bold: literal('true') } }] } } };
  const extra = { ...(labels ? { labels: [{ properties: { show: literal('true'), labelDisplayUnits: literal('1D') } }] } : {}), ...objects };
  if (Object.keys(extra).length) config.singleVisual.objects = extra;
  return { x, y, z: 0, width: w, height: h, config: JSON.stringify(config), filters: '[]' };
}
const card = (name, pos, measure, title) => visual(name, 'card', pos, { Values: [mea(measure)] }, title,
  { objects: { labels: [{ properties: { labelDisplayUnits: literal('1D') } }] } });
// Axes start at zero so differences are not exaggerated; the 50 % reference line carries no data labels.
const trend = (name, type, pos, measures, title) => visual(name, type, pos,
  { Category: [col('season')], Y: measures.map(mea) }, title, { order: col('season'), ascending: true,
    objects: {
      valueAxis: [{ properties: { start: literal('0D') } }],
      labels: [{ properties: { show: literal('true'), labelDisplayUnits: literal('1D') } },
        ...(measures.includes('Équilibre') ? [{ properties: { showSeries: literal('false') }, selector: { metadata: 'matches.Équilibre' } }] : [])],
    } });
const page = (name, displayName, ordinal, visualContainers) =>
  ({ name, displayName, displayOption: 1, width: 1280, height: 720, ordinal, config: '{}', filters: '[]', visualContainers });

function buildReport() {
  write(path.join(REPORT, 'definition.pbir'), { version: '1.0', datasetReference: { byPath: { path: `../${NAME}.SemanticModel` } } });
  const sections = [
    page('progression', 'La progression', 0, [
      card('c_ppm', [20, 20, 300, 120], 'Dernière saison points par match', 'Points par match, 2026/27'),
      card('c_share', [335, 20, 300, 120], 'Dernière saison part des tirs', 'Part des tirs, 2026/27'),
      card('c_conv', [650, 20, 300, 120], 'Dernière saison conversion', 'Tirs convertis en buts, 2026/27'),
      card('c_n', [965, 20, 295, 120], 'Dernière saison matchs', 'Matchs joués, saison en cours'),
      trend('l_ppm', 'lineChart', [20, 155, 620, 545], ['Brighton points par match', 'Championnat points par match'],
        'Points par match : Brighton et la moyenne'),
      trend('l_share', 'lineChart', [655, 155, 605, 545], ['Brighton part des tirs', 'Équilibre'],
        'Part des tirs de Brighton dans ses matchs'),
    ]),
    page('tirs', 'Créer et concéder', 1, [
      trend('b_shots', 'clusteredColumnChart', [20, 20, 1240, 345], ['Brighton tirs', 'Brighton tirs concédés'],
        'Brighton : tirs par match, créés et concédés'),
      trend('l_conv', 'lineChart', [20, 380, 1240, 320], ['Brighton conversion', 'Championnat conversion'],
        'Part des tirs convertis en buts'),
    ]),
    page('clubs', 'Comparer les clubs', 2, [
      visual('s_season', 'slicer', [20, 20, 280, 680], { Values: [col('season')] }, 'Choisir une saison'),
      visual('b_clubs', 'clusteredBarChart', [315, 20, 460, 680], { Category: [col('team')], Y: [mea('Part des tirs')] },
        'Part des tirs par club', { order: mea('Part des tirs'), labels: true }),
      visual('t_clubs', 'tableEx', [790, 20, 470, 680], { Values: [col('team'), mea('Matchs'), mea('Total points'), mea('Points par match'), mea('Conversion')] },
        'Points et efficacité par club', { order: mea('Points par match') }),
    ]),
  ];
  const theme = 'Brighton.json';
  write(path.join(REPORT, 'StaticResources', 'RegisteredResources', theme), { name: 'Brighton data',
    dataColors: ['#0e7c7b', '#e8505b', '#7cc4c1', '#1c2430', '#eda100', '#5b6472'],
    background: '#fbf8f5', foreground: '#1c2430', tableAccent: '#0e7c7b' });
  write(path.join(REPORT, 'report.json'), {
    config: JSON.stringify({ version: '5.59', themeCollection: { customTheme: { name: theme, version: '5.59', type: 1 } },
      activeSectionIndex: 0, defaultDrillFilterOtherVisuals: true }),
    layoutOptimization: 0,
    resourcePackages: [{ resourcePackage: { name: 'RegisteredResources', type: 1, disabled: false,
      items: [{ type: 202, path: theme, name: theme }] } }],
    sections,
  });
}

for (const d of [MODEL, REPORT]) fs.rmSync(d, { recursive: true, force: true });
buildModel();
buildReport();
write(path.join(OUT, `${NAME}.pbip`), { version: '1.0', artifacts: [{ report: { path: `${NAME}.Report` } }], settings: { enableAutoRecovery: true } });
console.log(`wrote powerbi/${NAME}.pbip (${MEASURES.length} measures, 3 pages)`);
