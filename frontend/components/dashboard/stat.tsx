import { Card, CardContent } from "@/components/ui/card"
import { cn } from "@/lib/utils"

export function Stat({ label, value, sub, className }: { label: string; value: React.ReactNode; sub?: React.ReactNode; className?: string }) {
  return (
    <Card className="gap-0 py-3">
      <CardContent className="px-4">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className={cn("mt-1 text-xl font-semibold num", className)}>{value}</div>
        {sub && <div className="mt-0.5 text-xs text-muted-foreground num">{sub}</div>}
      </CardContent>
    </Card>
  )
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight" data-tour="page-title">{title}</h1>
        {description && <p className="mt-1 max-w-[70ch] text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2" data-tour="page-actions">{actions}</div>}
    </div>
  )
}
