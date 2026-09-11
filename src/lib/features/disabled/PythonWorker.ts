/** Defensive boundary: research code must never create an execution worker. */
export default class PythonWorker {
	constructor() {
		throw new Error('Python execution is unavailable in the research frontend.');
	}
}
