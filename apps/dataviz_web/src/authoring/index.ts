export * from './types';
export * from './operations';
export * from './diff';
export * from './versioning';
export { VisualAuthoringEditor, type VisualAuthoringEditorProps } from './VisualAuthoringEditor';
export { default as AuthoringEntry, type AuthoringEntryProps } from './AuthoringEntry';
export { planAgentProposal } from './agent';
export { createAuthoringDocument, createSeedModel, createVisual, AUTHORING_STORAGE_KEY } from './seed';
export { loadDraft, saveDraft } from './storage';
export {
  DEFAULT_CSV_LIMITS,
  LOCAL_DATASET_SCHEMA_VERSION,
  datasetFingerprint,
  parseLocalCsv,
  semanticModelForDataset,
  semanticReplacementOps,
  type CsvLimits,
  type LocalDataset,
  type LocalDatasetColumn,
} from './csv';
export { loadLocalDataset, saveLocalDataset, validateLocalDataset } from './datasetStorage';
export { previewResultsFor, renderPlanForPresentation } from './preview';
export {
  addVisual,
  materializeVisuals,
  previewVisualOperations,
  removeVisual,
  updateVisual,
} from './visualOperations';
export {
  presentationFromIR,
  presentationToIR,
  visualFromIR,
  visualToIR,
} from './presentationBridge';
