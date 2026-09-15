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
	it.each([0, 20, 100])('allows a control allocation of %i percent', (control) => {
		expect(
			conditionAllocationIsValid([
				{ enabled: true, is_control: true, allocation_percent: control },
				{ enabled: true, is_control: false, allocation_percent: 100 - control }
			])
		).toBe(true);
	});

	it('ignores disabled conditions when totaling allocations', () => {
		expect(
			conditionAllocationIsValid([
				{ enabled: true, is_control: true, allocation_percent: 20 },
				{ enabled: true, is_control: false, allocation_percent: 80 },
				{ enabled: false, is_control: false, allocation_percent: 100 }
			])
		).toBe(true);
	});

	it('requires a total of 100 percent', () => {
		expect(
			conditionAllocationIsValid([
				{ enabled: true, is_control: true, allocation_percent: 20 },
				{ enabled: true, is_control: false, allocation_percent: 70 }
			])
		).toBe(false);
	});

	it.each([-1, 101, 0.5, NaN, Infinity, undefined, null])(
		'rejects invalid allocation %s even when the total is 100',
		(allocation) => {
			expect(
				conditionAllocationIsValid([
					{ enabled: true, is_control: true, allocation_percent: allocation as number },
					{ enabled: true, is_control: false, allocation_percent: 100 - (allocation as number) }
				])
			).toBe(false);
		}
	);

	it.each([
		[],
		[{ enabled: true, is_control: false, allocation_percent: 100 }],
		[
			{ enabled: false, is_control: true, allocation_percent: 0 },
			{ enabled: true, is_control: false, allocation_percent: 100 }
		],
		[
			{ enabled: true, is_control: true, allocation_percent: 0 },
			{ enabled: true, is_control: true, allocation_percent: 100 }
		]
	])('requires exactly one enabled control: %j', (...conditions) => {
		expect(conditionAllocationIsValid(conditions)).toBe(false);
	});
});
