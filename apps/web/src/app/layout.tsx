import type { Metadata } from "next";
import ThemeInit from "@/components/ThemeInit";
import Navbar from "@/components/Navbar";
import { UserProvider } from "@/lib/UserContext";
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
        <UserProvider>
          <Navbar />
          <main className="main-content">{children}</main>
        </UserProvider>
      </body>
    </html>
  );
}
