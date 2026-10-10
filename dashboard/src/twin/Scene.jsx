import { useEffect, useMemo, useRef, useState } from "react";
import { CylinderGeometry, Float32BufferAttribute } from "three";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { CameraControls, Html, Outlines } from "@react-three/drei";
import { hotspotColor, reducedMotion, SELECT, STATE, VIOLET } from "./states.js";

// A three-phase ONAN distribution transformer built from primitives: no fans,
// no on-load tap changer, no motor drive. Every component is a group named by
// its id in config/components.yaml. Units: metres. Phases R, Y, B along x.

import { FOCUS, PHASE_X } from "./focus.js";

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
      {comp?.state === "sensor" && (
        <Html position={(FOCUS[id] || [0, 0, 0]).map((v, k) => v + (k === 1 ? 0.14 : 0))} center
          distanceFactor={2.4} style={{ pointerEvents: "none" }}>
          <div className="twin-tip twin-tip-sensor">Instrument fault, transformer unaffected</div>
        </Html>
      )}
    </group>
  );
}

function Transformer({ comps, scale, sel, setSel, hover, setHover, xray, fem }) {
  const prof = (ph, w) => fem?.phases?.[ph.toUpperCase()]?.blocks?.[w]?.axial_max_c || null;
  const P = { comps, sel, setSel, hover, setHover };
  const shell = xray ? 0.12 : 1;
  const fins = useMemo(() => Array.from({ length: 7 }, (_, k) => -0.42 + k * 0.14), []);
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

      {/* radiators on both long sides */}
      <Comp id="radiators" {...P}>{(p) => (
        <group>
          {[0.5, -0.5].map((z) => fins.map((x) => (
            <Part key={`${z}${x}`} {...p} position={[x, 0.5, z]} geometry={<boxGeometry args={[0.025, 0.82, 0.22]} />} />
          )))}
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
      {["r", "y", "b"].map((ph) => (
        <Comp key={ph} id={`hv_bushing_${ph}`} {...P}>{(p) => (
          <group>
            <Part {...p} position={[PHASE_X[ph] * 0.85, 1.28, 0.08]} geometry={<cylinderGeometry args={[0.03, 0.045, 0.36, 14]} />} />
            {[0, 1, 2].map((k) => (
              <Part key={k} {...p} position={[PHASE_X[ph] * 0.85, 1.18 + k * 0.1, 0.08]}
                geometry={<cylinderGeometry args={[0.07, 0.07, 0.015, 18]} />} />
            ))}
          </group>
        )}</Comp>
      ))}
      {["r", "y", "b", "n"].map((ph, k) => (
        <Comp key={ph} id={`lv_bushing_${ph}`} {...P}>{(p) => (
          <Part {...p} position={[-0.45 + k * 0.3, 1.18, 0.27]} geometry={<cylinderGeometry args={[0.025, 0.035, 0.18, 12]} />} />
        )}</Comp>
      ))}

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
        <meshStandardMaterial color="#1f2a37" roughness={0.9} />
      </mesh>
    </group>
  );
}

const HOME = [2.5, 1.9, 2.9, 0, 0.62, 0];         // the whole unit, bushings to plinth

function Rig({ controls, fly, onStats }) {
  const { gl } = useThree();
  useEffect(() => {                                // start framed on the whole unit; Reset returns here
    if (!controls.current) return;
    controls.current.setLookAt(...HOME, false);
    controls.current.saveState();
  }, [controls]);
  useEffect(() => {
    if (!fly || !controls.current) return;
    const [x, y, z] = fly;
    // keep the whole unit in view: aim at the fault, stay at a full-tank distance
    controls.current.setLookAt(x + 1.9, y + 1.0, z + 2.3, x * 0.6, 0.55 + (y - 0.55) * 0.5, z * 0.6, true);
  }, [fly, controls]);
  useFrame(() => onStats?.(gl.info.render.triangles));
  return null;
}

export default function Scene({ comps, scale, sel, setSel, xray, flyTo, resetKey, onTriangles, fem }) {
  const [hover, setHover] = useState(null);
  const controls = useRef();
  useEffect(() => { controls.current?.reset(true); }, [resetKey]);
  return (
    <Canvas shadows camera={{ position: [2.1, 1.6, 2.4], fov: 38 }} onPointerMissed={() => setSel(null)}
      gl={{ preserveDrawingBuffer: true }}>
      <color attach="background" args={["#0e141b"]} />
      <hemisphereLight args={["#dfe7ef", "#1b222b", 0.8]} />
      <directionalLight position={[3, 4, 2]} intensity={1.4} castShadow />
      <directionalLight position={[-3, 2, -2]} intensity={0.4} />
      <Transformer comps={comps} scale={scale} sel={sel} setSel={setSel} hover={hover} setHover={setHover} xray={xray} fem={fem} />
      <CameraControls ref={controls} minDistance={1.2} maxDistance={6} />
      <Rig controls={controls} fly={flyTo} onStats={onTriangles} />
    </Canvas>
  );
}
