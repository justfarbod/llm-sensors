import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import SurveyMilestones from './SurveyMilestones.svelte';

const show = (progress: any) =>
	render(SurveyMilestones, {
		props: { progress, fmtDate: (value) => `time ${value}` }
	}).body.replace(/\s+/g, ' ');

describe('survey completion milestones', () => {
	it('shows completion and saved submission times without legacy survey answers', () => {
		const body = show({
			pre_survey: null,
			post_survey: null,
			pre_survey_completed: true,
			pre_survey_submitted_at: 123,
			post_survey_completed: true,
			post_survey_submitted_at: 456
		});
		expect(body).toContain('Pre-survey: Completed');
		expect(body).toContain('time 123');
		expect(body).toContain('Post-survey: Completed');
		expect(body).toContain('time 456');
	});
	it('distinguishes a skipped survey from an unfinished survey', () => {
		const body = show({
			pre_survey_started_at: 1,
			pre_survey_completed: false,
			post_survey_completed: false,
			post_survey_skipped_at: 456
		});
		expect(body).toContain('Pre-survey: In progress');
		expect(body).toContain('Post-survey: Skipped');
		expect(body).toContain('time 456');
		expect(body).not.toContain('Completed');
	});
});
