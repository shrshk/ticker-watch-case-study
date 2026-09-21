// Makes the configuration visible on screen.
//
// During a demo it shows which transport is active and whether the numbers are
// real. During a load run it confirms which mode a client is actually in.

export function StatusBar({ transport, status, meta, simulated, intervalSeconds }) {
  const label = { live: 'live', connecting: 'connecting', offline: 'offline' }[status] || status;

  return (
    <div className="status-bar">
      <span className={`pill status-${status}`}>{label}</span>
      <span className="pill">transport: {transport}</span>
      <span className="pill">every {intervalSeconds}s</span>
      {simulated && (
        <span className="pill warn" title="Prices are generated, not live market data">
          simulated prices
        </span>
      )}
      {meta && (
        <span className="pill muted">
          {meta.readPath} · {meta.cacheHits} hit / {meta.cacheMisses} miss
        </span>
      )}
    </div>
  );
}
