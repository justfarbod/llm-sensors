export const boundedActiveElapsed = (
	startedAt: number | null,
	now: number,
	isVisibleAndFocused: boolean
) => {
	if (!isVisibleAndFocused || startedAt === null) return 0;
	return Math.round(Math.min(30_000, Math.max(0, now - startedAt)));
};

export const conditionAllocationIsValid = (
	conditions: { enabled: boolean; is_control: boolean; allocation_percent: number }[]
) => {
	const enabled = conditions.filter((condition) => condition.enabled);
	const controls = enabled.filter((condition) => condition.is_control);
	return (
		conditions.every(
			(condition) =>
				Number.isInteger(condition.allocation_percent) &&
				condition.allocation_percent >= 0 &&
				condition.allocation_percent <= 100
		) &&
		controls.length === 1 &&
		enabled.reduce((total, condition) => total + condition.allocation_percent, 0) === 100
	);
};
