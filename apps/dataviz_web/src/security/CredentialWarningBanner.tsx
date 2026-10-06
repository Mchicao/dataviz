import React from 'react';

import type { CredentialScanReport } from '../runtime/client';

/**
 * Non-blocking warning banner shown when the server detected possible
 * embedded credentials in the uploaded artifact. It never displays the
 * matched secret value — only safe metadata from the scan report.
 *
 * The banner renders above the visual canvas and is purely informational:
 * it does not block rendering of the report.
 */
export const CredentialWarningBanner: React.FC<{
  readonly report: CredentialScanReport;
}> = ({ report }) => {
  if (!report.has_credential_risk) {return null;}

  const highSeverity = report.findings.filter((f) => f.severity === 'high');
  const warningSeverity = report.findings.filter((f) => f.severity !== 'high');

  return (
    <aside
      role="alert"
      className="dv-credential-warning"
      data-finding-count={report.finding_count}
      data-redaction-count={report.redaction_count}
    >
      <div className="dv-credential-warning-header">
        <span className="dv-credential-warning-icon">⚠️</span>
        <strong>Posible credencial embebida detectada</strong>
      </div>
      <p className="dv-credential-warning-body">
        El artefacto cargado contiene {report.finding_count} posible
        {report.finding_count === 1 ? ' credencial' : ' credenciales'} embebida
        {report.finding_count === 1 ? '' : 's'}.
        {report.redaction_count > 0 &&
          ` Se redactaron ${report.redaction_count} valor(es) automáticamente.`}
        {' '}
        <strong>Esta es una mala práctica de seguridad.</strong> Las credenciales
        no deben incluirse dentro de archivos PBIX/PBIP/PBIT. Use un secret
        store, referencias seguras o configure las credenciales fuera del
        artefacto.
      </p>
      {(highSeverity.length > 0 || warningSeverity.length > 0) && (
        <details className="dv-credential-warning-details">
          <summary>
            Ver detalles ({highSeverity.length} alta, {warningSeverity.length} advertencia)
          </summary>
          <ul className="dv-credential-warning-findings">
            {report.findings.map((finding, idx) => (
              <li
                key={`${finding.kind}-${finding.source}-${idx}`}
                className={`dv-credential-finding dv-severity-${finding.severity}`}
              >
                <span className="dv-finding-kind">{finding.kind}</span>
                <span className="dv-finding-location">
                  {finding.source}
                  {finding.line === null ? '' : `:${finding.line}`}
                </span>
                <span className="dv-finding-message">{finding.message}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </aside>
  );
};

export default CredentialWarningBanner;
