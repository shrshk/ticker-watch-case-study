const formatPrice = (value) =>
  value == null ? '—' : value.toLocaleString('en-US', { style: 'currency', currency: 'USD' });

const formatTime = (iso) => {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString('en-US', { hour12: false });
};

export function Watchlist({ items, changed, onRemove }) {
  if (items.length === 0) {
    return <p className="empty">Nothing on your watchlist yet. Search for a stock above.</p>;
  }

  return (
    <table className="watchlist">
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Name</th>
          <th className="numeric">Price</th>
          <th>Updated</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.id} className={changed.has(item.id) ? 'moved' : undefined}>
            <td className="ticker">{item.ticker}</td>
            <td className="name">{item.name}</td>
            <td className="numeric price">{formatPrice(item.price)}</td>
            <td className="updated">{formatTime(item.effective_at)}</td>
            <td>
              <button type="button" onClick={() => onRemove(item.id)}>
                Remove
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
