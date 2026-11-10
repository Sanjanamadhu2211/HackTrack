"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import ThemeToggle from "@/components/ThemeToggle";
import { useUser } from "@/lib/UserContext";

const NAV_LINKS = [
  { href: "/", label: "Dashboard", icon: "dashboard" },
  { href: "/capture", label: "Capture", icon: "capture" },
  { href: "/inbox", label: "Inbox", icon: "inbox" },
  { href: "/hackathons/new", label: "Add event", icon: "add" },
  { href: "/settings", label: "Settings", icon: "settings" },
] as const;

function NavIcon({ name }: { name: (typeof NAV_LINKS)[number]["icon"] }) {
  return (
    <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {name === "dashboard" && <><rect x="3.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="3.5" y="13.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="13.5" width="7" height="7" rx="1.5" /></>}
      {name === "capture" && <><path d="M10 13.5a4 4 0 0 0 5.7 0l3-3A4 4 0 0 0 13 4.8l-1.5 1.5" /><path d="M14 10.5a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 19.2l1.5-1.5" /></>}
      {name === "inbox" && <><path d="M4 5.5h16l1.5 8v5H2.5v-5z" /><path d="M3 13.5h5.5l1.5 2h4l1.5-2H21" /></>}
      {name === "add" && <><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M12 8v8M8 12h8" /></>}
      {name === "settings" && <><circle cx="12" cy="12" r="3" /><path d="M19 12a7.6 7.6 0 0 0-.1-1.2l1.6-1.3-1.6-2.8-2 .7a7 7 0 0 0-2-1.2l-.3-2.1H9.4L9 6.2a7 7 0 0 0-2 1.2l-2-.7-1.6 2.8L5 10.8a7.6 7.6 0 0 0 0 2.4l-1.6 1.3L5 17.3l2-.7a7 7 0 0 0 2 1.2l.4 2.1h5.2l.3-2.1a7 7 0 0 0 2-1.2l2 .7 1.6-2.8-1.6-1.3A7.6 7.6 0 0 0 19 12z" /></>}
    </svg>
  );
}

export default function Navbar() {
  const pathname = usePathname();
  const { me } = useUser();

  const isAuthPage = pathname === "/login";

  return (
    <header className="top">
      <div className="header-inner">
        <Link href="/" className="brand">
          <span className="brand-icon" aria-hidden="true">H</span>
          <span className="brand-copy"><span className="brand-title">HackTrack</span><span className="brand-caption">Personal workspace</span></span>
        </Link>

        {!isAuthPage && (
          <>
          <div className="nav-section-label">Workspace</div>
          <nav className="nav-links" aria-label="Primary">
            {NAV_LINKS.map(({ href, label, icon }) => {
              const active = pathname === href || (href !== "/" && pathname.startsWith(href));
              return (
                <Link
                  key={href}
                  href={href}
                  className={`nav-link${active ? " active" : ""}`}
                  aria-current={active ? "page" : undefined}
                >
                  <NavIcon name={icon} />
                  {label}
                </Link>
              );
            })}
          </nav>
          </>
        )}

        <div className="nav-actions">
          {me && (
            <Link href="/settings" className="user-badge" title={me.email} aria-label="Account settings">
              <span className="user-avatar" aria-hidden="true">{me.email.slice(0, 1).toUpperCase()}</span>
              <span className="user-details"><span className="user-email">{me.name || me.email.split("@")[0]}</span><span className="user-caption">Account settings</span></span>
            </Link>
          )}
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
