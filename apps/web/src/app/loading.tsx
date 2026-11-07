export default function LoadingPage() {
  return (
    <div aria-busy="true" aria-label="Loading">
      <div className="skeleton" style={{ height: 28, width: "40%", marginBottom: 16 }} />
      <div className="stats">
        {Array.from({ length: 5 }).map((_, i) => (
          <div className="panel" key={i}>
            <div className="skeleton" style={{ height: 28, width: "60%", marginBottom: 8 }} />
            <div className="skeleton" style={{ height: 14, width: "80%" }} />
          </div>
        ))}
      </div>
      <div className="grid" style={{ marginTop: 16 }}>
        {Array.from({ length: 3 }).map((_, i) => (
          <div className="panel" key={i}>
            <div className="skeleton" style={{ height: 18, width: "70%", marginBottom: 12 }} />
            <div className="skeleton" style={{ height: 14, width: "100%", marginBottom: 8 }} />
            <div className="skeleton" style={{ height: 14, width: "85%" }} />
          </div>
        ))}
      </div>
    </div>
  );
}
