import React, { useState } from 'react';
import type { BookmarkSnapshot, ParameterStateMap } from './types';
import type { FilterCondition } from '../../interactions/types';

export class BookmarkStore {
  private bookmarks = new Map<string, BookmarkSnapshot>();

  constructor(initialBookmarks: BookmarkSnapshot[] = []) {
    for (const bm of initialBookmarks) {
      this.bookmarks.set(bm.id, bm);
    }
  }

  public capture(
    name: string,
    targetPageId: string,
    activeFilters: Record<string, FilterCondition> = {},
    activeSelections: Record<string, unknown> = {},
    parameterStates: ParameterStateMap = {},
    description?: string,
  ): BookmarkSnapshot {
    if (!name || !name.trim()) {
      raiseError('Bookmark name cannot be empty');
    }
    const id = `bm_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    const snapshot: BookmarkSnapshot = {
      activeFilters: { ...activeFilters },
      activeSelections: { ...activeSelections },
      createdAt: new Date().toISOString(),
      description,
      id,
      name: name.trim(),
      parameterStates: { ...parameterStates },
      targetPageId,
    };
    this.bookmarks.set(id, snapshot);
    return snapshot;
  }

  public get(id: string): BookmarkSnapshot | undefined {
    return this.bookmarks.get(id);
  }

  public list(): BookmarkSnapshot[] {
    return [...this.bookmarks.values()];
  }

  public remove(id: string): boolean {
    return this.bookmarks.delete(id);
  }

  public clear(): void {
    this.bookmarks.clear();
  }
}

function raiseError(msg: string): never {
  throw new Error(msg);
}

export interface BookmarkManagerProps {
  initialBookmarks?: BookmarkSnapshot[];
  activeBookmarkId?: string | null;
  currentAppState: {
    currentPageId: string;
    activeFilters: Record<string, FilterCondition>;
    activeSelections: Record<string, unknown>;
    parameterStates: ParameterStateMap;
  };
  onApplyBookmark: (snapshot: BookmarkSnapshot) => void;
  onBookmarkListChange?: (bookmarks: BookmarkSnapshot[]) => void;
}

export const BookmarkManager: React.FC<BookmarkManagerProps> = ({
  initialBookmarks = [],
  activeBookmarkId = null,
  currentAppState,
  onApplyBookmark,
  onBookmarkListChange,
}) => {
  const [store] = useState(() => new BookmarkStore(initialBookmarks));
  const [bookmarks, setBookmarks] = useState<BookmarkSnapshot[]>(() => store.list());
  const [newBookmarkName, setNewBookmarkName] = useState('');

  const handleCapture = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newBookmarkName.trim()) {return;}

    const snapshot = store.capture(
      newBookmarkName,
      currentAppState.currentPageId,
      currentAppState.activeFilters,
      currentAppState.activeSelections,
      currentAppState.parameterStates,
    );
    const updated = store.list();
    setBookmarks(updated);
    setNewBookmarkName('');
    onBookmarkListChange?.(updated);
    onApplyBookmark(snapshot);
  };

  const handleRemove = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    store.remove(id);
    const updated = store.list();
    setBookmarks(updated);
    onBookmarkListChange?.(updated);
  };

  return (
    <div
      className="dataviz-bookmark-manager"
      role="region"
      aria-label="Report Bookmark Manager"
      style={{
        backgroundColor: '#FFFFFF',
        border: '1px solid #E2E8F0',
        borderRadius: '8px',
        padding: '12px',
      }}
    >
      <h4 style={{ color: '#1E293B', fontSize: '15px', margin: '0 0 12px 0' }}>
        {`🔖 Bookmarks (${bookmarks.length})`}
      </h4>

      <form onSubmit={handleCapture} style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
        <input
          type="text"
          placeholder="New bookmark name..."
          value={newBookmarkName}
          onChange={(e) => setNewBookmarkName(e.target.value)}
          aria-label="New bookmark name"
          style={{ border: '1px solid #CBD5E1', borderRadius: '4px', flex: 1, padding: '6px 10px' }}
        />
        <button
          type="submit"
          disabled={!newBookmarkName.trim()}
          style={{
            backgroundColor: newBookmarkName.trim() ? '#2563EB' : '#94A3B8',
            border: 'none',
            borderRadius: '4px',
            color: '#FFFFFF',
            cursor: newBookmarkName.trim() ? 'pointer' : 'not-allowed',
            fontWeight: 600,
            padding: '6px 12px',
          }}
        >
          Capture
        </button>
      </form>

      {bookmarks.length === 0 ? (
        <p style={{ color: '#64748B', fontSize: '13px', margin: 0 }}>
          No bookmarks saved yet. Click Capture to save current view.
        </p>
      ) : (
        <ul
          role="listbox"
          aria-label="Saved Bookmarks"
          style={{ display: 'flex', flexDirection: 'column', gap: '6px', listStyle: 'none', margin: 0, padding: 0 }}
        >
          {bookmarks.map((bm) => {
            const isActive = activeBookmarkId === bm.id;
            return (
              <li
                key={bm.id}
                role="option"
                aria-selected={isActive}
                tabIndex={0}
                onClick={() => onApplyBookmark(bm)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onApplyBookmark(bm);
                  }
                }}
                style={{
                  alignItems: 'center',
                  backgroundColor: isActive ? '#EFF6FF' : '#F8FAFC',
                  border: isActive ? '1px solid #3B82F6' : '1px solid #E2E8F0',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  display: 'flex',
                  justifyContent: 'space-between',
                  padding: '8px 12px',
                }}
              >
                <div>
                  <span style={{ color: isActive ? '#1D4ED8' : '#334155', fontSize: '14px', fontWeight: 600 }}>
                    {bm.name}
                  </span>
                  <span style={{ color: '#64748B', display: 'block', fontSize: '11px' }}>
                    {`Page: ${bm.targetPageId} | Filters: ${Object.keys(bm.activeFilters).length}`}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={(e) => handleRemove(bm.id, e)}
                  aria-label={`Delete bookmark ${bm.name}`}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: '#EF4444',
                    cursor: 'pointer',
                    fontSize: '14px',
                  }}
                >
                  ✕
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};
