"use client";

import { useQuery } from "@tanstack/react-query";
import * as React from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import { useCopy } from "@/lib/i18n";
import { phase5Keys } from "@/lib/query-keys";

export default function AdminWorkspacesPage() {
  const copy = useCopy();
  const [query, setQuery] = React.useState("");
  const workspaces = useQuery({
    queryKey: [...phase5Keys.admin("workspaces"), query],
    queryFn: () => api.adminWorkspaces(query ? { q: query } : {}),
  });

  return (
    <section className="flex flex-col gap-3">
      <Input
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={copy.admin.workspaces}
        aria-label={copy.admin.workspaces}
      />

      {workspaces.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : workspaces.isError ? (
        <ErrorState error={workspaces.error} onRetry={() => workspaces.refetch()} />
      ) : workspaces.data && workspaces.data.items.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[36rem] border-collapse text-small">
            <caption className="sr-only">{copy.admin.workspaces}</caption>
            <thead>
              <tr className="border-b border-border text-left text-caption text-muted-foreground">
                <th scope="col" className="px-2 py-2">#</th>
                <th scope="col" className="px-2 py-2">Slug</th>
                <th scope="col" className="px-2 py-2">Plan</th>
                <th scope="col" className="px-2 py-2">Documents</th>
                <th scope="col" className="px-2 py-2">Reviews</th>
                <th scope="col" className="px-2 py-2">Tokens (month)</th>
              </tr>
            </thead>
            <tbody>
              {workspaces.data.items.map((workspace) => (
                <tr key={workspace.id} className="border-b border-border/60 break-all">
                  <td className="px-2 py-2 font-medium">{workspace.name}</td>
                  <td className="px-2 py-2 text-muted-foreground">{workspace.slug}</td>
                  <td className="px-2 py-2">
                    <Badge variant="muted">{workspace.plan}</Badge>
                  </td>
                  <td className="px-2 py-2 tabular-nums">{workspace.counts.documents}</td>
                  <td className="px-2 py-2 tabular-nums">{workspace.counts.reviews}</td>
                  <td className="px-2 py-2 tabular-nums">
                    {workspace.counts.tokens_month}
                  </td>
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
