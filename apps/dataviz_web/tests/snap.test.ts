import { describe, expect, it } from "vitest";
import { computeSnap } from "../src/authoring/AuthoringCanvas";

describe("Smart snapping and alignment guides", () => {
  const canvas = { width: 1280, height: 720 };
  const others = [
    { geometry: { x: 200, y: 100, width: 300, height: 200 } }, // right=500, bottom=300, centerX=350, centerY=200
  ];

  it("snaps left edge to an adjacent visual right edge when within threshold", () => {
    // Attempting to place at x=503 (within 6px of right=500)
    const res = computeSnap({ x: 503, y: 50, width: 200, height: 100 }, others, canvas, { threshold: 6 });
    expect(res.x).toBe(500);
    expect(res.guidesX).toContain(500);
  });

  it("snaps center to center of another visual", () => {
    // other center is 350. Target width is 100. Center would be x + 50.
    // If x=298, center is 348 (within 2px of 350).
    const res = computeSnap({ x: 298, y: 50, width: 100, height: 100 }, others, canvas, { threshold: 6 });
    expect(res.x).toBe(300); // 300 + 50 = 350
    expect(res.guidesX).toContain(350);
  });

  it("snaps to canvas center when close", () => {
    // canvas center X is 640. Target width 200 (half is 100).
    // If x=538, center is 638 (within 2px of 640).
    const res = computeSnap({ x: 538, y: 50, width: 200, height: 100 }, [], canvas, { threshold: 6 });
    expect(res.x).toBe(540); // 540 + 100 = 640
    expect(res.guidesX).toContain(640);
  });

  it("snaps to 10px grid if no visual aligns", () => {
    // At x=123, close to 120 (within 3px)
    const res = computeSnap({ x: 123, y: 50, width: 100, height: 100 }, [], canvas, { threshold: 6, gridSize: 10 });
    expect(res.x).toBe(120);
  });

  it("does not snap when disabled (Alt key)", () => {
    const res = computeSnap({ x: 503, y: 53, width: 200, height: 100 }, others, canvas, { disabled: true });
    expect(res.x).toBe(503);
    expect(res.y).toBe(53);
    expect(res.guidesX).toHaveLength(0);
    expect(res.guidesY).toHaveLength(0);
  });
});
