import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { forbiddenModule, root } from './frontend-profile.mjs';

for (const target of ['client', 'server']) {
	const report = JSON.parse(
		readFileSync(resolve(root, `.generated/research/audit/${target}.json`), 'utf8')
	);
	assert.equal(report.profile, 'research');
	assert.ok(report.modules.length > 0, `Empty ${target} module graph`);
	for (const id of report.modules) assert.equal(forbiddenModule(resolve(root, id)), false, id);
	console.log(`${target}: ${report.modules.length} module IDs checked`);
}
const badAsset = /(?:pyodide|kokoro|onnx|ort-wasm|pyodideKernel|xterm|xyflow)/i;
function inspect(directory) {
	for (const entry of readdirSync(directory, { withFileTypes: true })) {
		const path = resolve(directory, entry.name);
		assert.equal(badAsset.test(entry.name), false, `Excluded runtime asset: ${path}`);
		if (entry.isDirectory()) inspect(path);
	}
}
assert.ok(existsSync(resolve(root, 'build/index.html')), 'No deployable app');
inspect(resolve(root, 'build'));
console.log('Research output contains no excluded runtime assets.');
