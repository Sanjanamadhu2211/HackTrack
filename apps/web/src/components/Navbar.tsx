"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import ThemeToggle from "@/components/ThemeToggle";
import { useUser } from "@/lib/UserContext";

const NAV_LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/capture", label: "Capture" },
  { href: "/inbox", label: "Inbox" },
  { href: "/hackathons/new", label: "Add event" },
  { href: "/settings", label: "Settings" },
];

export default function Navbar() {
  const pathname = usePathname();
  const { me } = useUser();

  const isAuthPage = pathname === "/login";

  return (
    <header className="top">
      <div className="header-inner">
        <Link href="/" className="brand">
          <span className="brand-icon">⚡</span>
          <span className="brand-title">HackTrack</span>
        </Link>

        {!isAuthPage && (
          <nav className="nav-links" aria-label="Primary">
            {NAV_LINKS.map(({ href, label }) => {
              const active = pathname === href || (href !== "/" && pathname.startsWith(href));
              return (
                <Link
                  key={href}
                  href={href}
                  className={`nav-link${active ? " active" : ""}`}
                  aria-current={active ? "page" : undefined}
                >
                  {label}
                </Link>
              );
            })}
          </nav>
        )}

        <div className="nav-actions">
          {me && (
            <Link href="/settings" className="user-badge" title={me.email}>
              <span className="user-dot" />
              <span className="user-email">{me.email.split("@")[0]}</span>
            </Link>
          )}
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
