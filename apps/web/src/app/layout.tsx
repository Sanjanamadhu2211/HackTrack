import type { Metadata } from "next";
import Link from "next/link";
import ThemeInit from "@/components/ThemeInit";
import ThemeToggle from "@/components/ThemeToggle";
import "./globals.css";

export const metadata: Metadata = {
  title: "HackTrack",
  description: "Every hackathon has a next action.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ThemeInit />
        <header className="top">
          <Link href="/" className="brand">
            HackTrack
          </Link>
          <nav>
            <Link href="/">Dashboard</Link>
            <Link href="/capture">Capture link</Link>
            <Link href="/inbox">Inbox</Link>
            <Link href="/hackathons/new">Add event</Link>
            <Link href="/settings">Settings</Link>
            <ThemeToggle />
          </nav>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}

