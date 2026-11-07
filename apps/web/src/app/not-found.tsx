import Link from "next/link";

export default function NotFoundPage() {
  return (
    <div className="panel" style={{ maxWidth: 520, margin: "40px auto", textAlign: "center" }}>
      <h1>That page doesn&apos;t exist</h1>
      <p className="muted">
        The link might be broken, or the page may have moved. Your hackathons are
        still right where you left them.
      </p>
      <div className="row" style={{ justifyContent: "center", marginTop: 16 }}>
        <Link href="/" className="button primary">
          Go to dashboard
        </Link>
      </div>
    </div>
  );
}
