import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './DataQualitySummary.css';

/**
 * Data quality score, analysis, and fix recommendations (matches extraction chat UX).
 */
export default function DataQualitySummary({ report }) {
  if (!report?.summary) return null;

  const score = report.summary.data_quality_score;
  const hasIssues = report.summary.has_issues;
  const recommendations = report.summary.recommendations || [];
  const summaryText = (report.summary_text || '').trim();

  let scoreClass = 'data-quality-score--good';
  if (score < 70) scoreClass = 'data-quality-score--poor';
  else if (score < 90) scoreClass = 'data-quality-score--fair';

  return (
    <div className="data-quality-summary" role="region" aria-label="Data quality analysis">
      <div className="data-quality-summary-header">
        <span className="data-quality-summary-title">Data quality analysis</span>
        {score != null && (
          <span className={`data-quality-score ${scoreClass}`} title="Data quality score out of 100">
            {Number(score).toFixed(score % 1 === 0 ? 0 : 1)}/100
          </span>
        )}
      </div>
      {summaryText ? (
        <div className="data-quality-summary-body react-markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{summaryText}</ReactMarkdown>
        </div>
      ) : null}
      {!summaryText && recommendations.length > 0 && (
        <ul className="data-quality-recommendations">
          {recommendations.slice(0, 8).map((rec, i) => (
            <li key={i}>{rec}</li>
          ))}
        </ul>
      )}
      {hasIssues === false && score >= 90 && !summaryText && (
        <p className="data-quality-summary-ok">Your data looks good — no major issues detected.</p>
      )}
    </div>
  );
}
