import type { Metadata } from "next"
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google"
import { AppSidebar } from "@/components/dashboard/app-sidebar"
import { TopBar } from "@/components/dashboard/top-bar"
import { Providers } from "@/components/providers"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import "./globals.css"

const plex = IBM_Plex_Sans({ variable: "--font-plex", subsets: ["latin"], weight: ["400", "500", "600"] })
const plexMono = IBM_Plex_Mono({ variable: "--font-plex-mono", subsets: ["latin"], weight: ["400"] })

export const metadata: Metadata = {
  title: "Polymarket Paper Lab",
  description: "Paper-trading research lab: does any strategy beat Polymarket prices after costs?",
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${plex.variable} ${plexMono.variable} antialiased`} suppressHydrationWarning>
      <body>
        <Providers>
          <SidebarProvider>
            <AppSidebar />
            <SidebarInset>
              <TopBar />
              <main className="flex-1 p-4 md:p-6">{children}</main>
            </SidebarInset>
          </SidebarProvider>
        </Providers>
      </body>
    </html>
  )
}
