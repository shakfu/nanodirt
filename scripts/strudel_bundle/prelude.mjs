// prelude.mjs - globals QuickJS lacks, defined before Strudel's modules load. AGPL-3.0-or-later.
const host = (name) => globalThis[`__nanodirt_host_${name}`];
const log =
  (level) =>
  (...args) =>
    host('log')?.(level, args.map((a) => (a instanceof Error ? a.message : String(a))).join(' '));
globalThis.console ??= { log: log('info'), info: log('info'), debug: log('debug'), warn: log('warning'), error: log('error') };
globalThis.setTimeout ??= () => 0; // the scheduler never runs: Python queries instead
globalThis.clearTimeout ??= () => {};
globalThis.setInterval ??= () => 0;
globalThis.clearInterval ??= () => {};

