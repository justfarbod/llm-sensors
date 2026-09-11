import {
	readFileSync,
	writeFileSync,
	mkdirSync,
	readdirSync,
	statSync,
	existsSync,
	copyFileSync,
	rmSync,
	utimesSync
} from 'node:fs';
import { resolve, relative, dirname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { excludedRouteNames, resolveProfile } from '../src/lib/features/definition.js';

export const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const profile = resolveProfile(process.env.FRONTEND_PROFILE);
const generated = resolve(root, '.generated/research');
/** @param {string} path */
const relativeName = (path) => relative(root, path).split(sep).join('/');
export const researchPaths = { routes: `${generated}/routes`, assets: `${generated}/static` };

export const optionalComponents = [
	'chat/MessageInput/CallOverlay',
	'chat/MessageInput/VoiceRecording',
	'chat/Settings/Audio',
	'admin/Settings/Audio',
	'admin/Settings/CodeExecution',
	'chat/FileNav',
	'chat/PyodideFileNav',
	'chat/Overview',
	'chat/MessageInput/TerminalMenu',
	'chat/Settings/Integrations/Terminals',
	'workspace/Models/TerminalSelector',
	'AddTerminalServerModal',
	'notes/Notes',
	'notes/NoteEditor',
	'notes/NotePanel',
	'chat/MessageInput/InputMenu/Notes',
	'layout/Sidebar/ChannelItem',
	'layout/Sidebar/ChannelModal',
	'AutomationModal'
].map((name) => `src/lib/components/${name}.svelte`);

const replacements = new Map(
	optionalComponents.map((name) => [name, `${generated}/disabled/${name}`])
);
replacements.set(
	'src/lib/features/full/FlowProvider.svelte',
	resolve(root, 'src/lib/features/disabled/FlowProvider.svelte')
);
replacements.set(
	'src/lib/components/notes/utils.ts',
	resolve(root, 'src/lib/features/disabled/notes.ts')
);
replacements.set(
	'src/lib/workers/KokoroWorker.ts',
	resolve(root, 'src/lib/features/disabled/KokoroWorker.ts')
);
replacements.set(
	'src/lib/workers/pyodide.worker.ts',
	resolve(root, 'src/lib/features/disabled/PythonWorker.ts')
);
replacements.set(
	'src/lib/pyodide/pyodideKernel.worker.ts',
	resolve(root, 'src/lib/features/disabled/PythonWorker.ts')
);

/** @param {string} path @param {string} content */
function writeChanged(path, content) {
	if (existsSync(path) && readFileSync(path, 'utf8') === content) return;
	mkdirSync(dirname(path), { recursive: true });
	writeFileSync(path, content);
}

/** @param {string} source @param {string} destination @param {(name: string) => boolean} keep @param {string} prefix @param {(name: string) => boolean} preserve */
export function syncTree(
	source,
	destination,
	keep = () => true,
	prefix = '',
	preserve = () => false
) {
	mkdirSync(destination, { recursive: true });
	const names = new Set();
	for (const entry of readdirSync(source, { withFileTypes: true })) {
		const name = prefix ? `${prefix}/${entry.name}` : entry.name;
		if (!keep(name)) continue;
		names.add(entry.name);
		const src = resolve(source, entry.name),
			dest = resolve(destination, entry.name);
		if (entry.isDirectory()) syncTree(src, dest, keep, name, preserve);
		else {
			const info = statSync(src);
			const old = existsSync(dest) ? statSync(dest) : null;
			if (!old || info.size !== old.size || Math.abs(info.mtimeMs - old.mtimeMs) > 1) {
				copyFileSync(src, dest);
				utimesSync(dest, info.atime, info.mtime);
			}
		}
	}
	for (const entry of readdirSync(destination)) {
		const name = prefix ? `${prefix}/${entry}` : entry;
		if (!names.has(entry) && !preserve(name))
			rmSync(resolve(destination, entry), { recursive: true, force: true });
	}
}

export function prepareResearchFiles() {
	syncTree(
		resolve(root, 'src/routes'),
		researchPaths.routes,
		(name) =>
			!excludedRouteNames.some(
				(route) => name === `(app)/${route}` || name.startsWith(`(app)/${route}/`)
			),
		'',
		(name) => excludedRouteNames.some((route) => name === `(app)/${route}`)
	);
	const redirect = `<script>\nimport ProfileRedirect from '$lib/features/ProfileRedirect.svelte';\n</script>\n<ProfileRedirect />\n`;
	for (const route of excludedRouteNames) {
		for (const suffix of ['+page.svelte', '[...path]/+page.svelte']) {
			writeChanged(`${researchPaths.routes}/(app)/${route}/${suffix}`, redirect);
		}
	}
	syncTree(
		resolve(root, 'static'),
		researchPaths.assets,
		(name) => name !== 'pyodide' && !name.startsWith('pyodide/')
	);
	for (const name of optionalComponents) {
		const source = readFileSync(resolve(root, name), 'utf8');
		const props = [...source.matchAll(/export\s+let\s+(\w+)/g)].map((match) => match[1]);
		const methods = [...source.matchAll(/export\s+(?:const|function)\s+(\w+)/g)].map(
			(match) => match[1]
		);
		writeChanged(
			replacements.get(name) ??
				(() => {
					throw new Error(`Missing replacement: ${name}`);
				})(),
			`<script lang="ts">\n// Disabled only in the research profile; implementation remains in ${name}.\n${props.map((p) => `export let ${p}: any = undefined;`).join('\n')}\n${methods.map((p) => `export const ${p} = (..._args: any[]) => undefined;`).join('\n')}\n</script>\n`
		);
	}
}

const forbiddenPackage =
	/\/node_modules\/(?:pyodide|kokoro-js|@huggingface\/transformers|onnxruntime-web|@xterm\/[^/]+|@xyflow\/svelte)(?:\/|$)/;
/** @param {string} id */
export function forbiddenModule(id) {
	const clean = id.split('?')[0].replaceAll('\\', '/');
	const name = relativeName(clean);
	return (
		forbiddenPackage.test(clean) ||
		replacements.has(name) ||
		/^src\/lib\/components\/(notes|calendar|automations|playground)\//.test(name) ||
		/^src\/lib\/components\/channel\/(Channel\.svelte|MessageInput\.svelte|Messages\.svelte)$/.test(
			name
		) ||
		/^src\/routes\/\(app\)\/(notes|channels|calendar|automations|playground)\//.test(name)
	);
}

/** @returns {import('vite').Plugin} */
export function researchProfilePlugin() {
	return {
		name: 'research-profile',
		enforce: 'pre',
		config() {
			if (profile !== 'research') return;
			// SvelteKit allows the route and lib directories, but these substituted
			// components live outside both. Vite merges this with Kit's allow list.
			return { server: { fs: { allow: [resolve(generated, 'disabled')] } } };
		},
		buildStart() {
			if (profile !== 'research') return;
			prepareResearchFiles();
			/** @param {string} dir */
			const watch = (dir) => {
				this.addWatchFile(dir);
				for (const entry of readdirSync(dir, { withFileTypes: true })) {
					const path = resolve(dir, entry.name);
					if (entry.isDirectory()) watch(path);
					else this.addWatchFile(path);
				}
			};
			watch(resolve(root, 'src/routes'));
		},
		watchChange(id) {
			if (profile === 'research' && id.startsWith(resolve(root, 'src/routes') + sep))
				prepareResearchFiles();
		},
		async resolveId(source, importer, options) {
			if (profile !== 'research' || !importer || source.startsWith('\0')) return null;
			const canonicalImporter = importer.startsWith(researchPaths.routes + '/')
				? resolve(root, 'src/routes', relative(researchPaths.routes, importer))
				: importer;
			const resolved = await this.resolve(source, canonicalImporter, {
				...options,
				skipSelf: true
			});
			if (!resolved) return null;
			const [path] = resolved.id.split('?');
			const replacement = replacements.get(relativeName(path));
			// Worker boundaries become a disabled constructor module, not another worker build.
			return replacement ? { id: replacement } : canonicalImporter !== importer ? resolved : null;
		},
		transform(_code, id) {
			if (profile === 'research' && forbiddenModule(id))
				this.error(`Excluded feature entered research build: ${relativeName(id)}`);
		},
		generateBundle(options) {
			if (profile !== 'research') return;
			const modules = [...this.getModuleIds()];
			for (const id of modules)
				if (forbiddenModule(id)) this.error(`Excluded research module: ${id}`);
			writeChanged(
				`${generated}/audit/${options.dir?.includes('/client') ? 'client' : options.dir?.includes('/server') ? 'server' : 'worker'}.json`,
				JSON.stringify({ profile, modules: modules.map(relativeName).sort() }, null, 2)
			);
		},
		configureServer(server) {
			if (profile !== 'research') return;
			const paths = ['src/routes', 'static', ...optionalComponents].map((path) =>
				resolve(root, path)
			);
			server.watcher.add(paths);
			/** @param {string} _event @param {string} path */
			const sync = (_event, path) => {
				if (paths.some((source) => path === source || path.startsWith(source + sep)))
					prepareResearchFiles();
			};
			server.watcher.on('all', sync);
			server.httpServer?.once('close', () => server.watcher.off('all', sync));
		}
	};
}
