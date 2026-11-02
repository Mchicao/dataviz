import { describe, expect, it, vi } from 'vitest';
import { applyModelReply, handleRequest } from '../src/worker';
import type { DemoEnvironment } from '../src/worker';
import { createClinicDemo } from '../src/authoring/clinicDemo';

const limiter = () => ({ limit: vi.fn(async () => ({ success: true })) });
const assets = { fetch: async () => new Response('<html>Studio</html>') };
const requestBody = () => {
  const version = createClinicDemo().document.versions[0];
  return { prompt: 'Cambia el título', model: 'glm-5.3-flash', thinking: 'low', base_version: 1,
    page_id: version.presentation_ir.pages[0].page_id, selected_visual_id: null,
    semantic_model: version.model, presentation: version.presentation_ir };
};
const post = (body: unknown, origin = 'https://demo.example') => new Request('https://demo.example/api/assistant/proposals', {
  method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json', 'CF-Connecting-IP': '192.0.2.1' },
  body: JSON.stringify(body),
});

describe('public assistant boundary', () => {
  it('reports no key and never calls the provider until configured', async () => {
    const provider = vi.fn();
    const env: DemoEnvironment = { ASSETS: assets };
    const config = await handleRequest(new Request('https://demo.example/api/assistant/config'), env, provider);
    expect((await config.json()).configured).toBe(false);
    expect((await handleRequest(post(requestBody()), env, provider)).status).toBe(503);
    expect(provider).not.toHaveBeenCalled();
  });

  it('rejects other origins, oversized bodies, invalid input, and exhausted limits before calling GLM', async () => {
    const provider = vi.fn();
    const env = { ASSETS: assets, ZAI_API_KEY: 'test-only', AI_PER_IP: limiter(), AI_GLOBAL: limiter() };
    expect((await handleRequest(post(requestBody(), 'https://other.example'), env, provider)).status).toBe(403);
    expect((await handleRequest(post({ text: 'x'.repeat(256001) }), env, provider)).status).toBe(413);
    expect((await handleRequest(post({ ...requestBody(), base_version: 0 }), env, provider)).status).toBe(400);
    env.AI_PER_IP.limit.mockResolvedValue({ success: false });
    expect((await handleRequest(post(requestBody()), env, provider)).status).toBe(429);
    expect(provider).not.toHaveBeenCalled();
  });

  it('uses the standard GLM API, returns a validated proposal, and does not leak its key', async () => {
    const body = requestBody();
    const provider = vi.fn(async () => Response.json({ choices: [{ finish_reason: 'stop', message: {
      content: JSON.stringify({ reply: 'Hola, puedo proponer cambios.', presentation_ops: [] }),
    } }] }));
    const response = await handleRequest(post(body), {
      ASSETS: assets, ZAI_API_KEY: 'test-only', AI_PER_IP: limiter(), AI_GLOBAL: limiter(),
    }, provider);
    expect(response.status).toBe(200);
    expect(provider.mock.calls[0][0]).toBe('https://api.z.ai/api/paas/v4/chat/completions');
    const result = await response.json();
    expect(result.presentation).toEqual(body.presentation);
    expect(JSON.stringify(result)).not.toContain('test-only');
    expect(body.presentation).toEqual(requestBody().presentation);
  });

  it('accepts bounded visual updates but rejects unknown bindings, other pages, and incomplete provider output', async () => {
    const body = requestBody();
    const visual = body.presentation.pages[0].visuals[0];
    const op = { kind: 'update', target: 'visual', name: visual.visual_id, page_id: body.page_id,
      payload: { ...visual, title: 'Nuevo título' } };
    const changed = applyModelReply({ reply: 'Propongo cambiar el título.', presentation_ops: [op] }, body.semantic_model, body.presentation, body.page_id);
    expect(changed.presentation.pages[0].visuals[0].title).toBe('Nuevo título');
    expect(changed.presentation.pages[0].visuals[0].properties.editor_data_roles).toEqual({ value: 'measure:Sum_Atendidas' });
    expect(() => applyModelReply({ reply: 'Cambio', presentation_ops: [{ ...op, page_id: 'other' }] }, body.semantic_model, body.presentation, body.page_id)).toThrow();
    expect(() => applyModelReply({ reply: 'Cambio', presentation_ops: [{ ...op, payload: {
      ...visual, bindings: [{ ...visual.bindings[0], field_name: 'Unknown' }],
    } }] }, body.semantic_model, body.presentation, body.page_id)).toThrow();
    const provider = vi.fn(async () => Response.json({ choices: [{ finish_reason: 'length', message: { content: '{}' } }] }));
    expect((await handleRequest(post(body), { ASSETS: assets, ZAI_API_KEY: 'test-only', AI_PER_IP: limiter(), AI_GLOBAL: limiter() }, provider)).status).toBe(502);
  });
});
