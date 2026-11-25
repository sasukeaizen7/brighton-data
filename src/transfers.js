// Brighton transfers from transfermarkt-datasets (CC0 repository, snapshot to 6 July 2026).
//   download: https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/transfers.csv.gz -> data/raw_transfers/
const fs = require('fs'), path = require('path'), zlib = require('zlib');
const ROOT = path.join(__dirname, '..'), OUT = path.join(ROOT, 'data', 'out');
const BRIGHTON = '1237', SNAPSHOT = '2026-07-06';
// Complete seasons only: 2026/27 had barely started when the snapshot was taken.
const SEASONS = ['17/18', '18/19', '19/20', '20/21', '21/22', '22/23', '23/24', '24/25', '25/26'];

function parseCsv(s) {
  const out = []; let row = [], f = '', quoted = false;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (quoted) { if (c !== '"') f += c; else if (s[i + 1] === '"') { f += '"'; i++; } else quoted = false; }
    else if (c === '"') quoted = true;
    else if (c === ',') { row.push(f); f = ''; }
    else if (c === '\n') { row.push(f.replace(/\r$/, '')); out.push(row); row = []; f = ''; }
    else f += c;
  }
  if (f || row.length) { row.push(f); out.push(row); }
  const [head, ...rows] = out;
  return rows.map(v => Object.fromEntries(head.map((h, i) => [h, v[i] ?? ''])));
}

const all = parseCsv(zlib.gunzipSync(fs.readFileSync(path.join(ROOT, 'data', 'raw_transfers', 'transfers.csv.gz'))).toString('utf8'));
const mine = all.filter(r => (r.from_club_id === BRIGHTON) !== (r.to_club_id === BRIGHTON) && SEASONS.includes(r.transfer_season));

const checks = [];
const check = (rule, severity, failed) => checks.push({ rule, severity, failed });
check('invalid_date', 'blocking', mine.filter(r => !/^\d{4}-\d{2}-\d{2}$/.test(r.transfer_date)).length);
check('negative_or_non_numeric_fee', 'blocking', mine.filter(r => r.transfer_fee !== '' && !(Number(r.transfer_fee) >= 0)).length);
check('duplicate_transfer', 'blocking', mine.length - new Set(mine.map(r => [r.player_id, r.transfer_date, r.from_club_id, r.to_club_id].join('|'))).size);
check('dated_after_snapshot', 'excluded', mine.filter(r => r.transfer_date > SNAPSHOT).length);
check('fee_unknown', 'info', mine.filter(r => r.transfer_fee === '').length);
check('fee_zero_free_or_loan', 'info', mine.filter(r => r.transfer_fee !== '' && Number(r.transfer_fee) === 0).length);

const rows = mine.filter(r => r.transfer_date <= SNAPSHOT).map(r => {
  const arrival = r.to_club_id === BRIGHTON;
  const fee = r.transfer_fee === '' ? '' : Number(r.transfer_fee);
  return {
    season: `20${r.transfer_season}`, transfer_date: r.transfer_date, player_id: r.player_id, player: r.player_name,
    direction: arrival ? 'Arrivée' : 'Départ', other_club: arrival ? r.from_club_name : r.to_club_name,
    fee_eur: fee, spent_eur: arrival && fee !== '' ? fee : 0, received_eur: !arrival && fee !== '' ? fee : 0,
  };
}).sort((a, b) => a.transfer_date.localeCompare(b.transfer_date));

// A trade = a paid departure matched with the latest earlier paid arrival of the same player.
const trades = rows.filter(s => s.direction === 'Départ' && s.fee_eur > 0).flatMap(s => {
  const buy = rows.filter(b => b.player_id === s.player_id && b.direction === 'Arrivée' && b.fee_eur > 0 && b.transfer_date < s.transfer_date).at(-1);
  return buy ? [{ player: s.player, bought_season: buy.season, buy_fee_eur: buy.fee_eur, sold_season: s.season,
    sold_to: s.other_club, sell_fee_eur: s.fee_eur, gain_eur: s.fee_eur - buy.fee_eur }] : [];
}).sort((a, b) => b.gain_eur - a.gain_eur);

const TABLES = {
  transfers: [['season', 'string'], ['transfer_date', 'date'], ['player', 'string'], ['direction', 'string'], ['other_club', 'string'],
    ['fee_eur', 'int'], ['spent_eur', 'int'], ['received_eur', 'int']],
  trades: [['player', 'string'], ['bought_season', 'string'], ['buy_fee_eur', 'int'], ['sold_season', 'string'], ['sold_to', 'string'],
    ['sell_fee_eur', 'int'], ['gain_eur', 'int']],
};
const cell = v => (typeof v === 'string' && /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
const write = (name, cols, data) => fs.writeFileSync(path.join(OUT, `${name}.csv`),
  [cols.join(','), ...data.map(r => cols.map(c => cell(r[c])).join(','))].join('\n') + '\n');
fs.mkdirSync(OUT, { recursive: true });
write('transfer_quality', ['rule', 'severity', 'failed'], checks);
const blocking = checks.filter(c => c.severity === 'blocking' && c.failed);
if (blocking.length) { console.error('Blocked, nothing published:', blocking); process.exit(1); }
write('transfers', TABLES.transfers.map(c => c[0]), rows);
write('trades', TABLES.trades.map(c => c[0]), trades);
fs.writeFileSync(path.join(OUT, 'transfer_schema.json'), JSON.stringify(TABLES));

const m = v => (v / 1e6).toFixed(1);
console.table(checks);
let spent = 0, received = 0;
for (const s of [...new Set(rows.map(r => r.season))].sort()) {
  const x = rows.filter(r => r.season === s), a = x.reduce((t, r) => t + r.spent_eur, 0), b = x.reduce((t, r) => t + r.received_eur, 0);
  spent += a; received += b; console.log(s, 'spent', m(a), 'received', m(b), 'balance', m(b - a));
}
console.log('TOTAL spent', m(spent), 'received', m(received), 'balance', m(received - spent), 'M EUR');
const g = trades.reduce((t, r) => t + r.gain_eur, 0);
console.log('matched trades', trades.length, 'with gain', trades.filter(t => t.gain_eur > 0).length, 'total gain', m(g));
console.table(trades.slice(0, 10).map(t => ({ player: t.player, buy: m(t.buy_fee_eur), sell: m(t.sell_fee_eur), gain: m(t.gain_eur), sold: t.sold_season })));
