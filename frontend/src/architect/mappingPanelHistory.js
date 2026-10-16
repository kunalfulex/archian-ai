/** Latest mapping panel in chat for an extraction job_id (newest wins). */
export function findLatestMappingPanelForJob(chatHistory, jobId) {
  if (!jobId) return null;
  const history = chatHistory || [];
  for (let i = history.length - 1; i >= 0; i--) {
    const e = history[i];
    if (
      e.type === 'mapping_panel' &&
      e.metadata?.job_id === jobId &&
      !e.isUnavailable
    ) {
      return e;
    }
  }
  return null;
}

export function nextMappingVersionForJob(chatHistory, jobId) {
  const history = chatHistory || [];
  let max = 0;
  for (const e of history) {
    if (e.type === 'mapping_panel' && e.metadata?.job_id === jobId) {
      max = Math.max(max, Number(e.metadata?.mapping_version) || 1);
    }
  }
  return max + 1;
}
