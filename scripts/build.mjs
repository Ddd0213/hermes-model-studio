import { build } from 'esbuild';
import { copyFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dist = resolve(root, 'dashboard/dist');

await mkdir(dist, { recursive: true });
await build({
  absWorkingDir: root,
  entryPoints: ['dashboard/src/index.jsx'],
  bundle: true,
  format: 'iife',
  target: 'es2020',
  jsxFactory: 'React.createElement',
  jsxFragment: 'React.Fragment',
  minify: true,
  charset: 'utf8',
  outfile: resolve(dist, 'index.js'),
});
await copyFile(resolve(root, 'dashboard/src/style.css'), resolve(dist, 'style.css'));

console.log('Built dashboard/dist/index.js and dashboard/dist/style.css');
