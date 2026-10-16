/**
 * Status text for the chat loader row — animated dots are appended in the UI,
 * so strip trailing ellipsis from backend/SSE messages to avoid "… ...".
 */
export function stripTrailingEllipsis(text) {
  if (text == null || text === '') return '';
  return String(text)
    .replace(/(\s*\.{1,3}\s*|\s*…\s*)+$/gu, '')
    .trimEnd();
}
