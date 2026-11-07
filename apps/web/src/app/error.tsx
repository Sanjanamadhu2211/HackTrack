"use client";

import Link from "next/link";
import { useEffect } from "react";

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="panel" style={{ maxWidth: 520, margin: "40px auto", textAlign: "center" }}>
      <h1>Something went wrong</h1>
      <p className="muted">
        We couldn&apos;t load this page. Your hackathons are safe — this is usually
        a temporary hiccup.
      </p>
      {process.env.NODE_ENV === "development" && error.message && (
        <p className="error">{error.message}</p>
      )}
      <div className="row" style={{ justifyContent: "center", marginTop: 16 }}>
        <button type="button" className="primary" onClick={() => reset()}>
          Try again
        </button>
        <Link href="/" className="button">
          Go to dashboard
        </Link>
      </div>
    </div>
  );
}
