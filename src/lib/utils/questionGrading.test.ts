import { describe, expect, it } from 'vitest';

import { completedRetryScore } from './questionGrading';

describe('question grading retry score updates', () => {
	it('returns the new generated score after a successful retry over an override', () => {
		expect(
			completedRetryScore(
				{
					generated_score: 3.75,
					effective_score: 4.5,
					grading_method: 'ADMIN_OVERRIDE',
					attempts: [{ id: 'retry', status: 'GRADED', awarded_score: 3.75 }]
				},
				'retry'
			)
		).toEqual({ terminal: true, score: 3.75 });
	});

	it('does not replace the score while retrying or after a failed retry', () => {
		expect(
			completedRetryScore(
				{ generated_score: 2, attempts: [{ id: 'retry', status: 'RUNNING' }] },
				'retry'
			)
		).toEqual({ terminal: false, score: undefined });
		expect(
			completedRetryScore(
				{ generated_score: 2, attempts: [{ id: 'retry', status: 'FAILED' }] },
				'retry'
			)
		).toEqual({ terminal: true, score: undefined });
	});
});
