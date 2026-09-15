import { writable } from 'svelte/store';
export const browser = true;
export const dev = true;
export const page = writable({params: {tab: location.pathname.split('/').pop()}, url: new URL(location.href)});
export async function goto(url, options = {}) {
 history[options.replaceState ? 'replaceState' : 'pushState']({}, '', url);
 page.set({params: {tab: location.pathname.split('/').pop()}, url: new URL(location.href)});
}
