"use client"
import {
  type ColumnDef,
  type RowData,
  columnFilteringFeature,
  createFilteredRowModel,
  createSortedRowModel,
  filterFn_includesString,
  globalFilteringFeature,
  rowSortingFeature,
  sortFn_alphanumeric,
  tableFeatures,
  useTable,
} from "@tanstack/react-table"
import { useVirtualizer } from "@tanstack/react-virtual"
import { ArrowDown, ArrowUp } from "lucide-react"
import { useRef } from "react"
import { Input } from "@/components/ui/input"
import { t } from "@/lib/i18n"

export const features = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: { alphanumeric: sortFn_alphanumeric },
  columnFilteringFeature,
  globalFilteringFeature,
  filteredRowModel: createFilteredRowModel(),
  filterFns: { includesString: filterFn_includesString },
})

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Col<T extends RowData> = ColumnDef<typeof features, T, any>

const ROW_H = 37

/** Sortable, searchable table that only renders visible rows (virtualised), for long lists. */
export function DataTable<T extends RowData>({
  data,
  columns,
  search = true,
  height = 560,
  empty,
  toolbar,
  onRowClick,
}: {
  data: T[]
  columns: Col<T>[]
  search?: boolean
  height?: number
  empty?: string
  toolbar?: React.ReactNode
  onRowClick?: (row: T) => void
}) {
  const table = useTable({ features, columns, data, globalFilterFn: "includesString" })
  const rows = table.getRowModel().rows
  const scrollRef = useRef<HTMLDivElement>(null)
  const v = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_H,
    overscan: 10,
  })
  const items = v.getVirtualItems()
  const padTop = items.length ? items[0].start : 0
  const padBottom = items.length ? v.getTotalSize() - items[items.length - 1].end : 0
  const ncols = table.getAllLeafColumns().length

  return (
    <div className="space-y-2" data-tour="table">
      {(search || toolbar) && (
        <div className="flex flex-wrap items-center gap-2">
          {search && (
            <Input
              placeholder={t("Search…")}
              className="h-8 w-full max-w-xs"
              aria-label={t("Search table")}
              onChange={(e) => table.setGlobalFilter(e.target.value)}
            />
          )}
          {toolbar}
          <span className="ml-auto text-xs text-muted-foreground num">{t("{n} rows", { n: rows.length })}</span>
        </div>
      )}
      <div ref={scrollRef} className="overflow-auto rounded-md border" style={{ maxHeight: height }}>
        <table className="w-full caption-bottom text-sm">
          <thead className="sticky top-0 z-[1] bg-card">
            {table.getHeaderGroups().map((g) => (
              <tr key={g.id} className="border-b">
                {g.headers.map((h) => {
                  const sorted = h.column.getIsSorted()
                  return (
                    <th key={h.id} className="h-9 px-3 text-left align-middle text-xs font-medium whitespace-nowrap text-muted-foreground">
                      {h.isPlaceholder ? null : (
                        <button
                          type="button"
                          className="inline-flex items-center gap-1 hover:text-foreground disabled:cursor-default"
                          onClick={h.column.getToggleSortingHandler()}
                          disabled={!h.column.getCanSort()}
                        >
                          {typeof h.column.columnDef.header === "string" ? t(h.column.columnDef.header) : <table.FlexRender header={h} />}
                          {sorted === "asc" ? <ArrowUp className="size-3" /> : sorted === "desc" ? <ArrowDown className="size-3" /> : null}
                        </button>
                      )}
                    </th>
                  )
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {padTop > 0 && <tr style={{ height: padTop }} />}
            {items.map((it) => {
              const row = rows[it.index]
              return (
                <tr
                  key={row.id}
                  className={`border-b last:border-0 hover:bg-muted/40 ${onRowClick ? "cursor-pointer" : ""}`}
                  style={{ height: ROW_H }}
                  onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                >
                  {row.getAllCells().map((c) => (
                    <td key={c.id} className="px-3 py-1.5 align-middle whitespace-nowrap num">
                      <table.FlexRender cell={c} />
                    </td>
                  ))}
                </tr>
              )
            })}
            {padBottom > 0 && <tr style={{ height: padBottom }} />}
            {rows.length === 0 && (
              <tr>
                <td colSpan={ncols} className="p-8 text-center text-sm text-muted-foreground">
                  {empty ?? t("Nothing here yet.")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
