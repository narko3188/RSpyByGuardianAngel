import { useEffect, useRef, forwardRef, useImperativeHandle } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

delete L.Icon.Default.prototype._getIconUrl
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
})

const SERBIA_CENTER = [44.0165, 21.0059]
const SERBIA_BOUNDS = [[41.85, 18.81], [46.19, 23.01]]

const operatorMarkerColors = {
  'A1': '#e74c3c',
  'Yettel': '#f39c12',
  'mt:s': '#3498db',
  'MTS': '#3498db',
  'Orion': '#ff6600',
}

function getOperatorColor(carrier) {
  if (!carrier) return '#8888aa'
  const name = carrier.carrier || carrier.name || carrier
  if (operatorMarkerColors[name]) return operatorMarkerColors[name]
  const upper = String(name).toUpperCase()
  if (upper.includes('A1')) return operatorMarkerColors['A1']
  if (upper.includes('YETTEL')) return operatorMarkerColors['Yettel']
  if (upper.includes('MTS') || upper.includes('MT:S')) return operatorMarkerColors['mt:s']
  if (upper.includes('ORION')) return operatorMarkerColors['Orion']
  return '#8888aa'
}

function formatCoord(value, digits = 6) {
  if (value == null || Number.isNaN(value)) return '—'
  return value.toFixed(digits)
}

const MapView = forwardRef(function MapView({ result, loading }, ref) {
  const mapRef = useRef(null)
  const mapInstance = useRef(null)
  const layersRef = useRef(null)
  const hasCenteredRef = useRef(false)

  useImperativeHandle(ref, () => ({
    flyTo(lat, lon, zoom = 13, options = {}) {
      if (!mapInstance.current) return
      const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      const duration = reducedMotion ? 0 : (options.duration ?? 1)
      if (duration === 0) {
        mapInstance.current.setView([lat, lon], zoom, { animate: false })
      } else {
        mapInstance.current.flyTo([lat, lon], zoom, { duration, ...options })
      }
    },
  }))

  useEffect(() => {
    if (!mapRef.current || mapInstance.current) return

    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

    mapInstance.current = L.map(mapRef.current, {
      center: SERBIA_CENTER,
      zoom: 8,
      maxBounds: L.latLngBounds(SERBIA_BOUNDS),
      maxBoundsViscosity: 0.8,
      zoomControl: true,
      attributionControl: true,
    })

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: 'abcd',
      maxZoom: 19,
    }).addTo(mapInstance.current)

    layersRef.current = L.layerGroup().addTo(mapInstance.current)

    return () => {
      mapInstance.current?.remove()
      mapInstance.current = null
      layersRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!mapInstance.current || !layersRef.current) return
    layersRef.current.clearLayers()

    if (!result?.location) return

    const { latitude, longitude, accuracy_km, accuracy_meters, confidence, matched_towers, carrier } = result.location
    const confColor = (confidence && ['excellent','good','moderate','low','poor'].includes(confidence))
      ? ({ excellent: '#00ff88', good: '#00d2ff', moderate: '#ffaa00', low: '#ff6b00', poor: '#e94560' }[confidence])
      : '#e94560'

    if (accuracy_km || accuracy_meters) {
      const radius = typeof accuracy_meters === 'number' ? accuracy_meters : (typeof accuracy_km === 'number' ? accuracy_km * 1000 : 0)
      if (radius > 0) {
        L.circle([latitude, longitude], {
          radius,
          color: '#00d2ff',
          fillColor: '#00d2ff',
          fillOpacity: 0.08,
          weight: 2,
          dashArray: '6 4',
          interactive: false,
        }).addTo(layersRef.current)
      }
    }

    const opColor = getOperatorColor(carrier)

    const mainMarker = L.circleMarker([latitude, longitude], {
      radius: 9,
      fillColor: confColor,
      color: '#ffffff',
      weight: 3,
      fillOpacity: 0.9,
    }).bindPopup(
      L.Util.template(
        `<div class="popup-content">
          <strong>Estimated position</strong><br/>
          <div class="popup-row"><span class="popup-label">Lat</span><span>{lat}</span></div>
          <div class="popup-row"><span class="popup-label">Lon</span><span>{lon}</span></div>
          <div class="popup-row"><span class="popup-label">Accuracy</span><span>{accuracy}</span></div>
          <div class="popup-row"><span class="popup-label">Method</span><span>{method}</span></div>
          <div class="popup-row"><span class="popup-label">Confidence</span><span>{confidence}</span></div>
          <div class="popup-row"><span class="popup-label">Operator</span><span>{operator}</span></div>
        </div>`,
        {
          lat: formatCoord(latitude),
          lon: formatCoord(longitude),
          accuracy: accuracy_meters ? `${Math.round(accuracy_meters)} m` : `${accuracy_km ?? '—'} km`,
          method: result.location.method || '—',
          confidence: confidence || '—',
          operator: carrier?.carrier || 'Unknown',
        }
      ),
      { className: 'custom-popup', closeButton: true, autoClose: true }
    )
    mainMarker.addTo(layersRef.current)

    const pulseMarker = L.circleMarker([latitude, longitude], {
      radius: 4,
      fillColor: confColor,
      color: confColor,
      weight: 8,
      fillOpacity: 0.25,
      opacity: 0.25,
      interactive: false,
    }).addTo(layersRef.current)

    if (matched_towers && Array.isArray(matched_towers) && matched_towers.length > 0) {
      matched_towers.forEach((tower) => {
        if (tower.lat == null || tower.lon == null) return
        const tColor = getOperatorColor({ carrier: tower.carrier || tower.operator || carrier?.carrier })
        L.circleMarker([tower.lat, tower.lon], {
          radius: 5,
          fillColor: tColor,
          color: '#ffffff',
          weight: 1,
          fillOpacity: 0.85,
        }).bindPopup(
          L.Util.template(
            `<div class="popup-content">
              <strong>Tower</strong><br/>
              <div class="popup-row"><span class="popup-label">Lat</span><span>{lat}</span></div>
              <div class="popup-row"><span class="popup-label">Lon</span><span>{lon}</span></div>
              <div class="popup-row"><span class="popup-label">Operator</span><span>{operator}</span></div>
              <div class="popup-row"><span class="popup-label">Distance</span><span>{distance} km</span></div>
            </div>`,
            {
              lat: formatCoord(tower.lat),
              lon: formatCoord(tower.lon),
              operator: tower.carrier || tower.operator || 'Unknown',
              distance: tower.distance_km ?? tower.closest_tower_km ?? '—',
            }
          ),
          { className: 'custom-popup', closeButton: true, autoClose: true }
        ).addTo(layersRef.current)
      })
    }

  }, [result])

  const showEmpty = !result && !loading
  const showLoading = loading

  return (
    <>
      <div ref={mapRef} style={{ width: '100%', height: '100%' }} aria-label="Map" />
      {showEmpty && (
        <div className="map-empty" aria-hidden="true">
          Enter a number and locate to show the map.
        </div>
      )}
      {showLoading && (
        <div className="map-loading-overlay" aria-live="polite" aria-label="Loading map">
          <div className="map-loading-spinner" />
        </div>
      )}
    </>
  )
})

export default MapView
