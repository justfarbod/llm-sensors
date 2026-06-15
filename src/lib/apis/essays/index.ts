import { WEBUI_API_BASE_URL } from '$lib/constants';

const request = async (token: string, path: string, options: RequestInit = {}) => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/essays${path}`, {
		...options,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`,
			...(options.headers ?? {})
		}
	});

	const body = await response.json().catch(() => null);
	if (!response.ok) {
		throw body?.detail ?? body?.message ?? `Request failed with status ${response.status}`;
	}

	return body;
};

export const getEssayWorkspace = async (token: string) => request(token, '/workspace');

export const submitEssay = async (token: string, content: string) => {
	return request(token, '/submit', {
		method: 'POST',
		body: JSON.stringify({ content })
	});
};

export const getEssayTopics = async (token: string) => request(token, '/topics');

export const createEssayTopic = async (
	token: string,
	topic: { title: string; question: string }
) => {
	return request(token, '/topics/create', {
		method: 'POST',
		body: JSON.stringify(topic)
	});
};

export const updateEssayTopic = async (
	token: string,
	id: string,
	topic: { title: string; question: string }
) => {
	return request(token, `/topics/${id}/update`, {
		method: 'POST',
		body: JSON.stringify(topic)
	});
};

export const deleteEssayTopic = async (token: string, id: string) => {
	return request(token, `/topics/${id}/delete`, {
		method: 'DELETE'
	});
};
