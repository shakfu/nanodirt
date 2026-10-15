// Sends frames shaped like Strudel's osc.mjs (packages/osc/osc.mjs:79-87) to the spike.
const ws = new WebSocket('ws://localhost:8080');
ws.addEventListener('open', async () => {
  const cps = 0.5;
  const base = Date.now() + 100; // Strudel latency 0.1 s, timestamp in ms
  const haps = [['bd', 0], ['sn', 0.25], ['hh', 0.5], ['cp', 0.75]];
  for (const [s, at] of haps) {
    const args = ['cps', cps, 'cycle', at, 'delta', 0.5, 's', s, 'n', 0, 'clip', 1, 'bank', null].filter(x => x !== null);
    ws.send(JSON.stringify({ address: '/dirt/play', args: args.slice(0, -1), timestamp: base + at * 2000 }));
  }
  setTimeout(() => ws.close(), 300);
});
ws.addEventListener('error', (e) => { console.log('ws error', e.message); process.exit(1); });
ws.addEventListener('close', () => console.log('sent 4 frames'));
