import { profile, researchProfilePlugin } from './scripts/frontend-profile.mjs';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

import { viteStaticCopy } from 'vite-plugin-static-copy';

export default defineConfig({
	cacheDir: `node_modules/.vite-${profile}`,
	plugins: [
		researchProfilePlugin(),
		sveltekit(),
		...(profile === 'full' ? [viteStaticCopy({
			targets: [
				{
					src: 'node_modules/onnxruntime-web/dist/*.jsep.*',

					dest: 'wasm'
				}
			]
		})] : [])
	],
	define: {
		__FRONTEND_PROFILE__: JSON.stringify(profile),
		APP_VERSION: JSON.stringify(process.env.npm_package_version),
		APP_BUILD_HASH: JSON.stringify(process.env.APP_BUILD_HASH || 'dev-build')
	},
	server: {
		proxy: {
			// Keep development traffic same-origin so local clients and temporary
			// reverse proxies only need to expose the Vite port.
			'/api': {
				target: 'http://localhost:8080',
				changeOrigin: true
			},
			'/ollama': {
				target: 'http://localhost:8080',
				changeOrigin: true
			},
			'/openai': {
				target: 'http://localhost:8080',
				changeOrigin: true
			},
			'/oauth': {
				target: 'http://localhost:8080',
				changeOrigin: true
			},
			'/static': {
				target: 'http://localhost:8080',
				changeOrigin: true
			},
			'/ws': {
				target: 'http://localhost:8080',
				changeOrigin: true,
				ws: true
			}
		}
	},
	build: {
		sourcemap: true
	},
	worker: {
		plugins: () => [researchProfilePlugin()],
		format: 'es'
	},
	esbuild: {
		pure: process.env.ENV === 'dev' ? [] : ['console.log', 'console.debug', 'console.error']
	}
});
