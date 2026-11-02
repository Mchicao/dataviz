export const json = (body: unknown, status = 200) => Response.json(body, {
  status, headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' },
});

export async function readBounded(request: Request | Response, maxBytes: number): Promise<string> {
  const reader = request.body?.getReader();
  if (!reader) {return '';}
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {break;}
      size += value.byteLength;
      if (size > maxBytes) { await reader.cancel(); throw new RangeError('Body too large'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {result.set(chunk, offset); offset += chunk.length;}
  return new TextDecoder('utf-8', { fatal: true }).decode(result);
}
