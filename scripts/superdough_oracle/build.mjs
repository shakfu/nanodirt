// Bundle superdough from a Strudel checkout for headless offline rendering.
// usage: node build.mjs <strudel checkout> <out dir>
import * as esbuild from 'esbuild';
import path from 'node:path';

const [root, out] = process.argv.slice(2);
const sd = path.join(root, 'packages/superdough');
const common = {
  bundle: true,
  format: 'esm',
  nodePaths: [path.resolve('node_modules')],
  define: { 'process.env.NODE_ENV': '"production"' },
  logLevel: 'warning',
};

// worklets.mjs?audioworklet -> a separate module file; the import yields its URL
// The worklet scope has its own Math.random (supersaw phases): seed it per context.
const seeded = `{ let a = 1; Math.random = () => { a |= 0; a = (a + 0x6d2b79f5) | 0;
  let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }`;
await esbuild.build({
  ...common,
  entryPoints: [path.join(sd, 'worklets.mjs')],
  outfile: path.join(out, 'worklets.js'),
  banner: { js: seeded },
});
const worklet = {
  name: 'audioworklet',
  setup(b) {
    b.onResolve({ filter: /\?audioworklet$/ }, () => ({ path: 'worklets', namespace: 'aw' }));
    b.onLoad({ filter: /.*/, namespace: 'aw' }, () => ({
      contents: 'export default new URL("./worklets.js", import.meta.url).href;',
    }));
  },
};
await esbuild.build({
  ...common,
  entryPoints: [path.join(sd, 'index.mjs')],
  outfile: path.join(out, 'superdough.js'),
  plugins: [worklet],
  external: ['@kabelsalat/web'], // live-coding DSP; not used for rendering
});
console.log('built', out);
