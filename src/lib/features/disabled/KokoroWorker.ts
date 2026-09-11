export class KokoroWorker {
	constructor(..._args: unknown[]) {}
	async init(): Promise<void> {
		throw new Error('Speech is unavailable in the research frontend.');
	}
	async generate(_input: { text: string; voice: string }): Promise<string> {
		throw new Error('Speech is unavailable in the research frontend.');
	}
	terminate() {}
}
