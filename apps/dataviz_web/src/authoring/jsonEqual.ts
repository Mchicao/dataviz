/** Compare JSON-safe IR values without depending on object key insertion order. */
export function jsonEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) {return true;}
  if (typeof left !== typeof right || left === null || right === null) {return false;}
  if (typeof left !== 'object') {return false;}

  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) {return false;}
    return left.every((value, index) => jsonEqual(value, right[index]));
  }

  const leftRecord = left as Record<string, unknown>;
  const rightRecord = right as Record<string, unknown>;
  const leftKeys = Object.keys(leftRecord).sort();
  const rightKeys = Object.keys(rightRecord).sort();
  if (!jsonEqual(leftKeys, rightKeys)) {return false;}
  return leftKeys.every((key) => jsonEqual(leftRecord[key], rightRecord[key]));
}
