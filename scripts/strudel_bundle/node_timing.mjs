// Node counterpart of scripts/strudel_eval_timing.py: same bundle, grid, windows, repeats.
// usage: node node_timing.mjs src/nanodirt/js/strudel.js tests/fixtures/strudel_tunes.json <out json> <at_cycle> [repeats]
import fs from 'node:fs';
import vm from 'node:vm';

const [bundle, tunesPath, out, atCycle, repeats = '3'] = process.argv.slice(2);
const source = fs.readFileSync(bundle, 'utf8');
const tunes = JSON.parse(fs.readFileSync(tunesPath, 'utf8'));
const quiet = { log() {}, info() {}, debug() {}, warn() {}, error() {} };
const GRID = 1024, CYCLES = 4, WINDOW = 0.05;
const at = Number(atCycle);

const runtime = () => {
  const ctx = vm.createContext({ console: quiet, performance, setTimeout, clearTimeout, setInterval, clearInterval });
  vm.runInContext(source, ctx);
  return ctx.__nanodirt;
};
const settle = async (nd) => {
  let r;
  do {
    await new Promise((res) => setImmediate(res));
    r = JSON.parse(nd.result());
  } while (r.state === 'pending');
  return r;
};
const median = (xs) => xs.slice().sort((a, b) => a - b)[Math.floor(xs.length / 2)];

async function timeTune(code) {
  let t = performance.now();
  const nd = runtime();
  const load = performance.now() - t;
  t = performance.now();
  nd.evaluate(code);
  let r = await settle(nd); // includes up to one setImmediate turn per pending step
  const cold = performance.now() - t;
  if (r.state === 'error') return null;
  t = performance.now();
  nd.evaluate(code);
  r = await settle(nd);
  const warm = performance.now() - t;
  const cps = r.cps;
  const step = Math.max(1, Math.round(WINDOW * cps * GRID));
  let query = 0;
  for (let k = at * GRID; k < (at + CYCLES) * GRID; k += step) {
    t = performance.now();
    nd.query(k / GRID, (k + step) / GRID, cps);
    query = Math.max(query, performance.now() - t);
  }
  return { load, cold, warm, query, nd, cps };
}

const results = {};
for (const name of Object.keys(tunes).sort()) {
  const runs = [];
  for (let i = 0; i < Number(repeats); i++) {
    const r = await timeTune(tunes[name].code);
    if (!r) break;
    runs.push(r);
  }
  if (runs.length) {
    results[name] = Object.fromEntries(['load', 'cold', 'warm', 'query'].map((k) => [k, median(runs.map((r) => r[k]))]));
  }
}

// Off-grid bounds, as in the QuickJS probe: barryHarris at cps 0.5.
const probe = (await timeTune(tunes.barryHarris.code)).nd;
const off = {};
for (const b of [0.5, 0.5000000000000001, 0.07500000000000001, 0.30000000000000004]) {
  const t = performance.now();
  for (let i = 0; i < 3; i++) probe.query(b, b + 0.025, 0.5);
  off[String(b)] = (performance.now() - t) / 3;
}
fs.writeFileSync(out, JSON.stringify({ results, off }, null, 1));
console.log(`${Object.keys(results).length} tunes; off-grid:`, JSON.stringify(off));
