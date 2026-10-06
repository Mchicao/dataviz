import React from 'react';
import type { InterpretedRole, InterpretedVisual, Row, Scalar } from '../../runtime/types';
import type { MapCapabilityReport, MapCapabilityStatus, MapDataPoint } from './types';
import { displayTitle } from '../base/title';
import { columnForRef } from '../base/data';
import { lookupApproximateCoordinate } from './geography';

export interface MapCapabilityOptions {
  tileServerConfigured?: boolean;
}

export function detectMapCapability(
  visual: InterpretedVisual,
  options: MapCapabilityOptions = {},
): MapCapabilityReport {
  const tileServerConfigured = options.tileServerConfigured ?? true;

  const entries = Object.entries(visual.roles);
  const latitudeRole = entries.find(([name, role]) => isLatitudeRole(name, role));
  const longitudeRole = entries.find(([name, role]) => isLongitudeRole(name, role));
  const locationRole = [...entries].sort((left, right) => locationRoleScore(right) - locationRoleScore(left))
    .find((entry) => locationRoleScore(entry) > 0);
  const valueRole = entries.find(([name, role]) => (
    role.ref.startsWith('measure:') || ['value', 'size', 'color'].includes(name)
  ));
  const rowRole = entries.find(([, role]) => Array.isArray(role.data)
    && role.data.some((value) => typeof value === 'object' && value !== null && !Array.isArray(value)));

  const points = rowRole
    ? pointsFromRows(rowRole[1].data as Row[])
    : pointsFromColumns(latitudeRole?.[1], longitudeRole?.[1], locationRole?.[1], valueRole?.[1]);
  const validCoordCount = points.filter((point) => point.latitude !== null && point.longitude !== null).length;
  const hasLatRole = Boolean(latitudeRole);
  const hasLngRole = Boolean(longitudeRole);
  const hasLocationRole = Boolean(locationRole);

  const hasCoords = (hasLatRole && hasLngRole) || validCoordCount > 0;
  const hasLoc = hasLocationRole || points.length > 0;

  let status: MapCapabilityStatus = 'ready';
  let message = 'Geocoded coordinates are available for a deterministic geographic plot.';

  if (!tileServerConfigured) {
    status = 'placeholder_unconfigured_tiles';
    message = 'Map tile server is unconfigured or offline. Rendering fallback map placeholder.';
  } else if (!hasCoords) {
    status = 'placeholder_missing_coords';
    message = 'Geocoded coordinates (latitude/longitude) are missing or incomplete. Rendering fallback map placeholder.';
  }

  return {
    hasLatitudeLongitude: hasCoords,
    hasLocationName: hasLoc,
    hasTileServer: tileServerConfigured,
    message,
    pointCount: points.length,
    points,
    status,
    validPointCount: validCoordCount,
  };
}

function isLatitudeRole(name: string, role: InterpretedRole): boolean {
  const ref = role.ref.toLowerCase();
  return name.toLowerCase() === 'latitude' || /(^|[.:_])lat(itude)?($|[.:_])/.test(ref);
}

function isLongitudeRole(name: string, role: InterpretedRole): boolean {
  const ref = role.ref.toLowerCase();
  return name.toLowerCase() === 'longitude' || /(^|[.:_])(lng|lon|longitude)($|[.:_])/.test(ref);
}

function locationRoleScore([name, role]: [string, InterpretedRole]): number {
  const key = name.toLowerCase();
  const ref = role.ref.toLowerCase();
  if (key === 'location') {return 100;}
  if (ref.includes('state') || ref.includes('province')) {return 90;}
  if (ref.includes('city')) {return 85;}
  if (key === 'label') {return 75;}
  if (key === 'detail') {return 65;}
  if (ref.includes('country') || ref.includes('region')) {return 55;}
  return 0;
}

function numericCoordinate(value: Scalar): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {return value;}
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {return Number(value);}
  return null;
}

function withCoordinateFallback(
  locationName: string,
  latitude: number | null,
  longitude: number | null,
): Pick<MapDataPoint, 'latitude' | 'longitude'> {
  if (latitude !== null && longitude !== null) {return { latitude, longitude };}
  const approximate = lookupApproximateCoordinate(locationName);
  return approximate ?? { latitude, longitude };
}

function pointsFromRows(rows: Row[]): MapDataPoint[] {
  return rows.map((row) => {
    const locationName = String(
      row.location ?? row.city ?? row.state ?? row.province ?? row.country ?? row.name ?? 'Unknown Location',
    );
    const latitude = numericCoordinate(row.latitude ?? row.lat ?? row.lat_val ?? null);
    const longitude = numericCoordinate(row.longitude ?? row.lng ?? row.lon ?? row.lng_val ?? null);
    return {
      ...withCoordinateFallback(locationName, latitude, longitude),
      locationName,
      value: (row.value as number | string | null) ?? null,
    };
  });
}

function pointsFromColumns(
  latitudeRole: InterpretedRole | undefined,
  longitudeRole: InterpretedRole | undefined,
  locationRole: InterpretedRole | undefined,
  valueRole: InterpretedRole | undefined,
): MapDataPoint[] {
  const latitudes = latitudeRole ? columnForRef(latitudeRole.ref, latitudeRole.data) : [];
  const longitudes = longitudeRole ? columnForRef(longitudeRole.ref, longitudeRole.data) : [];
  const locations = locationRole ? columnForRef(locationRole.ref, locationRole.data) : [];
  const values = valueRole ? columnForRef(valueRole.ref, valueRole.data) : [];
  const count = Math.max(latitudes.length, longitudes.length, locations.length, values.length);
  return Array.from({ length: count }, (_, index) => {
    const locationName = String(locations[index] ?? `Location ${index + 1}`);
    const latitude = numericCoordinate(latitudes[index] ?? null);
    const longitude = numericCoordinate(longitudes[index] ?? null);
    return {
      ...withCoordinateFallback(locationName, latitude, longitude),
      locationName,
      value: values[index] ?? null,
    };
  });
}

export interface MapVisualAdvancedProps {
  visual: InterpretedVisual;
  tileServerConfigured?: boolean;
}

/** Número de ubicaciones listadas en el aviso compacto. */
const COLLAPSED_LOCATION_LIMIT = 4;

interface ProjectedMapPoint {
  point: MapDataPoint;
  x: number;
  y: number;
  radius: number;
}

function projectMapPoints(points: MapDataPoint[]): { points: ProjectedMapPoint[]; continentalUs: boolean } {
  const valid = points.filter((point) => point.latitude !== null && point.longitude !== null);
  const continentalUs = valid.length > 0 && valid.every((point) => (
    Number(point.latitude) >= 24 && Number(point.latitude) <= 50
    && Number(point.longitude) >= -125 && Number(point.longitude) <= -66
  ));
  const latitudes = valid.map((point) => Number(point.latitude));
  const longitudes = valid.map((point) => Number(point.longitude));
  const minLat = continentalUs ? 24 : Math.min(...latitudes);
  const maxLat = continentalUs ? 50 : Math.max(...latitudes);
  const minLng = continentalUs ? -125 : Math.min(...longitudes);
  const maxLng = continentalUs ? -66 : Math.max(...longitudes);
  const latSpan = Math.max(maxLat - minLat, 1);
  const lngSpan = Math.max(maxLng - minLng, 1);
  const numericValues = valid.map((point) => typeof point.value === 'number' ? Math.max(point.value, 0) : 0);
  const maxValue = Math.max(...numericValues, 0);

  return {
    continentalUs,
    points: valid.map((point) => {
      const value = typeof point.value === 'number' ? Math.max(point.value, 0) : 0;
      return {
        point,
        radius: maxValue > 0 ? 2.5 + Math.sqrt(value / maxValue) * 4.5 : 4,
        x: 18 + ((Number(point.longitude) - minLng) / lngSpan) * 324,
        y: 12 + ((maxLat - Number(point.latitude)) / latSpan) * 148,
      };
    }),
  };
}

/**
 * Fila compacta para mapas sin coordenadas: un aviso de una línea (título +
 * estado) y, opcionalmente, las ubicaciones sin geocodificar. Colapsa lo que
 * antes eran tres paneles de diagnóstico en un solo elemento discreto que
 * ocupa la altura de la celda sin dominar el panel.
 */
export const MapVisualAdvanced: React.FC<MapVisualAdvancedProps> = ({
  visual,
  tileServerConfigured = true,
}) => {
  const report = detectMapCapability(visual, { tileServerConfigured });
  const title = displayTitle(visual.title, visual.name);

  if (report.status !== 'ready') {
    return (
      <div
        className="dataviz-map-placeholder"
        role="note"
        aria-label={`${title} - Map Placeholder`}
        style={{
          alignItems: 'flex-start',
          backgroundColor: '#f8fafc',
          border: '1px dashed #cbd5e0',
          borderRadius: 8,
          boxSizing: 'border-box',
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
          height: '100%',
          justifyContent: 'center',
          padding: '8px 12px',
          width: '100%',
        }}
      >
        <div className="dataviz-map-placeholder-header" style={{ alignItems: 'center', display: 'flex', gap: 8, maxWidth: '100%' }}>
          <h4 style={{ color: '#29425b', fontSize: 13, fontWeight: 600, margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</h4>
          <span
            className="dataviz-map-status-badge"
            style={{
              backgroundColor: '#eef2f6',
              border: '1px solid #d7e0e8',
              borderRadius: 999,
              color: '#52657a',
              flexShrink: 0,
              fontSize: 11,
              fontWeight: 600,
              padding: '1px 8px',
              whiteSpace: 'nowrap',
            }}
          >
            {report.status === 'placeholder_missing_coords'
              ? `Map: ${report.pointCount} locations without coordinates`
              : 'Map: tile server offline'}
          </span>
        </div>
        {report.points.length > 0 && (
          <div
            className="dataviz-map-placeholder-locations"
            style={{ color: '#64748b', fontSize: 11, maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {report.points.slice(0, COLLAPSED_LOCATION_LIMIT)
              .map((p) => p.locationName)
              .concat(report.points.length > COLLAPSED_LOCATION_LIMIT ? [`+${report.points.length - COLLAPSED_LOCATION_LIMIT} more`] : [])
              .join(' · ')}
          </div>
        )}
      </div>
    );
  }

  const projected = projectMapPoints(report.points);

  return (
    <div
      className="dataviz-map-canvas"
      role="region"
      aria-label={`${title} - Active Map Canvas with ${report.validPointCount} data points`}
      tabIndex={0}
      style={{
        backgroundColor: '#ffffff',
        border: '1px solid #e2e8f0',
        borderRadius: 8,
        boxSizing: 'border-box',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        padding: 12,
        width: '100%',
      }}
    >
      <div className="dataviz-map-header" style={{ alignItems: 'center', display: 'flex', gap: 8, justifyContent: 'space-between' }}>
        <h4 style={{ color: '#29425b', fontSize: 13, fontWeight: 600, margin: 0 }}>{title}</h4>
        <span
          className="dataviz-map-status-badge"
          style={{
            backgroundColor: '#e6f4ee',
            borderRadius: 999,
            color: '#1d6f4c',
            fontSize: 11,
            fontWeight: 600,
            padding: '1px 8px',
            whiteSpace: 'nowrap',
          }}
        >
          {`${report.validPointCount} geocoded points`}
        </span>
      </div>
      <div
        className="dataviz-map-view"
        style={{
          alignItems: 'center',
          backgroundColor: '#eaf3fb',
          borderRadius: 6,
          display: 'flex',
          flex: 1,
          justifyContent: 'center',
          marginTop: 8,
          minHeight: 0,
        }}
      >
        <svg
          viewBox="0 0 360 180"
          width="100%"
          height="100%"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label={`Coordinate plot with ${report.validPointCount} points`}
        >
          <rect x="0" y="0" width="360" height="180" rx="8" fill="#e8f1f7" />
          {projected.continentalUs && (
            <path
              d="M20 46 53 29 96 23 141 30 172 24 218 31 257 22 302 35 334 55 325 81 306 91 297 119 278 143 250 153 222 145 199 132 174 135 146 123 116 128 87 111 61 105 47 85 28 76Z"
              fill="#d3e3ec"
              stroke="#9db6c8"
              strokeWidth="1.5"
            />
          )}
          {projected.points.map(({ point, x, y, radius }, i) => (
            <circle
              aria-label={`${point.locationName}: ${point.latitude}, ${point.longitude}${point.value === null || point.value === undefined ? '' : `, ${String(point.value)}`}`}
              cx={x}
              cy={y}
              fill="#1677b8"
              fillOpacity="0.82"
              key={i}
              r={radius}
              stroke="#ffffff"
              strokeWidth="1"
            >
              <title>{`${point.locationName}: ${point.value ?? `${point.latitude}, ${point.longitude}`}`}</title>
            </circle>
          ))}
        </svg>
      </div>
    </div>
  );
};
