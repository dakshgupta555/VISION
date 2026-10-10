import { useEffect, useMemo, useRef, useState } from "react";
import { CylinderGeometry, Float32BufferAttribute } from "three";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { CameraControls, Html, Outlines } from "@react-three/drei";
import { hotspotColor, reducedMotion, SELECT, STATE, VIOLET } from "./states.js";

// A three-phase ONAN distribution transformer built from primitives: no fans,
// no on-load tap changer, no motor drive. Every component is a group named by
// its id in config/components.yaml. Units: metres. Phases R, Y, B along x.

import { FOCUS, HV_X, LV_X, PHASE_X } from "./focus.js";

const METAL = "#7b848e";      // terminal studs: neutral, never a status colour

function Part({ comp, selected, hovered, xray, children, geometry, position, rotation, color, opacity = 1, vertexColors = false }) {
  const mat = useRef();
  const motion = !reducedMotion();
  useFrame(({ clock }) => {
    if (!mat.current) return;
    const base = hovered ? 0.35 : comp?.pulse ? 0.35 : 0.05;
    mat.current.emissiveIntensity = comp?.pulse && motion
      ? base + 0.4 * (0.5 + 0.5 * Math.sin(2 * Math.PI * 1.0 * clock.elapsedTime))   // 1 Hz at most
      : base;
  });
  const st = comp ? STATE[comp.state] : STATE.grey;
  const c = vertexColors ? "#ffffff" : color || st.color;
  // an alarmed part always carries a red outline; the pulse is on top of it
  const outline = selected ? SELECT : comp?.state === "sensor" ? VIOLET : comp?.pulse ? STATE.red.color : null;
  return (
    <mesh position={position} rotation={rotation} castShadow>
      {geometry}
      <meshStandardMaterial ref={mat} color={c} vertexColors={vertexColors}
        emissive={comp?.pulse ? STATE.red.color : vertexColors ? "#000000" : c} emissiveIntensity={0.05} roughness={0.6}
        metalness={0.15} transparent={opacity < 1} opacity={opacity} depthWrite={opacity >= 1} side={2} />
      {outline && <Outlines thickness={selected ? 0.014 : comp?.pulse ? 0.02 : 0.01} color={outline} screenspace={false} />}
      {children}
    </mesh>
  );
}

// An open cylinder painted by height from an FEM axial profile: vertex y maps
// linearly onto the winding block's own z span (profile[0] at the bottom).
function gradedCylinder(radius, height, profile, scale) {
  const n = profile.length - 1;
  const g = new CylinderGeometry(radius, radius, height, 32, n, true);
  const pos = g.attributes.position;
  const col = new Float32Array(pos.count * 3);
  for (let k = 0; k < pos.count; k++) {
    const f = Math.min(Math.max(pos.getY(k) / height + 0.5, 0), 1) * n;
    const j = Math.min(Math.floor(f), n - 1), u = f - j;
    const v = profile[j] * (1 - u) + profile[j + 1] * u;
    const m = hotspotColor(v, scale).match(/\d+/g).map((x) => Number(x) / 255);
    // sRGB -> linear, since three treats vertex colours as linear
    col.set(m.map((x) => (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4)), 3 * k);
  }
  g.setAttribute("color", new Float32BufferAttribute(col, 3));
  return g;
}

function Winding({ p, radius, height, x, profile, scale }) {
  const geo = useMemo(() => (profile ? gradedCylinder(radius, height, profile, scale) : null), [radius, height, profile, scale]);
  useEffect(() => () => geo?.dispose(), [geo]);
  if (!geo) {
    return <Part {...p} color={hotspotColor(p.comp?.hotspot_c, scale)} position={[x, 0.55, 0]}
      geometry={<cylinderGeometry args={[radius, radius, height, 28, 1, true]} />} />;
  }
  return <Part {...p} vertexColors position={[x, 0.55, 0]} geometry={<primitive object={geo} attach="geometry" />} />;
}

function Comp({ id, comps, sel, setSel, hover, setHover, children }) {
  const comp = comps[id];
  return (
    <group name={id}
      onPointerOver={(e) => { e.stopPropagation(); setHover(id); }}
      onPointerOut={(e) => { e.stopPropagation(); setHover((h) => (h === id ? null : h)); }}
      onClick={(e) => { e.stopPropagation(); setSel(id); }}>
      {children({ comp, selected: sel === id, hovered: hover === id })}
      {hover === id && comp && (
        <Html position={FOCUS[id] || [0, 0, 0]} center distanceFactor={2.4} style={{ pointerEvents: "none" }}>
          <div className="twin-tip"><b>{comp.name}</b><br />{comp.reason}</div>
        </Html>
      )}
    </group>
  );
}

function Transformer({ comps, scale, sel, setSel, hover, setHover, xray, fem }) {
  const prof = (ph, w) => fem?.phases?.[ph.toUpperCase()]?.blocks?.[w]?.axial_max_c || null;
  const P = { comps, sel, setSel, hover, setHover };
  const shell = xray ? 0.12 : 1;
  const fins = useMemo(() => Array.from({ length: 9 }, (_, k) => -0.44 + k * 0.11), []);
  return (
    <group>
      {/* tank and oil */}
      <Comp id="tank" {...P}>{(p) => (
        <Part {...p} xray={xray} opacity={shell} position={[0, 0.55, 0]} geometry={<boxGeometry args={[1.3, 1.1, 0.75]} />} />
      )}</Comp>
      <Comp id="oil" {...P}>{(p) => (
        <Part {...p} opacity={xray ? 0.22 : 0.0} position={[0, 0.5, 0]} geometry={<boxGeometry args={[1.26, 0.98, 0.71]} />} />
      )}</Comp>

      {/* active part: three-limb core and yokes */}
      <Comp id="core" {...P}>{(p) => (
        <group>
          {Object.values(PHASE_X).map((x) => (
            <Part key={x} {...p} position={[x, 0.55, 0]} geometry={<boxGeometry args={[0.12, 0.72, 0.12]} />} />
          ))}
          <Part {...p} position={[0, 0.97, 0]} geometry={<boxGeometry args={[0.98, 0.12, 0.12]} />} />
          <Part {...p} position={[0, 0.13, 0]} geometry={<boxGeometry args={[0.98, 0.12, 0.12]} />} />
        </group>
      )}</Comp>
      {["r", "y", "b"].map((ph) => (
        <group key={ph}>
          <Comp id={`lv_winding_${ph}`} {...P}>{(p) => (
            <Winding p={p} radius={0.1} height={0.56} x={PHASE_X[ph]} profile={prof(ph, "lv")} scale={scale} />
          )}</Comp>
          <Comp id={`hv_winding_${ph}`} {...P}>{(p) => (
            <Winding p={p} radius={0.155} height={0.62} x={PHASE_X[ph]} profile={prof(ph, "hv")} scale={scale} />
          )}</Comp>
        </group>
      ))}

      {/* radiators on both long sides: pressed-steel panels on a top and a
          bottom header, each header piped into the tank wall, so the oil path
          (out at the top, back at the bottom) reads at a glance */}
      <Comp id="radiators" {...P}>{(p) => (
        <group>
          {[1, -1].map((sd) => (
            <group key={sd}>
              {fins.map((x) => (
                <Part key={x} {...p} position={[x, 0.5, sd * 0.5]} geometry={<boxGeometry args={[0.018, 0.7, 0.2]} />} />
              ))}
              {[0.86, 0.14].map((y) => (
                <group key={y}>
                  <Part {...p} position={[0, y, sd * 0.43]} rotation={[0, 0, Math.PI / 2]}
                    geometry={<cylinderGeometry args={[0.022, 0.022, 0.98, 14]} />} />
                  {[-0.3, 0.3].map((x) => (
                    <Part key={x} {...p} position={[x, y, sd * 0.4]} rotation={[Math.PI / 2, 0, 0]}
                      geometry={<cylinderGeometry args={[0.028, 0.028, 0.06, 14]} />} />
                  ))}
                </group>
              ))}
            </group>
          ))}
        </group>
      )}</Comp>

      {/* conservator with oil gauge, breather */}
      <Comp id="conservator" {...P}>{(p) => (
        <group>
          <Part {...p} position={[0, 1.38, -0.22]} rotation={[0, 0, Math.PI / 2]}
            geometry={<cylinderGeometry args={[0.12, 0.12, 0.82, 24]} />} />
          <Part {...p} position={[-0.42, 1.38, -0.22]} rotation={[0, 0, Math.PI / 2]}
            geometry={<cylinderGeometry args={[0.05, 0.05, 0.02, 20]} />} />
          <Part {...p} position={[0, 1.18, -0.22]} geometry={<cylinderGeometry args={[0.02, 0.02, 0.2, 10]} />} />
        </group>
      )}</Comp>
      <Comp id="breather" {...P}>{(p) => (
        <Part {...p} position={[0.48, 1.18, -0.22]} geometry={<cylinderGeometry args={[0.04, 0.04, 0.18, 16]} />} />
      )}</Comp>

      {/* bushings: 3 HV, 4 LV (R, Y, B, N) */}
      {["r", "y", "b"].map((ph) => {
        const x = HV_X[ph];
        return (
          <Comp key={ph} id={`hv_bushing_${ph}`} {...P}>{(p) => (
            <group>
              <Part {...p} position={[x, 1.115, 0.08]} geometry={<cylinderGeometry args={[0.075, 0.075, 0.02, 20]} />} />
              <Part {...p} position={[x, 1.3, 0.08]} geometry={<cylinderGeometry args={[0.028, 0.042, 0.36, 16]} />} />
              {[0, 1, 2, 3, 4].map((k) => (
                <Part key={k} {...p} position={[x, 1.17 + k * 0.065, 0.08]}
                  geometry={<cylinderGeometry args={[k % 2 ? 0.055 : 0.068, k % 2 ? 0.055 : 0.068, 0.012, 20]} />} />
              ))}
              <Part {...p} color={METAL} position={[x, 1.52, 0.08]} geometry={<cylinderGeometry args={[0.012, 0.012, 0.08, 10]} />} />
            </group>
          )}</Comp>
        );
      })}
      {["r", "y", "b", "n"].map((ph) => {
        const x = LV_X[ph];
        return (
          <Comp key={ph} id={`lv_bushing_${ph}`} {...P}>{(p) => (
            <group>
              <Part {...p} position={[x, 1.2, 0.27]} geometry={<cylinderGeometry args={[0.022, 0.034, 0.16, 14]} />} />
              {[0, 1].map((k) => (
                <Part key={k} {...p} position={[x, 1.16 + k * 0.06, 0.27]}
                  geometry={<cylinderGeometry args={[0.045, 0.045, 0.01, 16]} />} />
              ))}
              <Part {...p} color={METAL} position={[x, 1.31, 0.27]} geometry={<cylinderGeometry args={[0.01, 0.01, 0.07, 8]} />} />
              <Part {...p} color={METAL} position={[x, 1.35, 0.29]} geometry={<boxGeometry args={[0.03, 0.025, 0.05]} />} />
            </group>
          )}</Comp>
        );
      })}
      <Html position={[HV_X.r - 0.17, 1.5, 0.08]} center zIndexRange={[1, 0]} style={{ pointerEvents: "none" }}>
        <div className="scene-tag">HV</div>
      </Html>
      <Html position={[LV_X.r - 0.12, 1.36, 0.27]} center zIndexRange={[1, 0]} style={{ pointerEvents: "none" }}>
        <div className="scene-tag">LV</div>
      </Html>

      {/* fittings and instrumentation */}
      <Comp id="oil_pocket" {...P}>{(p) => (
        <Part {...p} position={[0.5, 1.13, -0.1]} geometry={<cylinderGeometry args={[0.025, 0.025, 0.08, 12]} />} />
      )}</Comp>
      <Comp id="tap_switch" {...P}>{(p) => (
        <Part {...p} position={[-0.55, 1.13, -0.2]} geometry={<cylinderGeometry args={[0.05, 0.05, 0.06, 16]} />} />
      )}</Comp>
      <Comp id="drain_valve" {...P}>{(p) => (
        <Part {...p} position={[0.55, 0.1, 0.4]} rotation={[Math.PI / 2, 0, 0]} geometry={<cylinderGeometry args={[0.03, 0.03, 0.1, 12]} />} />
      )}</Comp>
      <Comp id="earthing" {...P}>{(p) => (
        <Part {...p} position={[-0.67, 0.12, 0]} geometry={<boxGeometry args={[0.04, 0.06, 0.1]} />} />
      )}</Comp>
      <Comp id="monitor_box" {...P}>{(p) => (
        <Part {...p} position={[0.7, 0.6, 0.12]} geometry={<boxGeometry args={[0.1, 0.34, 0.3]} />} />
      )}</Comp>

      {/* plinth, so the unit does not float */}
      <mesh position={[0, -0.04, 0]} receiveShadow>
        <boxGeometry args={[1.8, 0.08, 1.4]} />
        <meshStandardMaterial color="#8c959f" roughness={0.9} />
      </mesh>
    </group>
  );
}

const HOME = [2.75, 2.0, 3.15, 0, 0.5, 0];         // the whole unit, bushings to plinth

// The vertical field of view is fixed, so a tall, narrow panel (a 1366 px
// projector) sees less width: step back in proportion.
const backOff = (aspect) => Math.max(1, 1.12 / aspect);

function Rig({ controls, fly, onStats }) {
  const { gl, size } = useThree();
  const ease = !reducedMotion();
  const f = backOff(size.width / Math.max(size.height, 1));
  useEffect(() => {                                // start framed on the whole unit; Reset returns here
    if (!controls.current) return;
    const [px, py, pz, tx, ty, tz] = HOME;
    controls.current.setLookAt(tx + (px - tx) * f, ty + (py - ty) * f, tz + (pz - tz) * f, tx, ty, tz, false);
    controls.current.saveState();
  }, [controls, f]);
  // Keyed on the target, not the array: a new state every few seconds in live
  // mode must not pull the camera back while someone is orbiting.
  const flyKey = fly ? fly.join(",") : null;
  useEffect(() => {
    if (!flyKey || !controls.current) return;
    const [x, y, z] = flyKey.split(",").map(Number);
    // keep the whole unit in view: aim at the fault, stay at a full-tank distance
    const t = [x * 0.6, 0.55 + (y - 0.55) * 0.5, z * 0.6];
    controls.current.setLookAt(t[0] + 2.3 * f, t[1] + 1.3 * f, t[2] + 2.8 * f, ...t, ease);
  }, [flyKey, controls, ease, f]);
  useFrame(() => onStats?.(gl.info.render.triangles));
  return null;
}

// The faulted part carries a label that stays on screen: what is wrong, in words.
function Callout({ callout }) {
  if (!callout || !FOCUS[callout.id]) return null;
  const [x, y, z] = FOCUS[callout.id];
  return (
    <Html position={[x, y + 0.42, z]} center zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
      <div className={`callout callout-${callout.tone}`}>{callout.text}</div>
    </Html>
  );
}

// Same values as --scene-bg in index.css. Read from the theme prop, not the
// CSS variable: the page's theme attribute changes after this renders.
const SCENE_BG = { light: "#e8ebef", dark: "#11161d" };

export default function Scene({ comps, scale, sel, setSel, xray, flyTo, resetKey, onTriangles, fem, callout, theme }) {
  const bg = SCENE_BG[theme] || SCENE_BG.light;
  const [hover, setHover] = useState(null);
  const controls = useRef();
  useEffect(() => { controls.current?.reset(!reducedMotion()); }, [resetKey]);
  return (
    <Canvas shadows camera={{ position: [2.1, 1.6, 2.4], fov: 38 }} onPointerMissed={() => setSel(null)}
      gl={{ preserveDrawingBuffer: true }}>
      <color attach="background" args={[bg]} />
      <hemisphereLight args={["#dfe7ef", "#1b222b", 0.8]} />
      <directionalLight position={[3, 4, 2]} intensity={1.4} castShadow />
      <directionalLight position={[-3, 2, -2]} intensity={0.4} />
      <Transformer comps={comps} scale={scale} sel={sel} setSel={setSel} hover={hover} setHover={setHover} xray={xray} fem={fem} />
      <Callout callout={callout} />
      <CameraControls ref={controls} minDistance={1.2} maxDistance={8} smoothTime={0.6} draggingSmoothTime={0.12}
        maxPolarAngle={Math.PI * 0.49} />
      <Rig controls={controls} fly={flyTo} onStats={onTriangles} />
    </Canvas>
  );
}
