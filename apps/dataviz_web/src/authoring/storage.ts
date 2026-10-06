import type { Document } from './types';
import { upgradeDocumentContracts, validateDocument } from './versioning';

export function loadDraft(storage: Storage, key: string): Document | null {
  let raw: string | null;
  try {
    raw = storage.getItem(key);
  } catch {
    return null;
  }
  if (!raw) {return null;}
  try {
    const parsed = JSON.parse(raw) as Document;
    const document = upgradeDocumentContracts(parsed);
    validateDocument(document);
    return document;
  } catch {
    try {
      storage.setItem(`${key}.corrupt`, raw);
    } catch {
      // Storage may be blocked or full; loading must still fall back safely.
    }
    return null;
  }
}

export function saveDraft(storage: Storage, key: string, document: Document): void {
  validateDocument(document);
  storage.setItem(key, JSON.stringify(document));
}
