import { useEffect, useState } from "react";
import { api, extractErrorMessage, type UnitOut, type SummaryReport } from "../lib/api";
import { Card, PageHeader, ErrorBanner } from "../components/ui";

function toNum(s: string): number {
  const n = parseFloat(s);
  return Number.isFinite(n) ? n : 0;
}

function fmtMoney(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return `₹${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function fmtPercent(n: number | null): string {
  if (n === null || !Number.isFinite(n)) return "—";
  return `${n.toFixed(2)}%`;
}

interface SimInputs {
  adr: number;
  occupancyPercent: number;
  fixedCosts: number;
  variableCostPerNight: number;
  units: number;
  periodDays: number;
}

interface SimResult {
  availableRoomNights: number;
  occupiedRoomNights: number;
  revenue: number;
  totalCost: number;
  profit: number;
  breakevenOccupancyPercent: number | null;
  breakevenReason: string | null;
}

// Deliberately does NOT clamp occupancy to <=100 — an out-of-range input is
// the user's own scenario to see the (unrealistic) consequences of, not
// something this tool should silently "correct" and then present as if
// nothing were wrong. Only floors at 0, since negative occupied nights
// would flip the sign of revenue/cost in a way that's never meaningful.
function occupiedRoomNightsFor(inputs: SimInputs): number {
  const availableRoomNights = Math.max(inputs.units, 0) * Math.max(inputs.periodDays, 0);
  return availableRoomNights * (Math.max(inputs.occupancyPercent, 0) / 100);
}

function simulate(inputs: SimInputs): SimResult {
  const availableRoomNights = Math.max(inputs.units, 0) * Math.max(inputs.periodDays, 0);
  const occupiedRoomNights = occupiedRoomNightsFor(inputs);
  const revenue = occupiedRoomNights * Math.max(inputs.adr, 0);
  const variableCost = occupiedRoomNights * Math.max(inputs.variableCostPerNight, 0);
  const totalCost = Math.max(inputs.fixedCosts, 0) + variableCost;
  const profit = revenue - totalCost;

  const contributionPerNight = inputs.adr - inputs.variableCostPerNight;
  let breakevenOccupancyPercent: number | null = null;
  let breakevenReason: string | null = null;

  if (availableRoomNights <= 0) {
    breakevenReason = "Not achievable with these inputs — no available room nights (check Units and Period).";
  } else if (contributionPerNight <= 0) {
    breakevenReason = "Not achievable with these inputs — Variable Cost / Night is at or above ADR, so no occupancy level can cover fixed costs.";
  } else {
    const breakevenOccupiedNights = Math.max(inputs.fixedCosts, 0) / contributionPerNight;
    const pct = (breakevenOccupiedNights / availableRoomNights) * 100;
    breakevenOccupancyPercent = Number.isFinite(pct) ? pct : null;
    if (breakevenOccupancyPercent === null) breakevenReason = "Not achievable with these inputs.";
  }

  return { availableRoomNights, occupiedRoomNights, revenue, totalCost, profit, breakevenOccupancyPercent, breakevenReason };
}

function requiredAdrForTargetProfit(inputs: SimInputs, targetProfit: number): number | null {
  const occupiedRoomNights = occupiedRoomNightsFor(inputs);
  if (occupiedRoomNights <= 0) return null;
  const requiredAdr = (targetProfit + Math.max(inputs.fixedCosts, 0)) / occupiedRoomNights + Math.max(inputs.variableCostPerNight, 0);
  return Number.isFinite(requiredAdr) ? requiredAdr : null;
}

function NumberField({
  label, value, onChange, realLabel, hint,
}: {
  label: string; value: string; onChange: (v: string) => void; realLabel?: string | null; hint?: string;
}) {
  return (
    <div>
      <label className="block text-xs text-slate-500 mb-1">{label}</label>
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
      />
      {realLabel && <p className="text-[11px] text-emerald-600 mt-1">Current OS10 value: {realLabel}</p>}
      {hint && <p className="text-[11px] text-slate-400 mt-1">{hint}</p>}
    </div>
  );
}

function ResultStat({ label, value, tone }: { label: string; value: string; tone?: "green" | "red" }) {
  return (
    <div className="bg-slate-50 rounded-lg p-3">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-xl font-semibold ${tone === "green" ? "text-emerald-600" : tone === "red" ? "text-red-600" : "text-slate-900"}`}>{value}</p>
    </div>
  );
}

export default function ProfitSimulatorPage() {
  const [error, setError] = useState<string | null>(null);
  const [loadingReal, setLoadingReal] = useState(true);

  const [realUnits, setRealUnits] = useState<number | null>(null);
  const [realOccupancy, setRealOccupancy] = useState<number | null>(null);
  const [realAdr, setRealAdr] = useState<number | null>(null);
  const [realPeriod, setRealPeriod] = useState<{ date_from: string; date_to: string; days: number } | null>(null);

  const [adr, setAdr] = useState("0");
  const [occupancy, setOccupancy] = useState("0");
  const [fixedCosts, setFixedCosts] = useState("0");
  const [variableCost, setVariableCost] = useState("0");
  const [units, setUnits] = useState("0");
  const [periodDays, setPeriodDays] = useState("30");
  const [targetProfit, setTargetProfit] = useState("");

  useEffect(() => {
    async function loadReal() {
      try {
        const [unitsRes, summaryRes] = await Promise.all([
          api.get("/units"),
          api.get("/reports/summary"),
        ]);
        const unitCount = (unitsRes.data as UnitOut[]).length;
        const summary = summaryRes.data as SummaryReport;
        const days = Math.max(
          Math.round((new Date(summary.date_to).getTime() - new Date(summary.date_from).getTime()) / 86_400_000),
          1
        );

        setRealUnits(unitCount);
        setRealOccupancy(summary.occupancy_rate_percent);
        setRealAdr(summary.adr);
        setRealPeriod({ date_from: summary.date_from, date_to: summary.date_to, days });

        setUnits(String(unitCount));
        setAdr(String(summary.adr));
        setOccupancy(String(summary.occupancy_rate_percent));
        setPeriodDays(String(days));
      } catch (err) {
        setError(extractErrorMessage(err));
      } finally {
        setLoadingReal(false);
      }
    }
    loadReal();
  }, []);

  const inputs: SimInputs = {
    adr: toNum(adr),
    occupancyPercent: toNum(occupancy),
    fixedCosts: toNum(fixedCosts),
    variableCostPerNight: toNum(variableCost),
    units: toNum(units),
    periodDays: toNum(periodDays),
  };
  const result = simulate(inputs);
  const targetProfitAdr = targetProfit.trim() === "" ? null : requiredAdrForTargetProfit(inputs, toNum(targetProfit));

  return (
    <div>
      <PageHeader title="Profit Simulator" />
      <ErrorBanner message={error} />

      <Card className="mb-6 bg-amber-50 border-amber-200">
        <p className="text-xs font-semibold text-amber-900">SIMULATED — NOT A REAL FINANCIAL RECORD</p>
        <p className="text-xs text-amber-800 mt-1">
          Results below use the assumptions you enter — they are a what-if projection, not an accounting statement,
          and nothing here is saved. Fixed Costs and Variable Cost / Night are not tracked separately anywhere in
          OS10 today, so they always start at 0 until you enter your own numbers.
        </p>
      </Card>

      <Card className="mb-6">
        <h2 className="text-sm font-semibold text-slate-900 mb-1">Assumptions</h2>
        {loadingReal && <p className="text-xs text-slate-400 mb-3">Loading current OS10 values to prefill...</p>}
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 mt-3">
          <NumberField label="Units" value={units} onChange={setUnits} realLabel={realUnits !== null ? String(realUnits) : null} />
          <NumberField
            label="Analysis Period (days)"
            value={periodDays}
            onChange={setPeriodDays}
            realLabel={realPeriod ? `${realPeriod.days} days (${realPeriod.date_from} to ${realPeriod.date_to})` : null}
          />
          <NumberField label="ADR (₹ / night)" value={adr} onChange={setAdr} realLabel={realAdr !== null ? fmtMoney(realAdr) : null} />
          <div>
            <NumberField label="Occupancy %" value={occupancy} onChange={setOccupancy} realLabel={realOccupancy !== null ? `${realOccupancy}%` : null} />
            {toNum(occupancy) > 100 && (
              <p className="text-[11px] text-amber-600 mt-1">Occupancy above 100% isn't physically possible — results will reflect this literally.</p>
            )}
          </div>
          <NumberField
            label="Fixed Costs (₹, whole period)"
            value={fixedCosts}
            onChange={setFixedCosts}
            hint="Not tracked separately in OS10 — your own assumption."
          />
          <NumberField
            label="Variable Cost / Night (₹)"
            value={variableCost}
            onChange={setVariableCost}
            hint="Not tracked separately in OS10 — your own assumption."
          />
        </div>
      </Card>

      <Card className="mb-6">
        <h2 className="text-sm font-semibold text-slate-900 mb-4">Results</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <ResultStat label="Estimated Revenue" value={fmtMoney(result.revenue)} />
          <ResultStat label="Estimated Cost" value={fmtMoney(result.totalCost)} />
          <ResultStat label="Estimated Profit" value={fmtMoney(result.profit)} tone={result.profit >= 0 ? "green" : "red"} />
          <ResultStat label="Occupied Room Nights" value={result.occupiedRoomNights.toFixed(1)} />
        </div>
      </Card>

      <Card className="mb-6">
        <h2 className="text-sm font-semibold text-slate-900 mb-2">Breakeven Occupancy</h2>
        {result.breakevenOccupancyPercent !== null ? (
          <p className="text-xl font-semibold text-slate-900">{fmtPercent(result.breakevenOccupancyPercent)}</p>
        ) : (
          <p className="text-sm text-amber-700">{result.breakevenReason ?? "Not achievable with these inputs."}</p>
        )}
        <p className="text-xs text-slate-400 mt-2">
          Occupancy needed for revenue to exactly cover fixed and variable costs, at your entered ADR and Variable
          Cost / Night.
        </p>
      </Card>

      <Card>
        <h2 className="text-sm font-semibold text-slate-900 mb-3">Required ADR for a Target Profit (optional)</h2>
        <div className="mb-3">
          <label className="block text-xs text-slate-500 mb-1">Target Profit (₹, whole period)</label>
          <input
            type="number"
            value={targetProfit}
            onChange={(e) => setTargetProfit(e.target.value)}
            placeholder="e.g. 50000"
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm w-48"
          />
        </div>
        {targetProfit.trim() !== "" && (
          targetProfitAdr !== null ? (
            <p className="text-sm text-slate-700">
              At {occupancy}% occupancy, you'd need an ADR of <span className="font-semibold">{fmtMoney(targetProfitAdr)}</span> to reach this profit.
            </p>
          ) : (
            <p className="text-sm text-amber-700">Not achievable with these inputs — occupancy is 0% (or Units/Period is 0), so no ADR can produce revenue.</p>
          )
        )}
      </Card>
    </div>
  );
}
