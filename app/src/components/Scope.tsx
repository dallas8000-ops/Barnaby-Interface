import { useEffect, useRef } from "react";
import type { EngineState } from "../lib/protocol";

interface Props {
  state: EngineState;
  level: number;
  speaking: boolean;
  size: number;
}

const HISTORY = 96;

function readColors(el: Element) {
  const cs = getComputedStyle(el);
  const v = (n: string) => cs.getPropertyValue(n).trim() || "#888";
  return { scope: v("--c-scope"), line: v("--c-line"), cyan: v("--c-cyan"), amber: v("--c-amber"), red: v("--c-red"), muted: v("--c-muted") };
}

/**
 * The radar scope. Listening: the mic level is plotted in polar form around the
 * range rings (a real waveform history, not a canned animation). Working: a
 * sweep arm. Speaking: amber range pulses. Waiting on approval: a red hold ring.
 * Idle is deliberately still.
 */
export function Scope({ state, level, speaking, size }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const live = useRef({ state, level, speaking });
  live.current = { state, level, speaking };

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const dpr = Math.max(1, window.devicePixelRatio || 1);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    const history = new Float32Array(HISTORY);
    let head = 0;
    let colors = readColors(canvas);
    let colorsAt = 0;
    let raf = 0;
    let staticKey = "";
    const cx = size / 2;
    const cy = size / 2;
    const R = size / 2 - 6;

    const draw = (t: number) => {
      if (t - colorsAt > 1000) {
        colors = readColors(canvas);
        colorsAt = t;
      }
      const { state: st, level: lv, speaking: sp } = live.current;
      const still = reduce.matches;
      const animating = st === "listening" || st === "thinking" || st === "transcribing" || st === "awaiting_approval" || sp;
      const key = `${st}|${sp}|${colors.scope}`;
      if (!animating && key === staticKey) {
        raf = requestAnimationFrame(draw);
        return; // idle: frame already on screen, skip the redraw
      }
      staticKey = animating ? "" : key;

      ctx.clearRect(0, 0, size, size);
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.fillStyle = colors.scope;
      ctx.fill();

      // range rings + crosshair
      ctx.strokeStyle = colors.line;
      ctx.lineWidth = 1;
      for (const f of [0.33, 0.66, 1]) {
        ctx.beginPath();
        ctx.arc(cx, cy, R * f, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.globalAlpha = 0.5;
      ctx.beginPath();
      ctx.moveTo(cx - R, cy);
      ctx.lineTo(cx + R, cy);
      ctx.moveTo(cx, cy - R);
      ctx.lineTo(cx, cy + R);
      ctx.stroke();
      ctx.globalAlpha = 1;

      if (st === "listening") {
        history[head] = lv;
        head = (head + 1) % HISTORY;
        ctx.beginPath();
        for (let i = 0; i <= HISTORY; i++) {
          const v = history[(head + i) % HISTORY] ?? 0;
          const a = (i / HISTORY) * Math.PI * 2 - Math.PI / 2;
          const r = R * 0.34 + R * 0.6 * Math.min(1, v * 1.4);
          const x = cx + Math.cos(a) * r;
          const y = cy + Math.sin(a) * r;
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.strokeStyle = colors.cyan;
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.globalAlpha = 0.12;
        ctx.fillStyle = colors.cyan;
        ctx.fill();
        ctx.globalAlpha = 1;
      } else {
        history.fill(0);
      }

      if (st === "thinking" || st === "transcribing") {
        const angle = still ? -Math.PI / 2 : ((t / (st === "transcribing" ? 700 : 1800)) % 1) * Math.PI * 2;
        const trail = Math.PI / 2.2;
        for (let i = 0; i < 24; i++) {
          const a0 = angle - (trail * (i + 1)) / 24;
          const a1 = angle - (trail * i) / 24;
          ctx.beginPath();
          ctx.moveTo(cx, cy);
          ctx.arc(cx, cy, R - 1, a0, a1);
          ctx.closePath();
          ctx.globalAlpha = 0.22 * (1 - i / 24);
          ctx.fillStyle = colors.cyan;
          ctx.fill();
        }
        ctx.globalAlpha = 1;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(angle) * R, cy + Math.sin(angle) * R);
        ctx.strokeStyle = colors.cyan;
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      if (sp) {
        for (let k = 0; k < 3; k++) {
          const phase = still ? 0.5 + k * 0.2 : ((t / 1400 + k / 3) % 1);
          ctx.beginPath();
          ctx.arc(cx, cy, R * (0.15 + 0.85 * phase), 0, Math.PI * 2);
          ctx.strokeStyle = colors.amber;
          ctx.globalAlpha = still ? 0.5 : 1 - phase;
          ctx.lineWidth = 2;
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
      }

      if (st === "awaiting_approval") {
        ctx.setLineDash([6, 6]);
        ctx.lineDashOffset = still ? 0 : -t / 60;
        ctx.beginPath();
        ctx.arc(cx, cy, R - 3, 0, Math.PI * 2);
        ctx.strokeStyle = colors.red;
        ctx.lineWidth = 3;
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // center blip
      const blip =
        st === "listening" ? colors.cyan : sp ? colors.amber : st === "awaiting_approval" ? colors.red : colors.muted;
      ctx.beginPath();
      ctx.arc(cx, cy, st === "listening" ? 5 + lv * 10 : 4, 0, Math.PI * 2);
      ctx.fillStyle = blip;
      ctx.fill();

      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [size]);

  return <canvas ref={canvasRef} width={size} height={size} style={{ width: size, height: size }} aria-hidden="true" />;
}
