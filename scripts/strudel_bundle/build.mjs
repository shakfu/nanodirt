// Bundle Strudel's pattern packages and nanodirt's harness into one script for QuickJS.
// usage: node build.mjs <strudel checkout> <out file>
import * as esbuild from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const [root, out] = process.argv.slice(2);
const here = path.dirname(fileURLToPath(import.meta.url));
const pkg = (name) => path.join(root, 'packages', name);

// fraction.js 5.2.1's float parse, one mediant step per iteration; farey.js has the reason.
const FAREY_LOOP = /\n {6}while \(B <= N && D <= N\) \{\n[\s\S]*?\n {6}\}\n(?= {6}n = BigInt\(n\) \* BigInt\(z\);)/g;
const fastFarey = {
  name: 'fast-farey',
  setup(build) {
    build.onLoad({ filter: /fraction\.js[\\/]dist[\\/]fraction\.mjs$/ }, (args) => {
      const src = fs.readFileSync(args.path, 'utf8');
      const loops = src.match(FAREY_LOOP) ?? [];
      if (loops.length !== 1 || !loops[0].includes('A += C;') || !loops[0].includes('C += A;')) {
        throw new Error(`fraction.js: expected one Farey loop, found ${loops.length}; check farey.js`);
      }
      const farey = fs.readFileSync(path.join(here, 'farey.js'), 'utf8');
      const contents = src.replace(FAREY_LOOP, '\n      [n, d] = __nanodirt_farey(p1, N);\n') + '\n' + farey;
      return { contents, loader: 'js' };
    });
  },
};
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
  plugins: [fastFarey],
});
console.log('built', out);
