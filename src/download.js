// Download the Premier League season files from football-data.co.uk into data/raw (not committed).
const fs = require('fs'), path = require('path');
const { SEASONS } = require('./seasons');
const raw = path.join(__dirname, '..', 'data', 'raw');
fs.mkdirSync(raw, { recursive: true });
(async () => {
  for (const s of SEASONS) {
    const res = await fetch(`https://www.football-data.co.uk/mmz4281/${s}/E0.csv`);
    if (!res.ok) throw new Error(`${s}: HTTP ${res.status}`);
    fs.writeFileSync(path.join(raw, `E0_${s}.csv`), Buffer.from(await res.arrayBuffer()));
    console.log('downloaded', s);
  }
})();
