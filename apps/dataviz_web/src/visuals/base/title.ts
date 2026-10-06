/**
 * Título legible para visuales.
 *
 * Los planes derivados de Tableau a veces llegan con la expresión cruda sin
 * resolver como título, por ejemplo `< [Sample - Superstore].[none:Category:nk] >`.
 * `displayTitle` resuelve cada expresión `[Fuente].[tipo:Campo:calificador]` al
 * nombre del campo y limpia los envoltorios técnicos; si el texto ya es legible
 * se devuelve tal cual (recortado).
 */

/** Expresión calificada de Tableau: `[Fuente].[segmento(:segmento)*]`. */
const QUALIFIED_EXPR = /\[([^\][]+)\]\.\[([^\][]+)\]/g;
/** Campo Tableau sin datasource explícito, por ejemplo `[yr:Order Date:ok]`. */
const FIELD_EXPR = /\[([^\][]+)\]/g;

/** Envoltorio técnico tipo `< ... >` alrededor de todo el título. */
const ANGLE_WRAPPED = /^<(.*)>$/s;
const TECHNICAL_TITLE_ONLY = /^\s*<\s*\[[^\]]+\]\.\[[^\]]+\]\s*>\s*$/s;
const BARE_FIELD = /^(none|yr|mn|wk|dy|sum|avg|min|max|attr|count|cntd):(.+):(nk|ok|qk)$/i;

/** Separa sufijos técnicos internos heredados del nombre del sheet. */
function lastMeaningfulSegment(title: string): string {
  const parts = title.split('::').map((part) => part.trim()).filter(Boolean);
  const meaningful = [...parts].reverse().find((part) => !/^\d+$/.test(part));
  return meaningful ?? title;
}

/** Reduce `[none:Ship Mode:nk]` al campo legible `Ship Mode`. */
function fieldNameFromSegments(segments: string): string {
  const parts = segments.split(':').map((part) => part.trim()).filter(Boolean);
  // Convención Tableau: [tipo:campo:calificador]. Los extremos describen la
  // agregación/tipo y el dominio, no forman parte del nombre presentado.
  if (parts.length >= 3) {return parts.slice(1, -1).join(':');}
  return parts.length === 2 ? parts[1] : parts[0] ?? '';
}

/**
 * Deriva un título humano a partir del título crudo del plan.
 *
 * @example
 * displayTitle('< [Sample - Superstore].[none:Category:nk] >') // 'Category'
 * displayTitle('Sales by region') // 'Sales by region'
 */
export function displayTitle(raw: string, fallbackName = ''): string {
  const rawText = String(raw ?? '').trim();
  const source = fallbackName.includes('::') && TECHNICAL_TITLE_ONLY.test(rawText)
    ? lastMeaningfulSegment(fallbackName)
    : rawText;
  const base = lastMeaningfulSegment(source);
  if (!base) {return '';}

  const unwrapped = base.replace(ANGLE_WRAPPED, (_, inner: string) => inner.trim());
  const bareField = unwrapped.match(BARE_FIELD);
  if (bareField) {return bareField[2].trim();}
  const resolved = unwrapped.replace(
    QUALIFIED_EXPR,
    (_match, _source: string, segments: string) => 
      fieldNameFromSegments(segments)
    ,
  );

  const cleaned = resolved
    .replace(FIELD_EXPR, (_match, segments: string) => fieldNameFromSegments(segments))
    .replaceAll(/[<>]/g, ' ')
    .replaceAll(/\s+/g, ' ')
    .replaceAll(/^[\s>+,;/|-]+|[\s<+,;/|-]+$/g, '')
    .trim();
  // Si la limpieza agotó un título anómalo, conservamos el original.
  return cleaned || base;
}

/** Etiquetas amables para los shelves neutrales conocidos. */
const ROLE_LABELS: Readonly<Record<string, string>> = {
  filter_target: 'Filter',
  x_axis: 'Category',
  x_axis_2: 'Breakdown',
  y_axis: 'Value',
  y_axis_2: 'Series',
  y_axis_3: 'Detail',
};

/**
 * Nombre presentable para un rol/shelf: mapeo canónico primero y
 * humanizado genérico (`snake_case` -> `Snake case`) como alternativa.
 */
export function friendlyRoleName(roleName: string): string {
  const mapped = ROLE_LABELS[roleName];
  if (mapped) {return mapped;}
  return roleName
    .replaceAll(/[_-]+/g, ' ')
    .replaceAll(/\s+/g, ' ')
    .trim()
    .replaceAll(/\b\w/g, (ch) => ch.toUpperCase());
}
