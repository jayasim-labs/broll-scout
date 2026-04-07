import type React from "react"
import "./globals.css"
import type { Metadata } from "next"
import { Inter } from "next/font/google"
import { Toaster } from "@/components/ui/sonner"

const inter = Inter({ subsets: ["latin"], variable: "--font-sans" })

export const metadata: Metadata = {
  title: "JJ — BRoll Scout Intelligence",
  description: "AI-powered B-roll intelligence for documentary editors. Paste a script, get 30+ timestamped YouTube clips.",
  robots: {
    index: false,
    follow: false,
    googleBot: {
      index: false,
      follow: false,
    },
  },
  icons: {
    icon: [
      { url: "/favicon-32x32.png", sizes: "32x32", type: "image/png" },
      { url: "/favicon-16x16.png", sizes: "16x16", type: "image/png" },
    ],
    apple: "/apple-touch-icon.png",
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body className={`${inter.variable} font-sans antialiased flex flex-col min-h-screen`}>
        <div className="flex-1">{children}</div>
        <footer className="border-t border-border bg-card py-4 text-center text-xs text-muted-foreground">
          &copy; {new Date().getFullYear()} BRoll Scout Intelligence. All rights reserved. &middot;{" "}
          <a href="mailto:contact@jayasim.com" className="hover:text-foreground transition-colors">
            contact@jayasim.com
          </a>
        </footer>
        <Toaster />
      </body>
    </html>
  )
}
