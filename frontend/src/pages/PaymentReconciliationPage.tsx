import { useEffect, useState } from "react";
import { api, extractErrorMessage } from "../lib/api";
import { Badge, Card, EmptyState, ErrorBanner, PageHeader, Table } from "../components/ui";

type ReconciliationCase = {
  id: string;
  case_type: string;
  event_or_payment_reference: string | null;
  booking_id: string | null;
  hold_id: string | null;
  order_id: string | null;
  reason: string;
  provider_event_type: string | null;
  amount: number | null;
  currency: string | null;
  created_at: string;
  status: string;
};

export default function PaymentReconciliationPage() {
  const [cases, setCases] = useState<ReconciliationCase[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get("/payment-gateway/reconciliation")
      .then(({ data }) => setCases(data))
      .catch((err) => setError(extractErrorMessage(err)));
  }, []);

  return (
    <div>
      <PageHeader title="Payment Reconciliation" />
      <ErrorBanner message={error} />
      <Card className="mb-5 border-amber-200 bg-amber-50">
        <p className="text-sm text-amber-900">These payment cases need staff review. This screen is read-only; confirm the provider outcome before taking any action.</p>
      </Card>
      <Card>
        {cases.length === 0 ? <EmptyState message="No unresolved payment cases." /> : (
          <div className="overflow-x-auto">
            <Table headers={["Case", "Reference", "Related record", "Reason", "Provider event", "Amount", "Received", "Status"]}>
              {cases.map((item) => (
                <tr key={`${item.case_type}-${item.id}`}>
                  <td className="px-4 py-3 capitalize text-slate-700">{item.case_type === "webhook_event" ? "Payment event" : "Refund"}</td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-600">{item.event_or_payment_reference ?? "—"}</td>
                  <td className="px-4 py-3 text-xs text-slate-600">{[
                    item.booking_id && `Booking ${item.booking_id.slice(0, 8)}`,
                    item.hold_id && `Hold ${item.hold_id.slice(0, 8)}`,
                    item.order_id && `Order ${item.order_id.slice(0, 8)}`,
                  ].filter(Boolean).join(" · ") || "Unmatched"}</td>
                  <td className="max-w-sm px-4 py-3 text-sm text-slate-700">{item.reason}</td>
                  <td className="px-4 py-3 text-xs text-slate-600">{item.provider_event_type ?? "—"}</td>
                  <td className="px-4 py-3 text-sm">{item.amount == null ? "—" : new Intl.NumberFormat("en-IN", { style: "currency", currency: item.currency || "INR" }).format(item.amount)}</td>
                  <td className="px-4 py-3 text-xs text-slate-600">{new Date(item.created_at).toLocaleString()}</td>
                  <td className="px-4 py-3"><Badge tone="amber">{item.status}</Badge></td>
                </tr>
              ))}
            </Table>
          </div>
        )}
      </Card>
    </div>
  );
}
