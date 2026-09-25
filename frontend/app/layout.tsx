import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata, Viewport } from "next";
import { Providers } from "@/lib/providers";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "relate [R] · AI-first CRM", template: "%s · relate [R]" },
  description: "Intelligent pipeline memory. A private-cloud, AI-first CRM from the SDC Solutions portfolio.",
  icons: { icon: "/icon.svg" },
};

export const viewport: Viewport = {
  themeColor: [{ media: "(prefers-color-scheme: light)", color: "#fafafc" }, { media: "(prefers-color-scheme: dark)", color: "#0d0d10" }],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
