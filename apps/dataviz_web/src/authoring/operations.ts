import type {
  Entity,
  Expression,
  Filter,
  Metric,
  Operation,
  OpKind,
  Parameter,
  Relationship,
  RLSIntent,
  SemanticModel,
  TargetKind,
} from './types';

export const VALID_OP_KINDS: readonly OpKind[] = ['add', 'update', 'remove'];
export const VALID_TARGET_KINDS: readonly TargetKind[] = [
  'entity',
  'metric',
  'parameter',
  'filter',
  'relationship',
  'rls_intent',
];

/** Validate a single operation structurally. */
export function validateOperation(op: Operation): void {
  if (!VALID_OP_KINDS.includes(op.kind)) {
    throw new Error(`Invalid op kind: ${String(op.kind)}`);
  }
  if (!VALID_TARGET_KINDS.includes(op.target)) {
    throw new Error(`Invalid op target: ${String(op.target)}`);
  }
  if (!op.name || typeof op.name !== 'string') {
    throw new Error('op.name must be a non-empty string');
  }
  if (op.kind === 'remove') {
    if (op.payload !== undefined && op.payload !== null) {
      throw new Error('remove ops must not carry a payload');
    }
  } else {
    if (!op.payload || typeof op.payload !== 'object') {
      throw new Error(`${op.kind} ops require a payload object`);
    }
    const payloadName = (op.payload as { name?: string }).name;
    if (payloadName !== op.name) {
      throw new Error(
        `payload.name "${String(payloadName)}" must equal op.name "${op.name}"`,
      );
    }
  }
}

// --- Typed Operation Factories ---

function makeOp(kind: OpKind, target: TargetKind, name: string, payload?: unknown): Operation {
  const op: Operation = payload === undefined
    ? { kind, name, target }
    : { kind, name, payload, target };
  validateOperation(op);
  return op;
}

export const addEntity = (entity: Entity): Operation => makeOp('add', 'entity', entity.name, entity);
export const removeEntity = (name: string): Operation => makeOp('remove', 'entity', name);
export const addMetric = (metric: Metric): Operation => makeOp('add', 'metric', metric.name, metric);
export const updateMetric = (metric: Metric): Operation => makeOp('update', 'metric', metric.name, metric);
export const removeMetric = (name: string): Operation => makeOp('remove', 'metric', name);
export const addFilter = (filter: Filter): Operation => makeOp('add', 'filter', filter.name, filter);
export const updateFilter = (filter: Filter): Operation => makeOp('update', 'filter', filter.name, filter);
export const removeFilter = (name: string): Operation => makeOp('remove', 'filter', name);
export const addParameter = (parameter: Parameter): Operation => makeOp('add', 'parameter', parameter.name, parameter);
export const removeParameter = (name: string): Operation => makeOp('remove', 'parameter', name);
export const removeRelationship = (name: string): Operation => makeOp('remove', 'relationship', name);
export const removeRLSIntent = (name: string): Operation => makeOp('remove', 'rls_intent', name);

function opaqueDaxBody(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') {return null;}
  const {expression} = (payload as { expression?: Expression });
  return expression?.kind === 'opaque'
    && expression.name === 'dax'
    && typeof expression.value === 'string'
    ? expression.value
    : null;
}

function guardOpaqueAuthoring(op: Operation, section: Map<string, unknown>): void {
  if (op.target !== 'metric' || op.kind === 'remove') {return;}
  const proposed = opaqueDaxBody(op.payload);
  if (proposed === null) {return;}
  if (op.kind === 'add') {throw new Error('authoring cannot introduce opaque DAX metrics');}
  if (opaqueDaxBody(section.get(op.name)) !== proposed) {
    throw new Error('authoring cannot introduce or modify opaque DAX bodies');
  }
}

/**
 * Apply operations to a model snapshot, returning a new updated SemanticModel.
 * Atomic: raises Error if any operation or structural/referential check fails.
 */
export function materialize(model: SemanticModel, ops: Operation[]): SemanticModel {
  const working: Record<TargetKind, Map<string, unknown>> = {
    entity: indexUnique(model.entities, 'entity'),
    filter: indexUnique(model.filters, 'filter'),
    metric: indexUnique(model.metrics, 'metric'),
    parameter: indexUnique(model.parameters, 'parameter'),
    relationship: indexUnique(model.relationships, 'relationship'),
    rls_intent: indexUnique(model.rls_intents, 'rls_intent'),
  };

  for (const op of ops) {
    validateOperation(op);
    const section = working[op.target];
    if (op.kind === 'add') {
      if (section.has(op.name)) {
        throw new Error(`cannot add ${op.target} "${op.name}": already exists`);
      }
      guardOpaqueAuthoring(op, section);
      section.set(op.name, op.payload);
    } else if (op.kind === 'update') {
      if (!section.has(op.name)) {
        throw new Error(`cannot update ${op.target} "${op.name}": not found`);
      }
      guardOpaqueAuthoring(op, section);
      section.set(op.name, op.payload);
    } else if (op.kind === 'remove') {
      if (!section.has(op.name)) {
        throw new Error(`cannot remove ${op.target} "${op.name}": not found`);
      }
      section.delete(op.name);
    }
  }

  const entities = [...working.entity.values()] as Entity[];
  const metrics = [...working.metric.values()] as Metric[];
  const parameters = [...working.parameter.values()] as Parameter[];
  const filters = [...working.filter.values()] as Filter[];
  const relationships = [...working.relationship.values()] as Relationship[];
  const rls_intents = [...working.rls_intent.values()] as RLSIntent[];

  // Referential integrity for executable semantic references.
  const entitySet = new Set(entities.map((e) => e.name));
  const fieldsByEntity = new Map(entities.map((entity) => [
    entity.name,
    new Set(entity.fields.map((field) => field.name)),
  ]));
  const metricSet = new Set(metrics.map((metric) => metric.name));
  const parameterSet = new Set(parameters.map((parameter) => parameter.name));
  for (const rel of relationships) {
    if (!entitySet.has(rel.from_entity)) {
      throw new Error(`relationship "${rel.name}": unknown from_entity "${rel.from_entity}"`);
    }
    if (!entitySet.has(rel.to_entity)) {
      throw new Error(`relationship "${rel.name}": unknown to_entity "${rel.to_entity}"`);
    }
    assertRelationshipFields(rel.name, 'from', rel.from_entity, rel.from_fields, fieldsByEntity);
    assertRelationshipFields(rel.name, 'to', rel.to_entity, rel.to_fields, fieldsByEntity);
  }
  for (const entity of entities) {
    for (const field of entity.fields) {
      if (field.expression) {
        assertOpaqueScope(field.expression, `field "${entity.name}.${field.name}"`);
        validateExpression(field.expression, {
          fieldsByEntity,
          metricSet,
          owner: `field "${entity.name}.${field.name}"`,
          parameterSet,
        });
      }
    }
  }
  for (const metric of metrics) {
    assertOpaqueScope(metric.expression, `metric "${metric.name}"`, true);
    validateExpression(metric.expression, {
      fieldsByEntity,
      metricSet,
      owner: `metric "${metric.name}"`,
      ownerMetric: metric.name,
      parameterSet,
    });
  }
  for (const parameter of parameters) {
    if (parameter.expression) {
      assertOpaqueScope(parameter.expression, `parameter "${parameter.name}"`);
      validateExpression(parameter.expression, {
        fieldsByEntity,
        metricSet,
        owner: `parameter "${parameter.name}"`,
        parameterSet,
      });
    }
  }
  for (const filter of filters) {
    assertOpaqueScope(filter.target, `filter "${filter.name}"`);
    validateExpression(filter.target, {
      fieldsByEntity,
      metricSet,
      owner: `filter "${filter.name}"`,
      parameterSet,
    });
  }
  for (const rls of rls_intents) {
    if (!entitySet.has(rls.entity)) {
      throw new Error(`rls_intent "${rls.name}": unknown entity "${rls.entity}"`);
    }
    assertOpaqueScope(rls.expression, `rls_intent "${rls.name}"`);
    validateExpression(rls.expression, {
      fieldsByEntity,
      metricSet,
      owner: `rls_intent "${rls.name}"`,
      parameterSet,
    });
  }

  return {
    description: model.description,
    entities,
    filters,
    metrics,
    name: model.name,
    parameters,
    relationships,
    rls_intents,
    schema_version: model.schema_version,
  };
}

function indexUnique<T extends { name: string }>(items: T[], target: TargetKind): Map<string, unknown> {
  const indexed = new Map<string, unknown>();
  for (const item of items) {
    if (indexed.has(item.name)) {throw new Error(`duplicate ${target} name "${item.name}"`);}
    indexed.set(item.name, item);
  }
  return indexed;
}

function countOpaque(expression: Expression): number {
  return (expression.kind === 'opaque' ? 1 : 0)
    + (expression.children ?? []).reduce((count, child) => count + countOpaque(child), 0);
}

function assertOpaqueScope(expression: Expression, owner: string, allowMetricRoot = false): void {
  const opaqueCount = countOpaque(expression);
  if (opaqueCount === 0) {return;}
  if (allowMetricRoot && expression.kind === 'opaque' && opaqueCount === 1) {return;}
  throw new Error(`${owner}: opaque DAX is only allowed as a whole metric expression`);
}

interface ExpressionContext {
  fieldsByEntity: Map<string, Set<string>>;
  metricSet: Set<string>;
  parameterSet: Set<string>;
  owner: string;
  ownerMetric?: string;
}

function validateExpression(expression: Expression, context: ExpressionContext): void {
  if (expression.kind === 'field_ref') {
    assertFieldReference(expression, context);
  } else if (expression.kind === 'measure_ref') {
    if (!expression.name || !context.metricSet.has(expression.name)) {
      throw new Error(`${context.owner}: unknown measure_ref "${String(expression.name)}"`);
    }
    if (expression.name === context.ownerMetric) {
      throw new Error(`${context.owner}: direct self reference is not allowed`);
    }
  } else if (expression.kind === 'parameter_ref') {
    if (!expression.name || !context.parameterSet.has(expression.name)) {
      throw new Error(`${context.owner}: unknown parameter_ref "${String(expression.name)}"`);
    }
  }
  expression.children?.forEach((child) => validateExpression(child, context));
}

function assertFieldReference(expression: Expression, context: ExpressionContext): void {
  const {name} = expression;
  if (!name) {throw new Error(`${context.owner}: field_ref name is required`);}
  if (expression.entity) {
    const fields = context.fieldsByEntity.get(expression.entity);
    if (!fields) {throw new Error(`${context.owner}: unknown field_ref entity "${expression.entity}"`);}
    if (!fields.has(name)) {
      throw new Error(`${context.owner}: unknown field_ref "${expression.entity}.${name}"`);
    }
    return;
  }
  const matches = [...context.fieldsByEntity.values()].filter((fields) => fields.has(name));
  if (matches.length !== 1) {
    const reason = matches.length === 0 ? 'unknown' : 'ambiguous';
    throw new Error(`${context.owner}: ${reason} field_ref "${name}"`);
  }
}

function assertRelationshipFields(
  relationshipName: string,
  side: 'from' | 'to',
  entity: string,
  fields: string[],
  fieldsByEntity: Map<string, Set<string>>,
): void {
  const known = fieldsByEntity.get(entity);
  for (const field of fields) {
    if (!known?.has(field)) {
      throw new Error(`relationship "${relationshipName}": unknown ${side}_field "${entity}.${field}"`);
    }
  }
}
