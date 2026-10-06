/** Coordenada aproximada y estable para geocodificación local de regiones conocidas. */
export interface ApproximateCoordinate {
  latitude: number;
  longitude: number;
}

// Centroides aproximados: suficientes para ubicar marcas de estado sin una API
// externa, sin afirmar precisión de dirección ni reemplazar un geocodificador.
const US_STATE_CENTROIDS: Readonly<Record<string, readonly [number, number]>> = {
  alabama: [32.8, -86.8], alaska: [64, -152], arizona: [34.3, -111.7],
  arkansas: [35, -92.4], california: [37.2, -119.7], colorado: [39, -105.5],
  connecticut: [41.6, -72.7], delaware: [39, -75.5], 'district of columbia': [38.9, -77],
  florida: [28.6, -82.4], georgia: [32.7, -83.3], hawaii: [20.8, -157.5],
  idaho: [44.2, -114.5], illinois: [40, -89.2], indiana: [39.9, -86.3],
  iowa: [42.1, -93.5], kansas: [38.5, -98.4], kentucky: [37.5, -85.3],
  louisiana: [31, -92], maine: [45.2, -69], maryland: [39, -76.7],
  massachusetts: [42.3, -71.8], michigan: [44.3, -85.6], minnesota: [46, -94.3],
  mississippi: [32.7, -89.7], missouri: [38.4, -92.5], montana: [47, -109.6],
  nebraska: [41.5, -99.8], nevada: [39.3, -116.6], 'new hampshire': [43.7, -71.6],
  'new jersey': [40.1, -74.5], 'new mexico': [34.4, -106.1], 'new york': [42.9, -75.5],
  'north carolina': [35.5, -79.4], 'north dakota': [47.5, -100.5], ohio: [40.3, -82.8],
  oklahoma: [35.6, -97.5], oregon: [44, -120.6], pennsylvania: [40.9, -77.8],
  'rhode island': [41.7, -71.5], 'south carolina': [33.8, -80.9], 'south dakota': [44.4, -100.2],
  tennessee: [35.8, -86.4], texas: [31.5, -99.3], utah: [39.3, -111.7],
  vermont: [44.1, -72.7], virginia: [37.5, -78.8], washington: [47.4, -120.7],
  'west virginia': [38.6, -80.6], wisconsin: [44.6, -89.6], wyoming: [43, -107.6],
};

/** Resuelve sólo regiones del diccionario local; una ubicación desconocida queda explícitamente sin coordenadas. */
export function lookupApproximateCoordinate(location: string): ApproximateCoordinate | null {
  const normalized = location.trim().toLocaleLowerCase('en-US').replaceAll(/\s+/g, ' ');
  const coordinate = US_STATE_CENTROIDS[normalized];
  return coordinate ? { latitude: coordinate[0], longitude: coordinate[1] } : null;
}
