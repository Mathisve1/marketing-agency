import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Yuvo OS — Operator",
  description:
    "Yuvo Studio AI Creative Operating System — agency operator dashboard + private client approval portal. Internal-only.",
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
