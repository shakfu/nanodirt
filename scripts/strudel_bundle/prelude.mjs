// prelude.mjs - globals QuickJS lacks, defined before Strudel's modules load. AGPL-3.0-or-later.
const host = (name) => globalThis[`__nanodirt_host_${name}`];
// Browser console styling: each %c in the format string takes one CSS argument.
const unstyle = (args) => {
  if (typeof args[0] !== 'string' || !args[0].includes('%c')) return args;
  const n = args[0].split('%c').length - 1;
  return [args[0].replaceAll('%c', ''), ...args.slice(1 + n)];
};
const log =
  (level) =>
  (...args) =>
    host('log')?.(level, unstyle(args).map((a) => (a instanceof Error ? a.message : String(a))).join(' '));
globalThis.console ??= { log: log('info'), info: log('info'), debug: log('debug'), warn: log('warning'), error: log('error') };
globalThis.setTimeout ??= () => 0; // the scheduler never runs: Python queries instead
globalThis.clearTimeout ??= () => {};
globalThis.setInterval ??= () => 0;
globalThis.clearInterval ??= () => {};

