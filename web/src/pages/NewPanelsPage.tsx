/**
 * N10 — New Panels page.
 *
 * Renders all 5 new diagnostic/analytical panels:
 *   1. RiskAtStakeHeader — open risk summary
 *   2. CloseReasonDonut  — exit mix by PnL sign
 *   3. TradeGateTable    — symbol attribution gate
 *   4. MonthlyCalendar   — daily PnL calendar (Diagnostic)
 *   5. ExpectancyHeatmap — hour×DOW PnL heatmap (Diagnostic)
 *
 * Route: /panels
 */
import {
  CloseReasonDonut,
  TradeGateTable,
  RiskAtStakeHeader,
  MonthlyCalendar,
  ExpectancyHeatmap,
} from '../components/NewPanels'

export function NewPanelsPage() {
  return (
    <div className="p-4 max-w-screen-xl mx-auto">
      <h1 className="text-2xl font-bold text-slate-100 mb-4">Analytics Panels</h1>

      {/* Risk summary always-visible at the top */}
      <RiskAtStakeHeader />

      {/* Two-column grid: donut + gate table */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-0">
        <CloseReasonDonut />
        <TradeGateTable />
      </div>

      {/* Diagnostic panels */}
      <MonthlyCalendar />
      <ExpectancyHeatmap />
    </div>
  )
}
