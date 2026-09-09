#!/usr/bin/env node
// OPP-CLASS: B1
// BPMN 2.0 XML → SVG через bpmn-js (viewer) в headless jsdom (без chromium/canvas).
// Шимы SVG-API адаптированы из dattmavis/BPMN-MCP и доработаны: (1) рабочая матрица,
// (2) createSVGTransform().setMatrix хранит матрицу, (3) transform.baseVal ОТРАЖАЕТСЯ
// в атрибут transform="matrix(...)" (иначе позиционирование фигур теряется при saveSVG),
// (4) canvas.getContext('2d').measureText приближённый (без node-canvas).
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");
const sharp = require("sharp");

function mkMatrix(a, b, c, d, e, f) {
  return {
    a, b, c, d, e, f,
    multiply(o) {
      return mkMatrix(
        this.a * o.a + this.c * o.b, this.b * o.a + this.d * o.b,
        this.a * o.c + this.c * o.d, this.b * o.c + this.d * o.d,
        this.a * o.e + this.c * o.f + this.e, this.b * o.e + this.d * o.f + this.f);
    },
    translate(x, y) { return this.multiply(mkMatrix(1, 0, 0, 1, x, y)); },
    scale(s) { return this.multiply(mkMatrix(s, 0, 0, s, 0, 0)); },
    scaleNonUniform(sx, sy) { return this.multiply(mkMatrix(sx, 0, 0, sy, 0, 0)); },
    rotate() { return this; }, inverse() { return this; },
    flipX() { return this; }, flipY() { return this; }, skewX() { return this; }, skewY() { return this; },
  };
}

function loadBpmn() {
  const bundlePath = path.join(__dirname, "node_modules/bpmn-js/dist/bpmn-viewer.development.js");
  const bundle = fs.readFileSync(bundlePath, "utf-8");
  const dom = new JSDOM(
    "<!DOCTYPE html><html><body><div id='canvas'></div></body></html>",
    { runScripts: "outside-only", pretendToBeVisual: true }
  );
  const win = dom.window;

  win.CSS = { escape: (s) => s.replace(/[!"#$%&'()*+,.\/:;<=>?@[\\\]^`{|}~]/g, "\\$&") };
  if (!win.structuredClone) win.structuredClone = (o) => JSON.parse(JSON.stringify(o));
  win.SVGMatrix = function () { return mkMatrix(1, 0, 0, 1, 0, 0); };

  const SVGElement = win.SVGElement;
  const SVGGraphicsElement = win.SVGGraphicsElement;
  const SVGSVGElement = win.SVGSVGElement;

  if (SVGElement && !SVGElement.prototype.getBBox)
    SVGElement.prototype.getBBox = function () {
      // text-aware: для подписей возвращаем размер по содержимому (центрирование меток и pool)
      const t = this.textContent || "";
      const tag = (this.tagName || "").toLowerCase();
      if (t && (tag === "text" || tag === "tspan")) {
        const lines = t.split("\n");
        const w = Math.max.apply(null, lines.map((l) => l.length)) * 6;
        return { x: 0, y: 0, width: w, height: 14 * lines.length };
      }
      return { x: 0, y: 0, width: 100, height: 100 };
    };
  if (SVGElement && !SVGElement.prototype.getScreenCTM)
    SVGElement.prototype.getScreenCTM = function () { return mkMatrix(1, 0, 0, 1, 0, 0); };
  if (SVGElement && !SVGElement.prototype.getCTM)
    SVGElement.prototype.getCTM = function () { return mkMatrix(1, 0, 0, 1, 0, 0); };

  // transform.baseVal → ОТРАЖАЕМ в атрибут transform (иначе позиция фигур теряется)
  const transformProp = {
    get() {
      const el = this;
      if (!el._transform) {
        const reflect = (items) => {
          if (!items.length) { el.removeAttribute && el.removeAttribute("transform"); return; }
          const s = items.map((it) => {
            const m = (it && it.matrix) || mkMatrix(1, 0, 0, 1, 0, 0);
            return "matrix(" + [m.a, m.b, m.c, m.d, m.e, m.f].join(" ") + ")";
          }).join(" ");
          el.setAttribute && el.setAttribute("transform", s);
        };
        const list = {
          numberOfItems: 0, _items: [],
          consolidate() { return this._items[0] || null; },
          clear() { this._items = []; this.numberOfItems = 0; reflect(this._items); },
          initialize(n) { this._items = [n]; this.numberOfItems = 1; reflect(this._items); return n; },
          getItem(i) { return this._items[i]; },
          insertItemBefore(n, i) { this._items.splice(i, 0, n); this.numberOfItems = this._items.length; reflect(this._items); return n; },
          replaceItem(n, i) { this._items[i] = n; this.numberOfItems = this._items.length; reflect(this._items); return n; },
          removeItem(i) { const it = this._items.splice(i, 1)[0]; this.numberOfItems = this._items.length; reflect(this._items); return it; },
          appendItem(n) { this._items.push(n); this.numberOfItems = this._items.length; reflect(this._items); return n; },
          createSVGTransformFromMatrix(m) { return { type: 1, matrix: m, angle: 0 }; },
        };
        el._transform = { baseVal: list, animVal: list };
      }
      return el._transform;
    },
  };
  if (SVGGraphicsElement) Object.defineProperty(SVGGraphicsElement.prototype, "transform", transformProp);
  if (SVGElement) Object.defineProperty(SVGElement.prototype, "transform", transformProp);

  if (SVGSVGElement) {
    SVGSVGElement.prototype.createSVGMatrix = function () { return mkMatrix(1, 0, 0, 1, 0, 0); };
    SVGSVGElement.prototype.createSVGPoint = function () { return { x: 0, y: 0, matrixTransform() { return this; } }; };
    SVGSVGElement.prototype.createSVGTransform = function () {
      return {
        type: 0, matrix: mkMatrix(1, 0, 0, 1, 0, 0), angle: 0,
        setMatrix(m) { this.matrix = m; this.type = 1; },
        setTranslate(tx, ty) { this.matrix = mkMatrix(1, 0, 0, 1, tx, ty); this.type = 2; },
        setScale(sx, sy) { this.matrix = mkMatrix(sx, 0, 0, sy, 0, 0); this.type = 3; },
        setRotate(a) { this.angle = a; this.type = 4; },
      };
    };
    SVGSVGElement.prototype.createSVGTransformFromMatrix = function (m) { return { type: 1, matrix: m, angle: 0 }; };
  }

  // canvas.measureText (без node-canvas): приближённая ширина
  if (win.HTMLCanvasElement) {
    win.HTMLCanvasElement.prototype.getContext = function () {
      return {
        font: "", textBaseline: "", textAlign: "",
        measureText: (s) => ({ width: (s ? String(s).length : 0) * 6 }),
        fillText() {}, strokeText() {}, fillRect() {}, clearRect() {}, strokeRect() {},
        beginPath() {}, closePath() {}, moveTo() {}, lineTo() {}, arc() {}, rect() {},
        stroke() {}, fill() {}, save() {}, restore() {}, scale() {}, translate() {},
        rotate() {}, setTransform() {}, transform() {}, setLineDash() {}, clip() {},
        createLinearGradient: () => ({ addColorStop() {} }),
        getImageData: () => ({ data: [] }), putImageData() {}, drawImage() {},
      };
    };
  }

  win.eval(bundle);
  global.document = win.document;
  global.window = win;
  return { Viewer: win.BpmnJS, container: win.document.getElementById("canvas") };
}

function diagramBounds(xml) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const num = (s, k) => { const m = new RegExp(k + '="([-0-9.]+)"').exec(s); return m ? parseFloat(m[1]) : null; };
  let m;
  const bre = /<(?:dc:)?Bounds\s+([^>]*?)\/?>/g;
  while ((m = bre.exec(xml))) {
    const x = num(m[1], "x"), y = num(m[1], "y"), w = num(m[1], "width") || 0, h = num(m[1], "height") || 0;
    if (x == null || y == null) continue;
    minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x + w); maxY = Math.max(maxY, y + h);
  }
  const wre = /<(?:di:)?waypoint\s+([^>]*?)\/?>/g;
  while ((m = wre.exec(xml))) {
    const x = num(m[1], "x"), y = num(m[1], "y");
    if (x == null || y == null) continue;
    minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
  }
  return isFinite(minX) ? { minX, minY, maxX, maxY } : null;
}

async function main() {
  const [inPath, outPath, pngPath] = process.argv.slice(2);
  if (!inPath || !outPath) { console.error("usage: render_bpmn.js <in.bpmn> <out.svg> [out.png]"); process.exit(2); }
  const xml = fs.readFileSync(inPath, "utf-8");
  const { Viewer, container } = loadBpmn();
  const viewer = new Viewer({ container });
  await viewer.importXML(xml);
  let { svg } = await viewer.saveSVG();
  const b = diagramBounds(xml);
  if (b) {
    const pad = 30;
    const x = b.minX - pad, y = b.minY - pad, w = (b.maxX - b.minX) + 2 * pad, h = (b.maxY - b.minY) + 2 * pad;
    svg = svg.replace(/<svg([^>]*)>/, (mm, attrs) => {
      attrs = attrs.replace(/\s(width|height|viewBox)="[^"]*"/g, "");
      return `<svg${attrs} width="${w}" height="${h}" viewBox="${x} ${y} ${w} ${h}">` +
        "<style>text,tspan{font-family:'Noto Sans','PT Sans',sans-serif;}</style>";
    });
  }
  fs.writeFileSync(outPath, svg, "utf-8");
  if (pngPath) {
    await sharp(Buffer.from(svg, "utf-8")).flatten({ background: "white" }).png().toFile(pngPath);
  }
  console.log("SVG:", outPath, "(", svg.length, "bytes )");
  if (pngPath) console.log("PNG:", pngPath);
}
main().catch((e) => { console.error("render_bpmn:", e && e.message ? e.message : e); process.exit(1); });
