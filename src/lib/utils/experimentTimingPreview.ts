export const waitUntilPreviewDeadline = async (
	deadline: number,
	isActive: () => boolean,
	now: () => number = () => performance.now()
) => {
	while (isActive()) {
		const remaining = deadline - now();
		if (remaining <= 0) return true;
		await new Promise((resolve) => setTimeout(resolve, Math.min(remaining, 50)));
	}
	return false;
};

const previewChunkSize = (seed: number, index: number, minimum: number, maximum: number) => {
	minimum = Math.max(1, minimum);
	maximum = Math.max(minimum, maximum);
	let hash = (seed * 2654435761 + index * 2246822519) >>> 0;
	hash = (hash ^ (hash >>> 16)) >>> 0;
	return minimum + (hash % (maximum - minimum + 1));
};

export const deterministicPreviewChunks = (
	text: string,
	unit: string,
	minimum: number,
	maximum: number,
	seed: number
) => {
	const values =
		unit === 'WORD'
			? (text.match(/\S+\s*/g) ?? [])
			: unit === 'CHUNK'
				? (text.match(/[\s\S]{1,4}/g) ?? [])
				: Array.from(text);
	const chunks: string[] = [];
	let cursor = 0;
	let index = 0;
	while (cursor < values.length) {
		const size = previewChunkSize(seed, index, minimum, maximum);
		chunks.push(values.slice(cursor, cursor + size).join(''));
		cursor += size;
		index += 1;
	}
	if (chunks.length === 1 && Array.from(chunks[0]).length > 1) {
		const values = Array.from(chunks[0]);
		const midpoint = Math.max(1, Math.floor(values.length / 2));
		return [values.slice(0, midpoint).join(''), values.slice(midpoint).join('')];
	}
	return chunks;
};
