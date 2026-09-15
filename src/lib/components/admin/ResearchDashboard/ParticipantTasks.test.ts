import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import ParticipantTasks from './ParticipantTasks.svelte';

const renderTasks = (tasks: any[]) =>
	render(ParticipantTasks, {
		props: { tasks, fmtDate: (value) => String(value ?? 'Not available'), onOpenQuestion: () => {} }
	}).body;

describe('participant task history', () => {
	it('shows pre/post survey answers and question results together in task order', () => {
		const body = renderTasks([
			{
				title: 'Pre-survey',
				position: 0,
				task_type: 'SURVEY',
				status: 'FINALIZED',
				survey_submission: {
					status: 'SUBMITTED',
					submitted_at: 10,
					answers: [{ prompt: 'Experience?', value: ['Often', 'At school'] }]
				}
			},
			{
				title: 'Knowledge task',
				position: 1,
				task_type: 'QUESTION',
				status: 'FINALIZED',
				question_submission: {
					submission_id: 'question-submission',
					status: 'SUBMITTED',
					grading_status: 'COMPLETED',
					score: 0,
					maximum_score: 5,
					submitted_at: 20
				}
			},
			{
				title: 'Post-survey',
				position: 2,
				task_type: 'SURVEY',
				status: 'FINALIZED',
				survey_submission: {
					status: 'SUBMITTED',
					submitted_at: 30,
					answers: [
						{ prompt: 'Helpfulness?', value: 4 },
						{ prompt: 'Comments?', value: 'Helpful feedback' },
						{ prompt: 'Anything else?', value: null }
					]
				}
			}
		]);
		expect(body.indexOf('Pre-survey')).toBeLessThan(body.indexOf('Knowledge task'));
		expect(body.indexOf('Knowledge task')).toBeLessThan(body.indexOf('Post-survey'));
		expect(body).toContain('Often, At school');
		expect(body).toContain('Score: 0 / 5');
		expect(body).toContain('View answers and grading');
		expect(body).toContain('Helpfulness?');
		expect(body).toMatch(/>\s*4\s*</);
		expect(body).toContain('Helpful feedback');
		expect(body).toContain('Not answered');
	});

	it('distinguishes skipped surveys, missing submissions, and pending grades', () => {
		const body = renderTasks([
			{
				title: 'Optional survey',
				position: 0,
				task_type: 'SURVEY',
				status: 'SKIPPED',
				survey_submission: {
					status: 'SKIPPED',
					skipped_at: 12,
					answers: [{ prompt: 'Hidden unanswered item', value: null }]
				}
			},
			{ title: 'Future survey', position: 1, task_type: 'SURVEY', status: 'LOCKED' },
			{ title: 'Future questions', position: 2, task_type: 'QUESTION', status: 'LOCKED' },
			{
				title: 'Pending grade',
				position: 3,
				task_type: 'QUESTION',
				status: 'FINALIZED',
				question_submission: {
					submission_id: 'pending',
					status: 'SUBMITTED',
					grading_status: 'PENDING',
					score: null,
					provisional_score: 2,
					maximum_score: 5
				}
			}
		]);
		expect(body).toContain('SKIPPED');
		expect(body).not.toContain('Hidden unanswered item');
		expect(body).toContain('No survey submission yet.');
		expect(body).toContain('No question submission yet.');
		expect(body).toContain('Score: Pending / 5');
		expect(body).toContain('Provisional: 2');
	});

	it('does not add an empty history to legacy sessions', () => {
		expect(renderTasks([])).not.toContain('Experiment tasks and surveys');
	});
});
