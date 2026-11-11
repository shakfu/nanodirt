// Bundle Strudel's pattern packages and nanodirt's harness into one script for QuickJS.
// usage: node build.mjs <strudel checkout> <out file>
import * as esbuild from 'esbuild';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const [root, out] = process.argv.slice(2);
const here = path.dirname(fileURLToPath(import.meta.url));
const pkg = (name) => path.join(root, 'packages', name);
await esbuild.build({
  entryPoints: [path.join(here, 'harness.mjs')],
  outfile: out,
  bundle: true,
  format: 'iife',
  platform: 'neutral',
  mainFields: ['module', 'main'],
  nodePaths: [path.join(here, 'node_modules')],
  alias: Object.fromEntries(
    ['core', 'mini', 'tonal', 'transpiler', 'edo', 'xen'].map((n) => [`@strudel/${n}`, pkg(n)]),
  ),
  define: { 'process.env.NODE_ENV': '"production"' },
  legalComments: 'inline',
  logLevel: 'warning',
});
console.log('built', out);
