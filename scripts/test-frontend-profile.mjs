import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import {
	profileFeatures,
	resolveProfile,
	excludedRouteNames
} from '../src/lib/features/definition.js';
import {
	syncTree,
	prepareResearchFiles,
	researchPaths,
	root,
	forbiddenModule
} from './frontend-profile.mjs';

test('research is the default; invalid profiles fail rather than silently enabling features', () => {
	assert.equal(resolveProfile(), 'research');
	assert.throws(() => resolveProfile('reserach'));
	assert.ok(Object.values(profileFeatures('research')).every((value) => value === false));
	assert.ok(Object.values(profileFeatures('full')).every((value) => value === true));
});

test('generated tree tracks additions, changes, deletions and excludes optional assets', () => {
	const dir = mkdtempSync(resolve(tmpdir(), 'frontend-profile-test-'));
	try {
		const src = resolve(dir, 'source'),
			dest = resolve(dir, 'generated');
		mkdirSync(resolve(src, 'pyodide'), { recursive: true });
		writeFileSync(resolve(src, 'pyodide/runtime.js'), 'excluded');
		writeFileSync(resolve(src, 'page.svelte'), 'first');
		const sync = () => syncTree(src, dest, (name) => name !== 'pyodide');
		sync();
		assert.equal(readFileSync(resolve(dest, 'page.svelte'), 'utf8'), 'first');
		assert.equal(existsSync(resolve(dest, 'pyodide')), false);
		writeFileSync(resolve(src, 'page.svelte'), 'updated page');
		writeFileSync(resolve(src, 'added.svelte'), 'new');
		sync();
		assert.equal(readFileSync(resolve(dest, 'page.svelte'), 'utf8'), 'updated page');
		assert.equal(readFileSync(resolve(dest, 'added.svelte'), 'utf8'), 'new');
		rmSync(resolve(src, 'page.svelte'));
		sync();
		assert.equal(existsSync(resolve(dest, 'page.svelte')), false);
	} finally {
		rmSync(dir, { recursive: true, force: true });
	}
});

test('research keeps research/admin/workspace routes and replaces only requested page trees', () => {
	prepareResearchFiles();
	for (const path of [
		'+layout.svelte',
		'(app)/+page.svelte',
		'(app)/c/[id]/+page.svelte',
		'(app)/admin/essays/+page.svelte',
		'(app)/admin/analytics/[tab]/+page.svelte',
		'(app)/admin/functions/+page.svelte',
		'(app)/workspace/knowledge/+page.svelte'
	]) {
		assert.equal(
			readFileSync(resolve(researchPaths.routes, path), 'utf8'),
			readFileSync(resolve(root, 'src/routes', path), 'utf8')
		);
	}
	for (const route of excludedRouteNames) {
		const file = readFileSync(
			resolve(researchPaths.routes, '(app)', route, '+page.svelte'),
			'utf8'
		);
		assert.match(file, /ProfileRedirect/);
		assert.ok(existsSync(resolve(root, 'src/routes/(app)', route)));
	}
	assert.equal(existsSync(resolve(researchPaths.assets, 'pyodide')), false);
	assert.ok(existsSync(resolve(researchPaths.assets, 'static/favicon.png')));
});

test('audit rejects excluded workers, feature implementations and all prohibited packages', () => {
	for (const dependency of [
		'pyodide',
		'kokoro-js',
		'@huggingface/transformers',
		'onnxruntime-web',
		'@xterm/xterm',
		'@xyflow/svelte'
	]) {
		assert.equal(
			forbiddenModule(resolve(root, 'node_modules', dependency, 'index.js')),
			true,
			dependency
		);
	}
	assert.equal(
		forbiddenModule(resolve(root, 'src/lib/workers/pyodide.worker.ts') + '?worker'),
		true
	);
	assert.equal(forbiddenModule(resolve(root, 'src/lib/components/notes/NoteEditor.svelte')), true);
	for (const file of [
		'src/lib/components/admin/ResearchDashboard.svelte',
		'src/lib/components/chat/ExperimentTaskSidebar.svelte',
		'src/lib/components/common/PDFViewer.svelte',
		'src/lib/components/channel/Messages/Message/ProfilePreview.svelte'
	]) {
		assert.equal(forbiddenModule(resolve(root, file)), false, file);
	}
});

test('stale execution flags are blocked without altering research IDs, prompts, files or other features', async () => {
	const { constrainChatRequest } = await import('../src/lib/features/definition.js');
	const original = {
		model: 'study-model',
		chat_id: 'chat-1',
		session_id: 'session-1',
		metadata: { experiment_session_task_id: 'task-1' },
		messages: [{ role: 'user', content: 'Essay help' }],
		files: [{ id: 'question-image' }],
		features: { voice: true, code_interpreter: true, web_search: true, memory: true },
		terminal_id: 'old-terminal'
	};
	const result = constrainChatRequest('research', original);
	assert.equal(result.features.voice, false);
	assert.equal(result.features.code_interpreter, false);
	assert.equal(result.terminal_id, undefined);
	assert.equal(result.features.web_search, true);
	for (const key of ['metadata', 'messages', 'files', 'model', 'session_id', 'chat_id'])
		assert.equal(result[key], original[key]);
	assert.equal(original.features.voice, true);
	assert.equal(original.terminal_id, 'old-terminal');
	assert.equal(constrainChatRequest('full', original), original);
});

test('excluded Python RPCs complete their callback once, while full mode keeps the original handler', async () => {
	const { rejectPythonExecution } = await import('../src/lib/features/definition.js');
	const replies = [];
	assert.equal(
		rejectPythonExecution('research', (reply) => replies.push(reply)),
		true
	);
	assert.equal(replies.length, 1);
	assert.match(replies[0].stderr, /unavailable/);
	assert.equal(
		rejectPythonExecution('full', (reply) => replies.push(reply)),
		false
	);
	assert.equal(replies.length, 1);
	assert.equal(rejectPythonExecution('research'), true);
});
