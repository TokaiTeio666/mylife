"use client";

import { useQuery } from "@tanstack/react-query";
import * as React from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import { useCopy } from "@/lib/i18n";
import { phase5Keys } from "@/lib/query-keys";

export default function AdminAuditPage() {
  const copy = useCopy();
  const [action, setAction] = React.useState("");

  const logs = useQuery({
    queryKey: [...phase5Keys.admin("audit"), action],
    queryFn: () => api.adminAuditLogs(action ? { action } : {}),
  });

  return (
    <section className="flex flex-col gap-3">
      <Input
        value={action}
        onChange={(event) => setAction(event.target.value)}
        placeholder={copy.admin.action}
        aria-label={copy.admin.action}
      />

      {logs.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : logs.isError ? (
        <ErrorState error={logs.error} onRetry={() => logs.refetch()} />
      ) : logs.data && logs.data.items.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[40rem] border-collapse text-small">
            <caption className="sr-only">{copy.admin.audit}</caption>
            <thead>
              <tr className="border-b border-border text-left text-caption text-muted-foreground">
                <th scope="col" className="px-2 py-2">{copy.admin.createdAt}</th>
                <th scope="col" className="px-2 py-2">{copy.admin.action}</th>
                <th scope="col" className="px-2 py-2">{copy.admin.target}</th>
                <th scope="col" className="px-2 py-2">IP</th>
              </tr>
            </thead>
            <tbody>
              {logs.data.items.map((log) => (
                <tr key={log.id} className="border-b border-border/60 break-all">
                  <td className="whitespace-nowrap px-2 py-2">{log.created_at}</td>
                  <td className="px-2 py-2 font-mono text-caption">{log.action}</td>
                  <td className="px-2 py-2 text-muted-foreground">
                    {log.target ?? "—"}
                  </td>
                  <td className="px-2 py-2">{log.ip ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title={copy.admin.empty} description={copy.admin.backofficeOnly} />
      )}
    </section>
  );
}
