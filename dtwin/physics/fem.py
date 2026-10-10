"""
Thermal finite-element model of one phase leg: the temperature field inside
the LV and HV windings, as an independent cross-check of the C57.91 hot-spot.

Problem (steady state, conduction only, 2D axisymmetric in r, z):

    -div(k grad T) = q            in each winding block
    weak form:  sum_e  int 2 pi r (k_r dT/dr dv/dr + k_z dT/dz dv/dz) dA
              + int 2 pi r h T v ds  =  int 2 pi r q v dA + int 2 pi r h T_oil(z) v ds

    Robin on every face that touches oil:  -k dT/dn = h (T - T_oil(z))
    T_oil(z) linear from bottom oil (z = 0) to top oil (top of the tallest
    winding): the IEC 60076-7 thermal-diagram assumption. Top oil comes from
    the C57.91 model; bottom oil = top oil - f x top-oil rise (f configured,
    an assumption).

Each winding is a homogenised block of conductor, paper and oil with
anisotropic conductivity: layers in parallel axially, in series radially,

    k_z = sum(f_i k_i)          k_r = 1 / sum(f_i / k_i)

Heat source: q = per-phase I^2 R / block volume, with I and R from the
equivalent circuit (dtwin/physics/circuit.py). Eddy and stray losses are
ignored unless config/fem.yaml sets eddy_fraction. R is not corrected for
temperature.

h is calibrated ONCE: at rated balanced current the FEM average-winding to
average-oil gradient equals the rated gradient g_r (config/fem.yaml, with its
source). Nothing else is fitted.

Linear P1 triangles on a structured mesh whose node lines follow the block
edges (scikit-fem). The two windings are separate blocks: oil fills the duct
between them, so they couple only through the prescribed oil profile. The
system matrix depends on h only, so it is factorised once and every later
solve is a back-substitution.

Limits, stated wherever results are shown: conduction only with a prescribed
oil profile; no oil-flow (CFD) simulation; homogenised windings; illustrative
geometry and materials until design data are supplied; a production model
would couple this to CFD.
"""

import math
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml
from scipy.optimize import brentq
from scipy.sparse.linalg import splu
from skfem import Basis, BilinearForm, ElementTriP1, FacetBasis, LinearForm, MeshTri, asm

from . import circuit as C
from .nameplate import Nameplate, preset
from .thermal import top_oil_rise_ultimate, hotspot_rise_ultimate

CFG_PATH = Path(__file__).resolve().parents[2] / "config" / "fem.yaml"
TWO_PI = 2.0 * math.pi
FACES = ("inner", "outer", "bottom", "top")
LIMITS = [
    "Conduction only, with a prescribed linear oil-temperature profile (bottom to top oil).",
    "No oil-flow (CFD) simulation: duct oil does not heat up along the winding.",
    "Homogenised windings: conductor, paper and oil smeared into one anisotropic block per winding.",
    "Illustrative geometry and materials until design data are supplied.",
    "DC I^2 R loss only, at reference resistance; no eddy, stray or core loss.",
    "A production model would couple this conduction model to CFD of the oil.",
]


@lru_cache(maxsize=1)
def config():
    return yaml.safe_load(CFG_PATH.read_text())


def homogenised_k(fill: dict, k: dict):
    """(k_r, k_z) of a layered block. fill: material -> volume fraction."""
    tot = sum(fill.values())
    if abs(tot - 1.0) > 1e-9:
        raise ValueError(f"volume fractions sum to {tot}, not 1")
    kz = sum(f * k[m] for m, f in fill.items())
    kr = 1.0 / sum(f / k[m] for m, f in fill.items())
    return kr, kz


# --------------------------------------------------------------------------- #
# One rectangular block in (r, z)
# --------------------------------------------------------------------------- #
@BilinearForm
def _cond(u, v, w):
    return TWO_PI * w.x[0] * (w.kr * u.grad[0] * v.grad[0] + w.kz * u.grad[1] * v.grad[1])


@BilinearForm
def _robin(u, v, w):
    return TWO_PI * w.x[0] * u * v


@LinearForm
def _one(v, w):
    return TWO_PI * w.x[0] * v


@LinearForm
def _zed(v, w):
    return TWO_PI * w.x[0] * w.x[1] * v


def rect_mesh(r0, r1, z0, z1, nr, nz):
    """Structured triangles, node (i, j) at index j*(nr+1)+i, so a field
    reshapes to a (nz+1, nr+1) grid. Each cell is split into two triangles
    along alternating diagonals (no directional bias)."""
    r = np.linspace(r0, r1, nr + 1)
    z = np.linspace(z0, z1, nz + 1)
    R, Z = np.meshgrid(r, z)
    p = np.vstack([R.ravel(), Z.ravel()])
    tri = []
    for j in range(nz):
        for i in range(nr):
            a, b = j * (nr + 1) + i, j * (nr + 1) + i + 1
            c, d = a + nr + 1, b + nr + 1
            if (i + j) % 2 == 0:
                tri += [(a, b, d), (a, d, c)]
            else:
                tri += [(a, b, c), (b, d, c)]
    m = MeshTri(np.ascontiguousarray(p), np.ascontiguousarray(np.array(tri).T))
    eps = 1e-12 + 1e-9 * max(r1 - r0, z1 - z0)
    return m.with_boundaries({
        "inner": lambda x: np.abs(x[0] - r0) < eps, "outer": lambda x: np.abs(x[0] - r1) < eps,
        "bottom": lambda x: np.abs(x[1] - z0) < eps, "top": lambda x: np.abs(x[1] - z1) < eps})


class Block:
    """A homogenised rectangle with precomputed FEM operators."""

    def __init__(self, name, r0, r1, z0, z1, kr, kz, nr, nz):
        self.name, self.r0, self.r1, self.z0, self.z1 = name, r0, r1, z0, z1
        self.kr, self.kz, self.nr, self.nz = kr, kz, nr, nz
        self.mesh = rect_mesh(r0, r1, z0, z1, nr, nz)
        e = ElementTriP1()
        basis = Basis(self.mesh, e, intorder=4)
        self.K = asm(_cond, basis, kr=kr, kz=kz).tocsc()
        self.W = asm(_one, basis)                       # int 2 pi r phi_i dA
        self.Wz = float(asm(_zed, basis).sum())         # int 2 pi r z dA
        self.volume = float(self.W.sum())               # = pi (r1^2 - r0^2) (z1 - z0)
        self.M, self.F1, self.Fz, self.area = {}, {}, {}, {}
        for f in FACES:
            fb = FacetBasis(self.mesh, e, facets=self.mesh.boundaries[f], intorder=4)
            self.M[f] = asm(_robin, fb).tocsc()
            self.F1[f] = asm(_one, fb)
            self.Fz[f] = asm(_zed, fb)
            self.area[f] = float(self.F1[f].sum())
        self._lu = {}

    @property
    def elements(self):
        return self.mesh.t.shape[1]

    def _factor(self, h):
        key = tuple(round(h[f], 12) for f in FACES)
        if key not in self._lu:
            if len(self._lu) > 32:
                self._lu.clear()
            A = self.K.copy()
            for f in FACES:
                if h[f]:
                    A = A + h[f] * self.M[f]
            self._lu[key] = splu(A.tocsc())
        return self._lu[key]

    def solve(self, q, h, oil_a, oil_b):
        """Nodal temperatures for heat density q (W/m3), face coefficients h
        (dict or scalar, W/m2K) and oil T_oil(z) = oil_a + oil_b z."""
        h = {f: float(h) for f in FACES} if np.isscalar(h) else {f: float(h.get(f, 0.0)) for f in FACES}
        b = q * self.W
        for f in FACES:
            if h[f]:
                b = b + h[f] * (oil_a * self.F1[f] + oil_b * self.Fz[f])
        T = self._factor(h).solve(b)
        return T, h

    def heat_out(self, T, h, oil_a, oil_b):
        """W leaving through the faces: sum_f h int 2 pi r (T - T_oil) ds."""
        return sum(h[f] * (self.F1[f] @ T - oil_a * self.area[f] - oil_b * float(self.Fz[f].sum()))
                   for f in FACES if h[f])

    def mean(self, T):
        return float(self.W @ T) / self.volume

    def mean_oil(self, oil_a, oil_b):
        return oil_a + oil_b * self.Wz / self.volume

    def grid(self, T):
        return T.reshape(self.nz + 1, self.nr + 1)


# --------------------------------------------------------------------------- #
# The phase leg: LV and HV blocks, losses from the circuit, calibrated h
# --------------------------------------------------------------------------- #
@dataclass
class LegResult:
    T: dict                 # block name -> nodal temperatures
    q: dict                 # block name -> W/m3
    p: dict                 # block name -> W
    top_oil_c: float
    bottom_oil_c: float
    h: float


class PhaseLeg:
    def __init__(self, np_: Nameplate, conductor=None, nr=None, nz=None, cfg=None):
        cfg = cfg or config()
        self.cfg, self.np_ = cfg, np_
        self.conductor = conductor or cfg["conductor"]
        mesh = cfg["mesh"]
        self.nr, self.nz = int(nr or mesh["nr"]), int(nz or mesh["nz"])
        g = cfg["geometry"]
        s = (np_.rating_kva / g["rating_kva"]) ** 0.25
        self.scale = s
        k = {"conductor": cfg["materials"][self.conductor]["k"], "paper": cfg["materials"]["paper"]["k"],
             "oil": cfg["materials"]["oil"]["k"]}
        hmax = max(g["lv"]["height_m"], g["hv"]["height_m"]) * s
        self.height = hmax
        self.blocks = {}
        self.k = {}
        for name in ("lv", "hv"):
            d = g[name]
            kr, kz = homogenised_k(cfg["fill"][name], k)
            self.k[name] = {"kr": kr, "kz": kz}
            hh = d["height_m"] * s
            z0 = 0.5 * (hmax - hh)
            self.blocks[name] = Block(name, d["r_in_m"] * s, d["r_out_m"] * s, z0, z0 + hh, kr, kz, self.nr, self.nz)
        self.circuit = C.from_nameplate(np_)
        self.f_bottom = float(cfg["bottom_oil_fraction"])
        self.g_rated = float(cfg["gradient_rated_k"])
        self._h = None

    @property
    def elements(self):
        return sum(b.elements for b in self.blocks.values())

    @property
    def nodes(self):
        return sum(b.mesh.p.shape[1] for b in self.blocks.values())

    # ---- losses ------------------------------------------------------------
    def losses(self, i_lv_a, r_mult=1.0, pf=0.9, v_pu=1.0):
        """Per-limb winding losses (W) for an LV phase current, from the
        circuit: LV |I2|^2 R2, HV |I1|^2 R1 (star equivalent = per limb)."""
        p = C.scaled(self.circuit, r=r_mult) if r_mult != 1.0 else self.circuit
        op = C.solve_from_primary(p, v1_ph=p.v1_ph * v_pu, load_pu=max(i_lv_a, 0.0) / p.i2_rated, pf=pf)
        extra = 1.0 + float(self.cfg.get("eddy_fraction", 0.0))
        return {"lv": abs(op.I2) ** 2 * p.R2 * extra, "hv": abs(op.I1) ** 2 * p.R1 * extra}

    def oil_profile(self, top_c, ambient_c):
        bottom = top_c - self.f_bottom * (top_c - ambient_c)
        return bottom, (top_c - bottom) / self.height      # T_oil(z) = bottom + slope z

    # ---- solve -------------------------------------------------------------
    def solve(self, p_w: dict, top_c, ambient_c, h=None):
        h = self.h if h is None else h
        bottom, slope = self.oil_profile(top_c, ambient_c)
        T, q = {}, {}
        for name, b in self.blocks.items():
            q[name] = p_w[name] / b.volume
            T[name], _ = b.solve(q[name], h, bottom, slope)
        return LegResult(T, q, dict(p_w), top_c, bottom, h)

    def gradient(self, res: LegResult):
        """Volume-weighted average winding minus average oil over the same
        volume, both windings together: the FEM's g."""
        bottom, slope = res.bottom_oil_c, (res.top_oil_c - res.bottom_oil_c) / self.height
        vol = sum(b.volume for b in self.blocks.values())
        tw = sum(b.mean(res.T[n]) * b.volume for n, b in self.blocks.items()) / vol
        to = sum(b.mean_oil(bottom, slope) * b.volume for b in self.blocks.values()) / vol
        return tw - to, tw, to

    def hotspot(self, res: LegResult):
        best = None
        for n, b in self.blocks.items():
            k = int(np.argmax(res.T[n]))
            if best is None or res.T[n][k] > best["c"]:
                best = {"c": float(res.T[n][k]), "winding": n, "r_m": float(b.mesh.p[0, k]),
                        "z_m": float(b.mesh.p[1, k]), "z_frac": float(b.mesh.p[1, k] / self.height)}
        return best

    def energy(self, res: LegResult):
        slope = (res.top_oil_c - res.bottom_oil_c) / self.height
        gen = sum(res.q[n] * b.volume for n, b in self.blocks.items())
        out = sum(b.heat_out(res.T[n], {f: res.h for f in FACES}, res.bottom_oil_c, slope)
                  for n, b in self.blocks.items())
        return gen, out

    # ---- calibration -------------------------------------------------------
    def rated_point(self):
        amb = 30.0
        top = amb + top_oil_rise_ultimate(1.0, self.np_)
        return self.losses(self.np_.rated_current_a, pf=1.0), top, amb

    @property
    def h(self):
        if self._h is None:
            p, top, amb = self.rated_point()
            f = lambda h: self.gradient(self.solve(p, top, amb, h))[0] - self.g_rated
            self._h = brentq(f, 1.0, 1.0e4, xtol=1e-6)
        return self._h

    def calibration(self):
        p, top, amb = self.rated_point()
        res = self.solve(p, top, amb)
        g, tw, to = self.gradient(res)
        hs = self.hotspot(res)
        return {"h_w_m2k": round(self.h, 3), "g_rated_k": self.g_rated, "g_fem_k": round(g, 4),
                "rule": "h chosen once so that, at rated balanced current, FEM mean winding - mean oil = g_r",
                "g_source": self.cfg["gradient_source"],
                "rated_losses_w": {k: round(v, 1) for k, v in p.items()},
                "rated_hotspot_c": round(hs["c"], 2), "rated_top_oil_c": round(top, 2),
                "H_fem": round((hs["c"] - top) / g, 4)}

    # ---- a full three-phase snapshot --------------------------------------
    def snapshot(self, currents: dict, top_oil_c, ambient_c, r_mult=1.0, pf=0.9, v_pu=1.0, grid=True):
        """Solve R, Y, B with each phase's own current. currents: phase -> A."""
        t0 = time.perf_counter()
        phases = {}
        for ph, i in currents.items():
            if i is None or not np.isfinite(i):
                phases[ph] = None
                continue
            p = self.losses(float(i), r_mult=r_mult, pf=pf, v_pu=v_pu)
            res = self.solve(p, top_oil_c, ambient_c)
            g, tw, to = self.gradient(res)
            hs = self.hotspot(res)
            gen, out = self.energy(res)
            d = {"current_a": round(float(i), 2), "load_pu": round(float(i) / self.np_.rated_current_a, 4),
                 "hotspot": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in hs.items()},
                 "mean_winding_c": round(tw, 3), "mean_oil_c": round(to, 3), "gradient_k": round(g, 4),
                 "H": round((hs["c"] - top_oil_c) / g, 4) if g > 1e-6 else None,
                 "heat_w": round(gen, 3), "heat_out_w": round(out, 3),
                 "blocks": {}}
            for n, b in self.blocks.items():
                d["blocks"][n] = {"p_w": round(res.p[n], 2), "q_w_m3": round(res.q[n], 1),
                                  "mean_c": round(b.mean(res.T[n]), 3), "max_c": round(float(res.T[n].max()), 3)}
                if grid:
                    G = b.grid(res.T[n])
                    d["blocks"][n].update({
                        "r": [round(float(x), 5) for x in np.linspace(b.r0, b.r1, b.nr + 1)],
                        "z": [round(float(x), 5) for x in np.linspace(b.z0, b.z1, b.nz + 1)],
                        "T": [[round(float(x), 2) for x in row] for row in G],
                        "axial_max_c": [round(float(x), 2) for x in G.max(axis=1)]})
            phases[ph] = d
        live = {k: v for k, v in phases.items() if v}
        worst = max(live, key=lambda k: live[k]["hotspot"]["c"]) if live else None
        return {"phases": phases, "worst_phase": worst,
                "oil": {"top_c": round(top_oil_c, 2), "bottom_c": round(top_oil_c - self.f_bottom * (top_oil_c - ambient_c), 2),
                        "ambient_c": round(ambient_c, 2), "height_m": round(self.height, 4),
                        "assumption": f"linear in height; bottom oil = top oil - {self.f_bottom:g} x top-oil rise "
                                      f"(average oil rise {1 - self.f_bottom / 2:.1f} x top-oil rise), assumed"},
                "mesh": {"elements_per_phase": self.elements, "nodes_per_phase": self.nodes,
                         "nr": self.nr, "nz": self.nz, "type": "linear triangles, structured, node lines on block edges"},
                "materials": {"conductor": self.conductor,
                              "k": {n: {kk: round(v, 4) for kk, v in d.items()} for n, d in self.k.items()},
                              "fill": self.cfg["fill"],
                              "sources": {m: self.cfg["materials"][m]["source"]
                                          for m in (self.conductor, "paper", "oil")}},
                "calibration": {"h_w_m2k": round(self.h, 3), "g_rated_k": self.g_rated,
                                "source": self.cfg["gradient_source"]},
                "geometry": {"scale": round(self.scale, 4), "source": self.cfg["geometry"]["source"],
                             **{n: {"r_in_m": round(b.r0, 4), "r_out_m": round(b.r1, 4),
                                    "z0_m": round(b.z0, 4), "z1_m": round(b.z1, 4)} for n, b in self.blocks.items()}},
                "limits": LIMITS, "solve_ms": round(1000 * (time.perf_counter() - t0), 1)}


_LEGS = {}


def leg_for(np_: Nameplate, conductor=None) -> PhaseLeg:
    """Cached leg per rating, nameplate thermal design and conductor."""
    key = (np_.rating_kva, np_.hv_kv, np_.lv_v, np_.top_oil_rise_rated_k, np_.loss_ratio_R, conductor)
    if key not in _LEGS:
        _LEGS[key] = PhaseLeg(np_, conductor=conductor)
    return _LEGS[key]


def c57_steady_hotspot(K, ambient_c, np_):
    """C57.91 steady hot-spot, for the cross-check."""
    return ambient_c + top_oil_rise_ultimate(K, np_) + hotspot_rise_ultimate(K, np_)


def cross_check(np_: Nameplate = None, loads=(0.5, 0.75, 1.0, 1.15, 1.3), ambient_c=30.0, leg=None):
    """FEM and C57.91 hot-spot at steady state for several balanced loads,
    with the same C57.91 top oil feeding the FEM boundary."""
    np_ = np_ or preset(100)
    leg = leg or leg_for(np_)
    rows = []
    for K in loads:
        top = ambient_c + top_oil_rise_ultimate(K, np_)
        res = leg.solve(leg.losses(K * np_.rated_current_a, pf=1.0), top, ambient_c)
        hs = leg.hotspot(res)
        g, _, _ = leg.gradient(res)
        c57 = c57_steady_hotspot(K, ambient_c, np_)
        rows.append({"load_pu": K, "top_oil_c": round(top, 2), "c57_hotspot_c": round(c57, 2),
                     "fem_hotspot_c": round(hs["c"], 2), "diff_k": round(hs["c"] - c57, 2),
                     "fem_gradient_k": round(g, 3), "H_fem": round((hs["c"] - top) / g, 4) if g > 0 else None,
                     "c57_hs_over_top_k": round(hotspot_rise_ultimate(K, np_), 2),
                     "fem_hs_over_top_k": round(hs["c"] - top, 2), "hotspot_z_frac": round(hs["z_frac"], 3),
                     "hotspot_winding": hs["winding"]})
    return rows
