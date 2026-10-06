import { readFile, readdir } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';

const budgets = [
  // 228 KiB: effect 4.0.1 añadió ~0.1 KiB al paquete viewer (226 → 226.07).
  { file: 'dist/dataviz-web.es.js', gzip: 64 * 1024, raw: 228 * 1024 },
  { file: 'dist/dataviz-web.umd.js', gzip: 57 * 1024, raw: 176 * 1024 },
  { file: 'dist/studio/dataviz-studio.js', gzip: 70 * 1024, raw: 260 * 1024 },
];

const routeBudgets = [
  {
    chunkPrefixes: ['AuthoringEntry-', 'base-'],
    gzip: 100 * 1024,
    label: 'DataVIZ Studio authoring route',
    raw: 310 * 1024,
  },
  {
    chunkPrefixes: ['ViewerEntry-', 'base-'],
    gzip: 125 * 1024,
    label: 'DataVIZ Studio viewer route',
    raw: 380 * 1024,
  },
];

const kib = (bytes) => `${(bytes / 1024).toFixed(2)} KiB`;
let exceeded = false;

for (const budget of budgets) {
  const content = await readFile(new URL(`../${budget.file}`, import.meta.url));
  const sizes = { gzip: gzipSync(content).byteLength, raw: content.byteLength };
  const failures = Object.entries(sizes)
    .filter(([kind, size]) => size > budget[kind])
    .map(([kind, size]) => `${kind} ${kib(size)} > ${kib(budget[kind])}`);

  console.log(
    `${budget.file}: raw ${kib(sizes.raw)} / ${kib(budget.raw)}, `
      + `gzip ${kib(sizes.gzip)} / ${kib(budget.gzip)}`,
  );
  if (failures.length > 0) {
    exceeded = true;
    console.error(`Bundle budget exceeded: ${failures.join(', ')}`);
  }
}

const chunksDirectory = new URL('../dist/studio/chunks/', import.meta.url);
const chunks = await readdir(chunksDirectory);
for (const budget of routeBudgets) {
  const routeFiles = [
    new URL('../dist/studio/dataviz-studio.js', import.meta.url),
    ...chunks
      .filter((file) => budget.chunkPrefixes.some((prefix) => file.startsWith(prefix)))
      .map((file) => new URL(`../dist/studio/chunks/${file}`, import.meta.url)),
  ];
  const contents = await Promise.all(routeFiles.map((file) => readFile(file)));
  // Summing per-file gzip sizes is intentionally conservative: it models each
  // requested chunk as a separate compressed HTTP response.
  const sizes = {
    gzip: contents.reduce((total, content) => total + gzipSync(content).byteLength, 0),
    raw: contents.reduce((total, content) => total + content.byteLength, 0),
  };
  const failures = Object.entries(sizes)
    .filter(([kind, size]) => size > budget[kind])
    .map(([kind, size]) => `${kind} ${kib(size)} > ${kib(budget[kind])}`);

  console.log(
    `${budget.label}: raw ${kib(sizes.raw)} / ${kib(budget.raw)}, `
      + `gzip ${kib(sizes.gzip)} / ${kib(budget.gzip)}`,
  );
  if (failures.length > 0) {
    exceeded = true;
    console.error(`Route budget exceeded: ${failures.join(', ')}`);
  }
}

if (exceeded) {process.exitCode = 1;}
