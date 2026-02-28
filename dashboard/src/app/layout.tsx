import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgentCafe — Company Dashboard",
  description: "Onboard your API to the AgentCafe marketplace",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
