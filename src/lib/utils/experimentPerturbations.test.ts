import { describe, expect, it } from 'vitest';
import { boundedActiveElapsed, conditionAllocationIsValid } from './experimentPerturbations';

describe('experiment active-time accounting', () => {
	it('counts only visible focused time and caps a heartbeat', () => {
		expect(boundedActiveElapsed(1000, 6000, true)).toBe(5000);
		expect(boundedActiveElapsed(1000, 6000, false)).toBe(0);
		expect(boundedActiveElapsed(1000, 100000, true)).toBe(30000);
	});
});

describe('experiment condition allocation', () => {
	it('requires one positive control and a total of 100 percent', () => {
		expect(
			conditionAllocationIsValid([
				{ enabled: true, is_control: true, allocation_percent: 20 },
				{ enabled: true, is_control: false, allocation_percent: 80 }
			])
		).toBe(true);
		expect(
			conditionAllocationIsValid([
				{ enabled: true, is_control: true, allocation_percent: 20 },
				{ enabled: true, is_control: false, allocation_percent: 70 }
			])
		).toBe(false);
	});
});
