/*
harness.mjs - nanodirt's headless Strudel: evaluate code, query events with OSC fields.
Copyright (C) 2026 nanodirt contributors.
parseControls follows packages/osc/osc.mjs and piano packages/repl/prebake.mjs of Strudel,
Copyright (C) 2022 Strudel contributors.
This program is free software: you can redistribute it and/or modify it under the terms of the
GNU Affero General Public License as published by the Free Software Foundation, either version 3
of the License, or (at your option) any later version.
*/

import './prelude.mjs'; // first: Strudel's modules need console at load time
import * as core from '@strudel/core';
import * as mini from '@strudel/mini';
import * as tonal from '@strudel/tonal';
import * as edo from '@strudel/edo';
import * as xen from '@strudel/xen';
import { transpiler } from '@strudel/transpiler';

const host = (name) => globalThis[`__nanodirt_host_${name}`];
const { repl, evalScope, Pattern, silence, parseNumeral, isNote, noteToMidi, valueToMidi } = core;

// Drawing methods (@strudel/draw, codemirror widgets): no display, so they return the pattern.
for (const name of [
  'pianoroll', 'punchcard', 'spiral', 'scope', 'tscope', 'fscope', 'spectrum', 'wordfall',
  'pitchwheel', 'markcss', 'draw', 'onPaint', 'animate',
]) {
  for (const n of [name, `_${name}`]) {
    if (!(n in Pattern.prototype)) {
      Pattern.prototype[n] = function () {
        return this;
      };
    }
  }
}

const maxPan = noteToMidi('C8');
const panwidth = (pan, width) => pan * width + (1 - width) / 2;
Pattern.prototype.piano = function () {
  return this.fmap((v) => ({ ...v, clip: v.clip ?? 1 }))
    .s('piano')
    .release(0.1)
    .fmap((value) => {
      const midi = valueToMidi(value);
      const pan = panwidth(Math.min(Math.round(midi) / maxPan, 1), 0.5);
      return { ...value, pan: (value.pan || 1) * pan };
    });
};

// Sample maps go to nanodirt; registration of browser sounds does nothing.
const hostScope = {
  samples: async (map, base = '') => host('samples')?.(JSON.stringify([map, base])),
  aliasBank: async () => {},
  soundAlias: () => {},
  registerSound: () => {},
  registerSynthSounds: async () => {},
  registerZZFXSounds: async () => {},
  registerSoundfonts: async () => {},
  initAudio: async () => {},
  initAudioOnFirstClick: async () => {},
};

/** Strudel's OSC fields for a hap (osc.mjs parseControlsFromHap). */
export function parseControls(hap, cps) {
  hap.ensureObjectValue();
  const cycle = hap.wholeOrPart().begin.valueOf();
  const delta = hap.duration.valueOf() / cps;
  const controls = Object.assign({}, { cps, cycle, delta }, hap.value);
  controls.n && (controls.n = parseNumeral(controls.n));
  if (typeof controls.note !== 'undefined') {
    if (isNote(controls.note)) {
      controls.midinote = noteToMidi(controls.note, controls.octave || 3);
    } else {
      controls.midinote = parseNumeral(controls.note);
    }
  }
  controls.roomsize && (controls.size = parseNumeral(controls.roomsize));
  controls.unit === 'c' && controls.speed != null && (controls.speed = controls.speed / cps);
  const channels = controls.channels;
  channels != undefined && (controls.channels = JSON.stringify(channels));
  return controls;
}

let evalError = null;
const r = repl({
  transpiler,
  getTime: () => 0,
  setInterval: () => 0,
  clearInterval: () => {},
  onSchedule: () => {},
  onEvalError: (err) => {
    evalError = err;
  },
});
let scoped = null;
let status = { state: 'idle' };

const api = {
  /** Start evaluating *code*; the caller runs pending jobs, then reads result(). */
  evaluate(code) {
    status = { state: 'pending' };
    evalError = null;
    scoped ??= evalScope(core, mini, tonal, edo, xen, hostScope);
    scoped
      .then(() => r.evaluate(code, false))
      .then(() => {
        status = evalError
          ? { state: 'error', message: String(evalError.message ?? evalError), stack: String(evalError.stack ?? '') }
          : { state: 'ok' };
      })
      .catch((err) => {
        status = { state: 'error', message: String(err?.message ?? err), stack: String(err?.stack ?? '') };
      });
  },
  result() {
    return JSON.stringify({ ...status, cps: r.scheduler.cps });
  },
  /** Onset events in cycles [begin, end) at *cps*, and how many haps had no OSC fields, as JSON. */
  query(begin, end, cps) {
    const pattern = r.state.pattern;
    const events = [];
    let skipped = 0;
    let error = null;
    if (pattern) {
      for (const hap of pattern.queryArc(begin, end, { _cps: cps, cyclist: 'cyclist' })) {
        if (!hap.hasOnset()) {
          continue;
        }
        try {
          const controls = parseControls(hap, cps);
          events.push({ begin: hap.whole.begin.valueOf(), end: hap.whole.end.valueOf(), controls });
        } catch (err) {
          skipped++; // as Strudel's scheduler, which logs and skips
          error ??= String(err?.message ?? err);
        }
      }
    }
    return JSON.stringify({ events, skipped, error });
  },
  hush() {
    r.stop();
    r.state.pattern = silence;
  },
};

globalThis.__nanodirt = api;
for (const [name, fn] of Object.entries(api)) {
  globalThis[`__nanodirt_${name}`] = fn; // the binding looks up globals, not properties
}
