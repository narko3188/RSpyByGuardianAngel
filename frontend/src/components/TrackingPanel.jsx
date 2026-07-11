import { useMemo } from 'react'

const OPERATOR_COLORS = {
  'A1 Srbija': '#e74c3c',
  'A1': '#e74c3c',
  'Yettel': '#f39c12',
  'mt:s': '#3498db',
  'MTS': '#3498db',
  'Orion': '#ff6600',
}

const OPERATOR_COLOR_DEFAULT = '#8888aa'

const CONFIDENCE_COLORS = {
  excellent: '#00ff88',
  good: '#00d2ff',
  moderate: '#ffaa00',
  low: '#ff6b00',
  poor: '#e94560',
}

function getOperatorColor(carrier) {
  if (!carrier) return OPERATOR_COLOR_DEFAULT
  const name = carrier.carrier || carrier.name || carrier
  if (OPERATOR_COLORS[name]) return OPERATOR_COLORS[name]
  const upper = String(name).toUpperCase()
  if (upper.includes('A1')) return OPERATOR_COLORS['A1']
  if (upper.includes('YETTEL')) return OPERATOR_COLORS['Yettel']
  if (upper.includes('MTS') || upper.includes('MT:S')) return OPERATOR_COLORS['mt:s']
  if (upper.includes('ORION')) return OPERATOR_COLORS['Orion']
  return OPERATOR_COLOR_DEFAULT
}

function formatTimestamp(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString()
}

export default function TrackingPanel({
  phone,
  onPhoneChange,
  onTrack,
  loading,
  error,
  validationError,
  result,
  mode,
  onModeChange,
  history,
  onSelectHistory,
}) {
  const operatorColor = useMemo(() => getOperatorColor(result?.carrier), [result?.carrier])

  return (
    <>
      <div className="input-group">
        <label htmlFor="phone-input">Phone number</label>
        <div className="phone-input">
          <span className="prefix" aria-hidden="true">+381</span>
          <input
            id="phone-input"
            type="tel"
            inputMode="tel"
            autoComplete="tel"
            value={phone}
            onChange={(e) => onPhoneChange(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && onTrack()}
            placeholder="641234567"
            maxLength={12}
            aria-invalid={!!validationError}
            aria-describedby={validationError ? 'phone-error' : undefined}
          />
        </div>
        <div id="phone-error" className="phone-error" role="alert" aria-live="polite">
          {validationError || '\u00A0'}
        </div>
      </div>

      <div className="input-group">
        <label>Mode</label>
        <div className="mode-selector" role="group" aria-label="Tracking mode">
          <button
            type="button"
            className={`mode-btn ${mode === 'simulation' ? 'active' : ''}`}
            onClick={() => onModeChange('simulation')}
            aria-pressed={mode === 'simulation'}
          >
            Simulation
          </button>
          <button
            type="button"
            className={`mode-btn ${mode === 'real' ? 'active' : ''}`}
            onClick={() => onModeChange('real')}
            aria-pressed={mode === 'real'}
          >
            Real APIs
          </button>
        </div>
      </div>

      <div className="buttons-row">
        <button
          type="button"
          className="btn btn-primary"
          onClick={onTrack}
          disabled={loading}
          aria-busy={loading}
        >
          {loading ? 'Locating...' : 'Locate'}
        </button>
      </div>

      {(error || validationError) && (
        <div className="alert" role="alert" aria-live="assertive">
          {error || validationError}
        </div>
      )}

      {result && result.success && (
        <div className="result-card">
          <div className="result-card-header">
            <span>Result</span>
          </div>

          <div className="result-row">
            <span className="label">Operator</span>
            <span className="value" style={{ color: operatorColor }}>
              {result.carrier?.carrier || 'Unknown'}
            </span>
          </div>

          <div className="result-row">
            <span className="label">City</span>
            <span className="value" style={{ textTransform: 'capitalize' }}>
              {result.location?.city_estimated || '—'}
            </span>
          </div>

          <div className="result-row">
            <span className="label">Coordinates</span>
            <span className="value">
              {result.location?.latitude?.toFixed(6)}, {result.location?.longitude?.toFixed(6)}
            </span>
          </div>

          <div className="result-row">
            <span className="label">Accuracy</span>
            <span className="value">
              {result.location?.accuracy_meters
                ? `${Math.round(result.location.accuracy_meters)} m`
                : `${result.location?.accuracy_km ?? '—'} km`}
            </span>
          </div>

          <div className="result-row">
            <span className="label">Confidence</span>
            <span
              className={`confidence-badge confidence-${result.location?.confidence || 'poor'}`}
            >
              {result.location?.confidence || 'N/A'}
            </span>
          </div>

          <div className="result-row">
            <span className="label">Method</span>
            <span className="value">{result.location?.method || '—'}</span>
          </div>

          <div className="result-row">
            <span className="label">Towers</span>
            <span className="value">
              {result.location?.towers_used ?? result.location?.matched_towers ?? 0} used
            </span>
          </div>

          <div className="result-row">
            <span className="label">Elapsed</span>
            <span className="value">{result.elapsed_ms ? `${result.elapsed_ms} ms` : '—'}</span>
          </div>
        </div>
      )}

      <div className="history-section">
        <div className="history-header">
          History {history.length > 0 && `(${history.length})`}
        </div>
        {history.length === 0 ? (
          <div className="history-empty">No history yet.</div>
        ) : (
          <div className="history-list scrollbar-thin" role="list">
            {history.map((item) => (
              <div
                key={item.id}
                className="history-item"
                role="listitem"
                tabIndex={0}
                onClick={() => onSelectHistory(item)}
                onKeyDown={(e) => e.key === 'Enter' && onSelectHistory(item)}
              >
                <div className="history-item-phone">{item.phone}</div>
                <div className="history-item-meta">
                  {item.carrier} • {item.accuracy ?? '—'} km • {item.confidence || '—'}
                </div>
                <div className="history-item-time">{formatTimestamp(item.timestamp)}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}
