export const AGENT_MODELS = ['glm-5.3', 'glm-5.3-flash'] as const;
export const THINKING_LEVELS = ['low', 'high', 'max'] as const;
export type AgentModel = 'local' | typeof AGENT_MODELS[number];
export type ThinkingLevel = typeof THINKING_LEVELS[number];
export interface AgentSelection { model: AgentModel; thinking: ThinkingLevel }
export interface AgentConfig { configured: boolean; default_model: AgentModel; default_thinking: ThinkingLevel }

export function parseSelection(value: unknown): AgentSelection | null {
  if (typeof value !== 'object' || value === null || !('model' in value) || !('thinking' in value)) {return null;}
  const model = (['local', ...AGENT_MODELS] as const).find((model) => model === value.model);
  const thinking = THINKING_LEVELS.find((level) => level === value.thinking);
  return model && thinking ? { model, thinking } : null;
}

export async function loadAgentConfig(endpoint: string, signal: AbortSignal): Promise<AgentConfig> {
  const response = await fetch(`${endpoint}/config`, { signal });
  if (!response.ok) {throw new Error('El servicio de IA no está disponible. Inicia el servidor del asistente.');}
  const body: unknown = await response.json();
  if (typeof body !== 'object' || body === null || !('default_model' in body) || !('default_thinking' in body)
    || !('configured' in body) || typeof body.configured !== 'boolean') {throw new Error('La configuración del asistente no es válida.');}
  const defaults = parseSelection({ model: body.default_model, thinking: body.default_thinking });
  if (!defaults) {throw new Error('La selección del asistente no es válida.');}
  return { configured: body.configured, default_model: defaults.model, default_thinking: defaults.thinking };
}
