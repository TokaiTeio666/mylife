"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import { useCopy } from "@/lib/i18n";
import { phase5Keys } from "@/lib/query-keys";

export default function AdminInvitesPage() {
  const copy = useCopy();
  const queryClient = useQueryClient();
  const [count, setCount] = React.useState(1);

  const invites = useQuery({
    queryKey: phase5Keys.admin("invites"),
    queryFn: () => api.adminListInviteCodes(),
  });

  const create = useMutation({
    mutationFn: () => api.adminCreateInviteCodes({ count, expires_in_days: 30 }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: phase5Keys.admin("invites") });
    },
  });

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-small">
          {copy.admin.invites}
          <Input
            type="number"
            min={1}
            max={50}
            value={count}
            aria-label={copy.admin.invites}
            onChange={(event) => setCount(Number(event.target.value))}
            className="w-28"
          />
        </label>
        <Button onClick={() => create.mutate()} disabled={create.isPending}>
          {copy.admin.invites}
        </Button>
      </div>

      {invites.isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : invites.isError ? (
        <ErrorState error={invites.error} onRetry={() => invites.refetch()} />
      ) : invites.data && invites.data.items.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[32rem] border-collapse text-small">
            <caption className="sr-only">{copy.admin.invites}</caption>
            <thead>
              <tr className="border-b border-border text-left text-caption text-muted-foreground">
                <th scope="col" className="px-2 py-2">Code</th>
                <th scope="col" className="px-2 py-2">{copy.admin.status}</th>
                <th scope="col" className="px-2 py-2">{copy.admin.createdAt}</th>
              </tr>
            </thead>
            <tbody>
              {invites.data.items.map((invite) => (
                <tr key={invite.id} className="border-b border-border/60">
                  <td className="px-2 py-2 font-mono">{invite.code}</td>
                  <td className="px-2 py-2">
                    <Badge variant={invite.status === "unused" ? "success" : "muted"}>
                      {invite.status}
                    </Badge>
                  </td>
                  <td className="px-2 py-2">{invite.expires_at ?? "—"}</td>
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
