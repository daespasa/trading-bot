"use client"
import {
  Activity,
  BrainCircuit,
  ChartLine,
  FlaskConical,
  History,
  LayoutDashboard,
  Radar,
  Scale,
  Settings,
  SlidersHorizontal,
  Wallet,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar"
import { t } from "@/lib/i18n"

const NAV = () => [
  { href: "/", label: t("Overview"), icon: LayoutDashboard },
  { href: "/profitability", label: t("Profitability Center"), icon: Scale },
  { href: "/markets", label: t("Live markets"), icon: Activity },
  { href: "/opportunities", label: t("Opportunities"), icon: Radar },
  { href: "/paper-trading", label: t("Paper trading"), icon: Wallet },
  { href: "/ai", label: t("AI predictions"), icon: BrainCircuit },
  { href: "/strategies", label: t("Strategies"), icon: SlidersHorizontal },
  { href: "/trades", label: t("Trade history"), icon: History },
  { href: "/analytics", label: t("Analytics"), icon: ChartLine },
  { href: "/settings", label: t("Settings"), icon: Settings },
]

export function AppSidebar() {
  const path = usePathname()
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <div className="flex items-center gap-2 px-1 py-1.5">
          <FlaskConical className="size-5 shrink-0 text-primary" />
          <div className="leading-tight group-data-[collapsible=icon]:hidden">
            <div className="text-sm font-semibold">Paper Lab</div>
            <div className="text-xs text-muted-foreground">{t("Simulated trading on Polymarket")}</div>
          </div>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup data-tour="nav">
          <SidebarGroupContent>
            <SidebarMenu>
              {NAV().map((n) => (
                <SidebarMenuItem key={n.href}>
                  <SidebarMenuButton
                    asChild
                    tooltip={n.label}
                    isActive={n.href === "/" ? path === "/" : path.startsWith(n.href)}
                  >
                    <Link href={n.href}>
                      <n.icon />
                      <span>{n.label}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <p className="px-2 pb-1 text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">
          {t("Fictitious money only. No wallet is connected.")}
        </p>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
