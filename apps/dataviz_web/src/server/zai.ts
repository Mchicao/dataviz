import { readBounded } from './http';

const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);

/** Un único transporte acotado para propuestas de autoría y explicaciones de especialistas. */
export async function requestZaiJson(apiKey: string, payload: Record<string, unknown>, providerFetch: typeof fetch = fetch): Promise<unknown> {
  const response = await providerFetch('https://api.z.ai/api/paas/v4/chat/completions', {
    method: 'POST', signal: AbortSignal.timeout(45000),
    headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, response_format: { type: 'json_object' }, stream: false }),
  });
  if (!response.ok) {await response.body?.cancel(); throw new Error('Provider unavailable');}
  const result: unknown = JSON.parse(await readBounded(response, 128000));
  if (!record(result) || !Array.isArray(result.choices) || !record(result.choices[0])) {throw new Error('Invalid response');}
  const choice = result.choices[0];
  if (choice.finish_reason !== 'stop' || !record(choice.message) || typeof choice.message.content !== 'string') {throw new Error('Incomplete response');}
  return JSON.parse(choice.message.content);
}
