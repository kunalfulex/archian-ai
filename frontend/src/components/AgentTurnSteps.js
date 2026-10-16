import React from 'react';

/**
 * Minimal agent step list during SSE turns (agent_step_started / agent_step_completed).
 */
export default function AgentTurnSteps({ steps, title = 'Data operations' }) {
  if (!steps || steps.length === 0) return null;

  return (
    <div
      className="agent-turn-steps"
      style={{
        margin: '12px 0',
        padding: '10px 12px',
        borderRadius: 8,
        backgroundColor: 'rgba(99, 102, 241, 0.06)',
        border: '1px solid rgba(99, 102, 241, 0.2)',
        fontSize: 13,
        textAlign: 'left',
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 8, color: '#374151', textAlign: 'left' }}>{title}</div>
      <ul style={{ margin: 0, paddingLeft: 18, textAlign: 'left' }}>
        {steps.map((s, i) => (
          <li key={`${s.tool_id}-${i}`} style={{ marginBottom: 4 }}>
            <span style={{ marginRight: 6 }}>{s.status === 'done' ? '✓' : '…'}</span>
            <span>{s.detail || s.label || s.tool_id}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
