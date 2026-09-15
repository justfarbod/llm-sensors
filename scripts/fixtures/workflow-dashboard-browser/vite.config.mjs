import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('./', import.meta.url));
export default defineConfig({root,plugins:[svelte({configFile:false,compilerOptions:{dev:true}})],
 resolve:{alias:{'$lib':fileURLToPath(new URL('../../../src/lib',import.meta.url)), '$app/stores':root+'app.ts','$app/navigation':root+'app.ts','$app/environment':root+'app.ts'}},
 define:{APP_VERSION:JSON.stringify('test'),APP_BUILD_HASH:JSON.stringify('test')},
 server:{host:'127.0.0.1',port:5188,fs:{allow:[fileURLToPath(new URL('../../../',import.meta.url))]}}});
