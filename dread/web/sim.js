// dread gui's force simulation, without the DOM: the layout of a function's relations and of the
// whole program.
//
// Nodes push each other apart: every pair exactly up to EXACT_MAX nodes, beyond it by Barnes-Hut's
// approximation (a quadtree of the nodes; a cell far enough away, its size against its distance below
// theta, pushes as one node of its whole charge at its centre of charge). Links pull towards their
// length, the end with fewer links moving more. A view adds forces of its own (layers, groups, an
// anchor). Each tick adds the forces, scaled by the energy alpha, to the velocities, and friction keeps
// a share of them: a node overshoots its place a little and settles. Alpha moves towards its target
// (0; more while a node is dragged); the simulation rests when alpha has decayed or no node has moved
// for QUIET ticks, and a resting simulation is not ticked. No randomness: the same graph from the
// same positions takes the same path.

export const EXACT_MAX = 600;     // up to this many nodes every pair is computed; beyond, Barnes-Hut
const QUIET = 12;                 // ticks without a node faster than restSpeed: at rest
const MAX_DEPTH = 32;             // the quadtree's depth at most (nodes closer than that share a cell)

export const DEFAULTS = {
  repel: 9000,          // the repulsion of two nodes of charge 1 at distance 1
  power: 2,             // how it falls with distance d: 2 as 1/d², 1 as 1/d
  reach: Infinity,      // no repulsion between nodes farther apart
  theta: 0.9,           // Barnes-Hut: a cell pushes as one when its size is below theta times its distance
  near: 1,              // distances below this count as this (no infinite push)
  friction: 0.86,       // the share of its velocity a node keeps from one tick to the next
  gravity: 0,           // every node pulled towards the origin, per unit of distance
  maxSpeed: 40,         // at most this far in one tick
  decay: 0.03,          // alpha moves this share of the way to its target in each tick
  alphaMin: 0.002,      // at rest below it (with target 0)
  restSpeed: 0.02,      // ... or when no node moved faster than this for QUIET ticks
};

export class Sim {
  constructor(options = {}) {
    this.o = Object.assign({}, DEFAULTS, options);
    this.alpha = 0;
    this.target = 0;
    this.quiet = 0;
    this.ticks = 0;
    this.rested = true;
    this.forces = [];       // more forces: f(sim) adds to sim.fx, sim.fy (before alpha scales them)
    this.tree = new Quadtree();
    this.size(0);
  }

  size(n) {
    this.n = n;
    for (const k of ["x", "y", "vx", "vy", "fx", "fy", "px", "py"]) this[k] = new Float64Array(n);
    this.q = new Float64Array(n).fill(1);           // each node's charge
    this.fixed = new Uint8Array(n);                 // held at (px, py)
    this.links([], [], [], []);
    return this;
  }

  // Links: source and target node numbers, the length each pulls towards and its strength.
  links(source, target, length, strength) {
    const m = source.length, degree = new Float64Array(this.n);
    this.ls = Int32Array.from(source);
    this.lt = Int32Array.from(target);
    this.len = Float64Array.from(length);
    this.k = Float64Array.from(strength);
    for (let l = 0; l < m; l++) { degree[this.ls[l]] += 1; degree[this.lt[l]] += 1; }
    this.bias = new Float64Array(m);
    for (let l = 0; l < m; l++) {
      const s = degree[this.ls[l]], t = degree[this.lt[l]];
      this.bias[l] = s / (s + t);
    }
    this.degree = degree;
    return this;
  }

  heat(alpha) {
    this.alpha = Math.max(this.alpha, alpha);
    this.quiet = 0;
    this.rested = false;
    return this;
  }

  fix(i, x, y) { this.fixed[i] = 1; this.px[i] = x; this.py[i] = y; }
  free(i) { this.fixed[i] = 0; }

  tick() {
    const o = this.o, n = this.n, { x, y, vx, vy, fx, fy } = this;
    fx.fill(0);
    fy.fill(0);
    if (n > EXACT_MAX) this.repelTree(); else this.repelExact();
    const { ls, lt, len, k, bias } = this;
    for (let l = 0; l < ls.length; l++) {
      const s = ls[l], t = lt[l];
      let dx = x[t] - x[s], dy = y[t] - y[s], d = Math.sqrt(dx * dx + dy * dy);
      if (d < 1e-6) { dx = 1e-3 * (1 + (l % 5)); dy = 1e-3 * (1 + (l % 3)); d = Math.sqrt(dx * dx + dy * dy); }
      const f = (d - len[l]) / d * k[l], b = bias[l];
      fx[t] -= dx * f * b; fy[t] -= dy * f * b;
      fx[s] += dx * f * (1 - b); fy[s] += dy * f * (1 - b);
    }
    if (o.gravity) for (let i = 0; i < n; i++) { fx[i] -= x[i] * o.gravity; fy[i] -= y[i] * o.gravity; }
    for (const force of this.forces) force(this);
    const alpha = this.alpha, friction = o.friction, cap = o.maxSpeed, cap2 = cap * cap;
    let fastest = 0;
    for (let i = 0; i < n; i++) {
      if (this.fixed[i]) {
        x[i] = this.px[i]; y[i] = this.py[i]; vx[i] = 0; vy[i] = 0;
        continue;
      }
      let u = (vx[i] + alpha * fx[i]) * friction, w = (vy[i] + alpha * fy[i]) * friction;
      const s2 = u * u + w * w;
      if (s2 > cap2) { const r = cap / Math.sqrt(s2); u *= r; w *= r; }
      vx[i] = u; vy[i] = w;
      x[i] += u; y[i] += w;
      if (s2 > fastest) fastest = s2;
    }
    this.alpha += (this.target - this.alpha) * o.decay;
    this.ticks += 1;
    this.quiet = fastest < o.restSpeed * o.restSpeed ? this.quiet + 1 : 0;
    if (this.target === 0 && (this.alpha < o.alphaMin || this.quiet >= QUIET)) this.rested = true;
    return !this.rested;
  }

  // Ticks until at rest or max ticks (the layout without the motion); returns how many it took.
  run(max = 1000) {
    let t = 0;
    while (!this.rested && t < max) { this.tick(); t += 1; }
    return t;
  }

  repelExact() {
    const o = this.o, n = this.n, { x, y, fx, fy, q } = this;
    const reach2 = o.reach * o.reach, near2 = o.near * o.near, square = o.power === 2;
    for (let i = 0; i < n; i++) {
      const qi = q[i] * o.repel;
      for (let j = i + 1; j < n; j++) {
        let dx = x[i] - x[j], dy = y[i] - y[j], d2 = dx * dx + dy * dy;
        if (d2 > reach2) continue;
        if (d2 < 1e-6) { dx = 0.1 * (1 + (i % 7)); dy = 0.1 * (1 + (j % 5)); d2 = dx * dx + dy * dy; }
        if (d2 < near2) d2 = near2;
        const f = qi * q[j] / (square ? d2 * Math.sqrt(d2) : d2);
        fx[i] += dx * f; fy[i] += dy * f;
        fx[j] -= dx * f; fy[j] -= dy * f;
      }
    }
  }

  repelTree() {
    const o = this.o, n = this.n, { x, y, fx, fy, q } = this, t = this.tree;
    t.build(x, y, q, n);
    const reach2 = o.reach * o.reach, near2 = o.near * o.near, theta2 = o.theta * o.theta, square = o.power === 2;
    const { left, top, side, qs, qx, qy, body, kid, leaf, next, stack } = t;
    for (let i = 0; i < n; i++) {
      const xi = x[i], yi = y[i];
      let ax = 0, ay = 0, sp = 0;
      stack[sp++] = 0;
      while (sp > 0) {
        const c = stack[--sp], Q = qs[c];
        if (Q === 0) continue;
        if (leaf[c]) {
          for (let j = body[c]; j >= 0; j = next[j]) {
            if (j === i) continue;
            let dx = xi - x[j], dy = yi - y[j], d2 = dx * dx + dy * dy;
            if (d2 > reach2) continue;
            if (d2 < 1e-6) { dx = 0.1 * (1 + (i % 7)); dy = 0.1 * (1 + (j % 5)); d2 = dx * dx + dy * dy; }
            if (d2 < near2) d2 = near2;
            const f = q[j] / (square ? d2 * Math.sqrt(d2) : d2);
            ax += dx * f; ay += dy * f;
          }
          continue;
        }
        const s = side[c], x0 = left[c], y0 = top[c];
        const dx = xi - qx[c] / Q, dy = yi - qy[c] / Q;
        let d2 = dx * dx + dy * dy;
        const inside = xi >= x0 && xi < x0 + s && yi >= y0 && yi < y0 + s;
        if (!inside && s * s < theta2 * d2) {          // far: the cell as one
          if (d2 > reach2) continue;
          if (d2 < near2) d2 = near2;
          const f = Q / (square ? d2 * Math.sqrt(d2) : d2);
          ax += dx * f; ay += dy * f;
          continue;
        }
        if (reach2 !== Infinity) {                     // a cell wholly out of reach
          const cx = xi < x0 ? x0 : xi > x0 + s ? x0 + s : xi, cy = yi < y0 ? y0 : yi > y0 + s ? y0 + s : yi;
          if ((xi - cx) * (xi - cx) + (yi - cy) * (yi - cy) > reach2) continue;
        }
        for (let k = 4 * c; k < 4 * c + 4; k++) if (kid[k] >= 0) stack[sp++] = kid[k];
      }
      const qi = q[i] * o.repel;
      fx[i] += ax * qi;
      fy[i] += ay * qi;
    }
  }
}

// A quadtree over points with charges, in typed arrays that are kept from one build to the next. Each
// cell holds the sum of its charges and of the charges times the positions (their centre); a leaf
// holds a list of bodies (one, or several at the same place or at MAX_DEPTH).
export class Quadtree {
  constructor() {
    this.cap = 0;
    this.count = 0;
    this.next = new Int32Array(0);
    this.stack = new Int32Array(4 * MAX_DEPTH + 8);
    this.grow(64);
  }

  grow(cap) {
    const old = this.cap;
    const copy = (Type, a, size) => { const b = new Type(size); if (a) b.set(a.subarray(0, Math.min(a.length, size))); return b; };
    for (const k of ["left", "top", "side", "qs", "qx", "qy"]) this[k] = copy(Float64Array, this[k], cap);
    this.body = copy(Int32Array, this.body, cap);
    this.leaf = copy(Uint8Array, this.leaf, cap);
    const kid = new Int32Array(4 * cap).fill(-1);
    if (this.kid) kid.set(this.kid.subarray(0, 4 * old));
    this.kid = kid;
    this.cap = cap;
  }

  cell(x0, y0, s) {
    if (this.count >= this.cap) this.grow(this.cap * 2);
    const c = this.count++;
    this.left[c] = x0; this.top[c] = y0; this.side[c] = s;
    this.qs[c] = 0; this.qx[c] = 0; this.qy[c] = 0;
    this.body[c] = -1; this.leaf[c] = 1;
    this.kid[4 * c] = this.kid[4 * c + 1] = this.kid[4 * c + 2] = this.kid[4 * c + 3] = -1;
    return c;
  }

  build(x, y, q, n) {
    if (this.next.length < n) this.next = new Int32Array(Math.max(n, 2 * this.next.length));
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let i = 0; i < n; i++) {
      if (x[i] < x0) x0 = x[i]; if (x[i] > x1) x1 = x[i];
      if (y[i] < y0) y0 = y[i]; if (y[i] > y1) y1 = y[i];
    }
    if (!(x1 >= x0)) { x0 = y0 = 0; x1 = y1 = 1; }
    const s = Math.max(x1 - x0, y1 - y0, 1) * (1 + 1e-9) + 1e-9;
    this.count = 0;
    this.cell(x0, y0, s);
    for (let i = 0; i < n; i++) this.insert(i, x, y, q);
  }

  insert(i, x, y, q) {
    const xi = x[i], yi = y[i], qi = q[i], next = this.next;
    let c = 0, depth = 0;
    for (;;) {
      this.qs[c] += qi; this.qx[c] += qi * xi; this.qy[c] += qi * yi;
      if (this.leaf[c]) {
        const b = this.body[c];
        if (b < 0) { this.body[c] = i; next[i] = -1; return; }
        if (depth >= MAX_DEPTH || (x[b] === xi && y[b] === yi)) { next[i] = b; this.body[c] = i; return; }
        this.leaf[c] = 0;                   // split: its bodies (one place) go down into a child
        this.body[c] = -1;
        const k = this.child(c, x[b], y[b]);
        this.body[k] = b;
        for (let j = b; j >= 0; j = next[j]) {
          this.qs[k] += q[j]; this.qx[k] += q[j] * x[j]; this.qy[k] += q[j] * y[j];
        }
      }
      c = this.child(c, xi, yi);
      depth += 1;
    }
  }

  child(c, px, py) {
    const half = this.side[c] / 2, right = px >= this.left[c] + half, down = py >= this.top[c] + half;
    const slot = 4 * c + (right ? 1 : 0) + (down ? 2 : 0);
    let k = this.kid[slot];
    if (k < 0) {
      k = this.cell(this.left[c] + (right ? half : 0), this.top[c] + (down ? half : 0), half);
      this.kid[slot] = k;                   // (cell() may have grown kid: index it after)
    }
    return k;
  }
}

// The nodes' forces from every pair, without the quadtree: what Barnes-Hut approximates (for checks).
export function exactForces(sim) {
  const copy = new Sim(sim.o).size(sim.n);
  copy.x.set(sim.x); copy.y.set(sim.y); copy.q.set(sim.q);
  copy.repelExact();
  return [copy.fx, copy.fy];
}
