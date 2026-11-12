// Replaces fraction.js 5.2.1's float-to-fraction loop (parse, p1 in (0, 1), denominators <= N).
// That loop steps through the Stern-Brocot tree one mediant at a time, so a float one ulp from
// a short fraction takes up to N steps: 265 ms per conversion in QuickJS. This takes each run of
// same-direction steps at once, found by binary search on the loop's own float comparisons, so
// it returns the same [n, d]. Within a run the mediant is monotone, correctly rounded division
// keeps that order, and all integers stay below 2^53.
// Copyright (C) 2026 nanodirt contributors. MIT, as fraction.js (Robert Eisele).
function __nanodirt_farey(p1, N) {
  // Largest k with ok(0) ... ok(k - 1) all true, given ok(0) and ok true then false.
  const run = (ok) => {
    let lo = 0, hi = 1;
    while (ok(hi)) { lo = hi; hi *= 2; }
    while (hi - lo > 1) { const m = (lo + hi) >>> 1; if (ok(m)) lo = m; else hi = m; }
    return lo + 1;
  };
  let n = 0, d = 1;
  let A = 0, B = 1, C = 1, D = 1;
  while (B <= N && D <= N) {
    const M = (A + C) / (B + D);
    if (p1 === M) {
      if (B + D <= N) { n = A + C; d = B + D; } else if (D > B) { n = C; d = D; } else { n = A; d = B; }
      break;
    }
    // Step j of a run starts from (A + jC, B + jD), or (C + jA, D + jB).
    if (p1 > M) {
      const k = run((j) => B + j * D <= N && p1 > (A + (j + 1) * C) / (B + (j + 1) * D));
      A += k * C; B += k * D;
    } else {
      const k = run((j) => D + j * B <= N && p1 < (C + (j + 1) * A) / (D + (j + 1) * B));
      C += k * A; D += k * B;
    }
    if (B > N) { n = C; d = D; } else { n = A; d = B; }
  }
  return [n, d];
}
globalThis.__nanodirt_farey = __nanodirt_farey; // for tests/test_headless.py
