import { useReducer, useCallback, useEffect, useRef } from 'react'

const initialState = {
  phone: '',
  loading: false,
  result: null,
  error: null,
  mode: 'real',
  history: [],
  validationError: null,
}

function reducer(state, action) {
  switch (action.type) {
    case 'SET_PHONE':
      return { ...state, phone: action.payload, validationError: null }
    case 'SET_LOADING':
      return { ...state, loading: action.payload, error: null }
    case 'SET_RESULT':
      return { ...state, result: action.payload, loading: false, error: null, validationError: null }
    case 'SET_ERROR':
      return { ...state, error: action.payload, loading: false }
    case 'SET_VALIDATION_ERROR':
      return { ...state, validationError: action.payload }
    case 'SET_MODE':
      return { ...state, mode: action.payload }
    case 'ADD_HISTORY':
      return { ...state, history: [action.payload, ...state.history].slice(0, 20) }
    case 'CLEAR_RESULT':
      return { ...state, result: null }
    default:
      return state
  }
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState)
  const mapRef = useRef(null)

  const validatePhone = useCallback((raw) => {
    let digits = raw.replace(/\D/g, '')
    if (!digits) return null
    if (digits.startsWith('381')) digits = digits.slice(3)
    if (!/^6[1-9]\d{6,8}$/.test(digits)) {
      return 'Invalid Serbian mobile number. Example: 641234567'
    }
    return null
  }, [])

  const handleTrack = useCallback(async () => {
    const validationError = validatePhone(state.phone)
    if (validationError) {
      dispatch({ type: 'SET_VALIDATION_ERROR', payload: validationError })
      return
    }
    dispatch({ type: 'SET_LOADING', payload: true })

    try {
      const digits = state.phone.replace(/\D/g, '')
      const normalized = digits.startsWith('381') ? `+${digits}` : `+381${digits}`

      const payload = {
        phone: normalized,
        use_simulation: state.mode === 'simulation',
        towers: state.mode === 'simulation' ? [] : (state.result?.location?.matched_towers ? [] : []),
      }

      const { data } = await fetch('/api/v6/track', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })

      if (data.success) {
        dispatch({ type: 'SET_RESULT', payload: data })
        dispatch({
          type: 'ADD_HISTORY',
          payload: {
            id: Date.now(),
            phone: data.phone,
            carrier: data.carrier?.carrier || 'Unknown',
            lat: data.location?.latitude,
            lon: data.location?.longitude,
            accuracy: data.location?.accuracy_km,
            confidence: data.location?.confidence,
            method: data.location?.method,
            timestamp: new Date().toISOString(),
          },
        })
      } else {
        dispatch({ type: 'SET_ERROR', payload: data.detail || 'Tracking failed' })
      }
    } catch (e) {
      dispatch({ type: 'SET_ERROR', payload: e.message || 'Network error' })
    }
  }, [state.phone, state.mode, state.result, validatePhone])

  const selectHistory = useCallback(
    (item) => {
      dispatch({ type: 'SET_PHONE', payload: item.phone.replace('+381', '') })
      if (mapRef.current && item.lat && item.lon) {
        mapRef.current.flyTo([item.lat, item.lon], 13, { duration: 1 })
      }
    },
    [mapRef]
  )

  const handlePhoneChange = useCallback(
    (value) => {
      const digits = value.replace(/[^\d]/g, '').slice(0, 12)
      dispatch({ type: 'SET_PHONE', payload: digits })
    },
    []
  )

  useEffect(() => {
    fetch('/api/v1/health')
      .then((r) => r.json())
      .catch(() => {})
  }, [])

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <h1>SerbiaTracker</h1>
          <span className="badge">+381</span>
        </div>
        <div className="header-meta">
          {state.history.length > 0 && `${state.history.length} trackings`}
        </div>
      </header>

      <div className="main">
        <aside className="sidebar">
          <TrackingPanel
            phone={state.phone}
            onPhoneChange={handlePhoneChange}
            onTrack={handleTrack}
            loading={state.loading}
            error={state.error}
            validationError={state.validationError}
            result={state.result}
            mode={state.mode}
            onModeChange={(m) => dispatch({ type: 'SET_MODE', payload: m })}
            history={state.history}
            onSelectHistory={selectHistory}
          />
        </aside>

        <div className="map-container">
          <MapView ref={mapRef} result={state.result} loading={state.loading} />
        </div>
      </div>
    </div>
  )
}
