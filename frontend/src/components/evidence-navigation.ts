/** Native frame IDs are not page offsets, and missing frames must never be substituted. */
export interface EvidenceFocus { frame: number; token: number }
export async function locateFrame<T extends { frame: number }>(frame: number,
  read: (offset: number) => Promise<{ total: number; items: T[] }>, signal: AbortSignal): Promise<number | null> {
  if (signal.aborted) return null;
  const first = await read(0);
  if (signal.aborted) return null;
  if (first.items[0]?.frame === frame) return 0;
  // Most recordings have contiguous native IDs. Verify this cheap candidate;
  // sparse recordings still use exact binary search below.
  const candidate = frame - (first.items[0]?.frame ?? frame);
  if (Number.isSafeInteger(candidate) && candidate > 0 && candidate < first.total) {
    const page = await read(candidate);
    if (signal.aborted) return null;
    if (page.items[0]?.frame === frame) return candidate;
  }
  let low = 0, high = first.total - 1;
  while (low <= high) {
    if (signal.aborted) return null;
    const middle = Math.floor((low + high) / 2);
    const item = (middle === 0 ? first : await read(middle)).items[0];
    if (signal.aborted) return null;
    if (!item) return null;
    if (item.frame === frame) return middle;
    if (item.frame < frame) low = middle + 1; else high = middle - 1;
  }
  return null;
}
