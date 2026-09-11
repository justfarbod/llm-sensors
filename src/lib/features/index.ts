import {
	profileFeatures,
	resolveProfile,
	constrainChatRequest as constrain,
	rejectPythonExecution as rejectPython
} from './definition.js';

declare const __FRONTEND_PROFILE__: string;
export const frontendProfile = resolveProfile(__FRONTEND_PROFILE__);
export const features = profileFeatures(frontendProfile);
export const unsupportedExecution = () => ({
	stdout: null,
	stderr: 'This execution feature is unavailable in the research frontend.',
	result: null
});

export const constrainChatRequest = (request: Record<string, any>) =>
	constrain(frontendProfile, request);
export const rejectPythonExecution = (callback?: (response: object) => void) =>
	rejectPython(frontendProfile, callback);
