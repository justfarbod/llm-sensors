export const completedRetryScore = (response: any, attemptId: string) => {
	const attempt = (response?.attempts ?? []).find((item: any) => item.id === attemptId);
	if (!attempt || ['PENDING', 'RUNNING'].includes(attempt.status))
		return { terminal: false, score: undefined };
	return {
		terminal: true,
		score:
			attempt.status === 'GRADED' && response.generated_score != null
				? Number(response.generated_score)
				: undefined
	};
};
