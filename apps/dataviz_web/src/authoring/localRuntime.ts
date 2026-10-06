import type { RuntimePayload } from '../runtime/client';
import { RENDER_PLAN_SCHEMA_VERSION } from '../runtime/types';
import type { LocalDataset } from './csv';
import { datasetFingerprint } from './csv';
import { presentationFromIR } from './presentationBridge';
import { previewResultsFor, renderPlanForPresentation } from './preview';
import type { Document, Version } from './types';

function versionByNumber(document: Document, number?: number): Version {
  if (number === undefined) {return document.versions[document.versions.length - 1];}
  const version = document.versions.find((candidate) => candidate.number === number);
  if (!version) {throw new Error(`versión ${number} inexistente en el documento local "${document.doc_id}"`);}
  return version;
}

export function compileLocalRuntimePayload(
  document: Document,
  versionNumber?: number,
  dataset: LocalDataset | null = null,
): RuntimePayload {
  const version = versionByNumber(document, versionNumber);
  const presentation = presentationFromIR(version.presentation_ir);
  const usableDataset = dataset
    && version.dataset_fingerprint
    && datasetFingerprint(dataset) === version.dataset_fingerprint
    ? dataset
    : null;
  const authoredPlan = renderPlanForPresentation(presentation);
  const plan: RuntimePayload['plan'] = {
    ...authoredPlan,
    metadata: {
      ...authoredPlan.metadata,
      authoring_status: version.status,
      authoring_version: version.number,
      interaction_ir: version.interaction_ir,
      local_authoring: true,
      source: { artifact_name: document.doc_id },
    },
    schema_version: RENDER_PLAN_SCHEMA_VERSION,
  };
  return {
    plan,
    results: previewResultsFor(presentation.visuals, usableDataset, version.model),
  };
}
