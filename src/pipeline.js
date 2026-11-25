// Raw match files -> one row per team per match, published only if every blocking check passes.
const fs = require('fs'), path = require('path');
const { SEASONS, CURRENT } = require('./seasons');
const ROOT = path.join(__dirname, '..'), RAW = path.join(ROOT, 'data', 'raw'), OUT = path.join(ROOT, 'data', 'out');

// Single source of truth for the published columns; the Power BI model is generated from it.
const SCHEMA = [
  ['season', 'string'], ['match_date', 'date'], ['team', 'string'], ['opponent', 'string'], ['venue', 'string'],
  ['goals_for', 'int'], ['goals_against', 'int'], ['points', 'int'],
  ['shots_for', 'int'], ['shots_against', 'int'], ['sot_for', 'int'], ['sot_against', 'int'],
  ['xg_for', 'decimal'], ['xg_against', 'decimal'],
];
const label = s => `20${s.slice(0, 2)}/${s.slice(2)}` + (s === CURRENT ? ' (en cours)' : '');
const read = s => {
  const lines = fs.readFileSync(path.join(RAW, `E0_${s}.csv`), 'utf8').replace(/^﻿/, '').split(/\r?\n/)
    .filter(l => l.trim() && !/^,+$/.test(l));
  const head = lines[0].split(',');
  return lines.slice(1).map(l => { const v = l.split(','); return Object.fromEntries(head.map((h, i) => [h, v[i] ?? ''])); });
};

const checks = [];
const check = (season, rule, severity, failed) => checks.push({ season, rule, severity, failed });
const rows = [];
for (const s of SEASONS) {
  const m = read(s);
  const int = v => (/^\d+$/.test(v) ? Number(v) : null);
  const need = ['FTHG', 'FTAG', 'HS', 'AS', 'HST', 'AST'];
  check(s, 'missing_or_non_numeric_stat', 'blocking', m.filter(r => need.some(k => int(r[k]) === null)).length);
  check(s, 'invalid_date', 'blocking', m.filter(r => !/^\d{2}\/\d{2}\/\d{4}$/.test(r.Date)).length);
  check(s, 'duplicate_fixture', 'blocking', m.length - new Set(m.map(r => r.HomeTeam + '|' + r.AwayTeam)).size);
  check(s, 'result_contradicts_score', 'blocking',
    m.filter(r => r.FTR !== (+r.FTHG > +r.FTAG ? 'H' : +r.FTHG < +r.FTAG ? 'A' : 'D')).length);
  // Impossible shot counts: the score is kept, the shot statistics of that match are blanked (never corrected by hand).
  const badShots = r => +r.HST > +r.HS || +r.AST > +r.AS;
  check(s, 'shots_on_target_above_shots', 'quarantine', m.filter(badShots).length);
  // Own goals can push goals above shots on target: reported, not blocking.
  check(s, 'goals_above_shots_on_target', 'info', m.filter(r => !badShots(r) && (+r.FTHG > +r.HST || +r.FTAG > +r.AST)).length);
  if (s !== CURRENT) check(s, 'season_incomplete', 'blocking', m.length === 380 ? 0 : 1);
  for (const r of m) {
    const [d, mo, y] = r.Date.split('/');
    const side = (home) => {
      const gf = +(home ? r.FTHG : r.FTAG), ga = +(home ? r.FTAG : r.FTHG);
      const shot = (h, a) => (badShots(r) ? '' : +(home ? r[h] : r[a]));
      return {
        season: label(s), match_date: `${y}-${mo}-${d}`,
        team: home ? r.HomeTeam : r.AwayTeam, opponent: home ? r.AwayTeam : r.HomeTeam, venue: home ? 'Domicile' : 'Extérieur',
        goals_for: gf, goals_against: ga, points: gf > ga ? 3 : gf === ga ? 1 : 0,
        shots_for: shot('HS', 'AS'), shots_against: shot('AS', 'HS'),
        sot_for: shot('HST', 'AST'), sot_against: shot('AST', 'HST'),
        xg_for: (home ? r.HxG : r.AxG) || '', xg_against: (home ? r.AxG : r.HxG) || '',
      };
    };
    rows.push(side(true), side(false));
  }
}
fs.mkdirSync(OUT, { recursive: true });
const csv = (cols, data) => [cols.join(','), ...data.map(r => cols.map(c => r[c]).join(','))].join('\n') + '\n';
fs.writeFileSync(path.join(OUT, 'quality.csv'), csv(['season', 'rule', 'severity', 'failed'], checks));
const blocking = checks.filter(c => c.severity === 'blocking' && c.failed);
if (blocking.length) { console.error('Blocked, nothing published:', blocking); process.exit(1); }
fs.writeFileSync(path.join(OUT, 'matches.csv'), csv(SCHEMA.map(c => c[0]), rows));
fs.writeFileSync(path.join(OUT, 'schema.json'), JSON.stringify(SCHEMA));

// Summary used to verify the dashboard and the post.
const sum = (a, k) => a.reduce((t, r) => t + Number(r[k]), 0);
console.log('season | Brighton pts/match (league) | shot share | conversion (league) | shots for/against');
for (const s of SEASONS) {
  const all = rows.filter(r => r.season === label(s)), b = all.filter(r => r.team === 'Brighton');
  console.log([label(s), `${(sum(b, 'points') / b.length).toFixed(2)} (${(sum(all, 'points') / all.length).toFixed(2)})`,
    (100 * sum(b, 'shots_for') / (sum(b, 'shots_for') + sum(b, 'shots_against'))).toFixed(1) + '%',
    `${(100 * sum(b.filter(r => r.shots_for !== ''), 'goals_for') / sum(b, 'shots_for')).toFixed(1)}% (${(100 * sum(all.filter(r => r.shots_for !== ''), 'goals_for') / sum(all, 'shots_for')).toFixed(1)}%)`,
    `${(sum(b, 'shots_for') / b.length).toFixed(1)}/${(sum(b, 'shots_against') / b.length).toFixed(1)}`,
    `${b.length} matches, ${sum(b, 'points')} pts`].join(' | '));
}
console.log('rows', rows.length, '| checks', checks.length, '| info flags', sum(checks.filter(c => c.severity === 'info'), 'failed'));
