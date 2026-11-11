// Evaluate Strudel's tunes with the bundle in Node, as the reference for the QuickJS run.
// usage: node node_events.mjs <strudel checkout> <bundle> <out json> <cycles>
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { pathToFileURL } from 'node:url';

const [root, bundle, out, cycles] = process.argv.slice(2);
const source = fs.readFileSync(bundle, 'utf8');
const quiet = { log() {}, info() {}, debug() {}, warn() {}, error() {} }; // errors are recorded below
// A fresh runtime per tune, as each Python Strudel(): the REPL keeps cps across evaluations.
const runtime = () => {
  const ctx = vm.createContext({ console: quiet, performance, setTimeout, clearTimeout, setInterval, clearInterval });
  vm.runInContext(source, ctx);
  return ctx.__nanodirt;
};
const report = (...a) => process.stdout.write(a.join(' ') + '\n');

const tunes = {};
for (const file of ['website/src/repl/tunes.mjs', 'test/testtunes.mjs']) {
  const mod = await import(pathToFileURL(path.join(root, file)).href);
  for (const [name, code] of Object.entries(mod)) {
    if (typeof code === 'string' && !Object.values(tunes).some((t) => t.code === code)) {
      tunes[name in tunes ? `${name}_test` : name] = { code };
    }
  }
}
for (const tune of Object.values(tunes)) {
  const nd = runtime();
  nd.evaluate(tune.code);
  let result;
  do {
    await new Promise((r) => setImmediate(r));
    result = JSON.parse(nd.result());
  } while (result.state === 'pending');
  tune.cps = result.cps;
  if (result.state === 'error') {
    tune.error = result.message;
  } else {
    const { events, skipped, error } = JSON.parse(nd.query(0, Number(cycles), result.cps));
    tune.events = events;
    if (skipped) {
      Object.assign(tune, { skipped, skip_error: error });
    }
  }
}
fs.writeFileSync(out, JSON.stringify(tunes, null, 1) + '\n');
const failed = Object.values(tunes).filter((t) => t.error).length;
report(`${Object.keys(tunes).length} tunes, ${failed} failed, in ${out}`);
