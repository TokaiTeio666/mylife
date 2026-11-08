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

export default function AdminUsersPage() {
  const copy = useCopy();
  const queryClient = useQueryClient();
  const [query, setQuery] = React.useState("");
  const [reason, setReason] = React.useState("policy violation");

  const users = useQuery({
    queryKey: [...phase5Keys.admin("users"), query],
    queryFn: () => api.adminUsers(query ? { q: query } : {}),
  });

  const toggle = useMutation({
    mutationFn: ({ id, disabled }: { id: string; disabled: boolean }) =>
      disabled ? api.adminEnableUser(id) : api.adminDisableUser(id, reason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: phase5Keys.admin("users") });
    },
  });

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={copy.admin.email}
          aria-label={copy.admin.email}
          className="min-w-56 flex-1"
        />
        <Input
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          aria-label={copy.admin.reason}
          aria-placeholder={copy.admin.reason}
          className="min-w-56 flex-1"
        />
      </div>

      {users.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : users.isError ? (
        <ErrorState error={users.error} onRetry={() => users.refetch()} />
      ) : users.data && users.data.items.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[36rem] border-collapse text-small">
            <caption className="sr-only">{copy.admin.users}</caption>
            <thead>
              <tr className="border-b border-border text-left text-caption text-muted-foreground">
                <th scope="col" className="px-2 py-2">{copy.admin.email}</th>
                <th scope="col" className="px-2 py-2">{copy.admin.status}</th>
                <th scope="col" className="px-2 py-2">{copy.admin.workspaceCount}</th>
                <th scope="col" className="px-2 py-2">{copy.admin.lastLogin}</th>
                <th scope="col" className="px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {users.data.items.map((user) => {
                const disabled = user.status === "disabled";
                return (
                  <tr key={user.id} className="border-b border-border/60 break-all">
                    <td className="px-2 py-2">{user.email}</td>
                    <td className="px-2 py-2">
                      <Badge variant={disabled ? "warning" : "success"}>
                        {disabled ? copy.admin.disabled : copy.admin.active}
                      </Badge>
                    </td>
                    <td className="px-2 py-2 tabular-nums">{user.workspace_count}</td>
                    <td className="px-2 py-2">{user.last_login_at ?? "—"}</td>
                    <td className="px-2 py-2 text-right">
                      <Button
                        size="sm"
                        variant={disabled ? "outline" : "danger"}
                        onClick={() =>
                          toggle.mutate({ id: user.id, disabled })
                        }
                        disabled={toggle.isPending}
                      >
                        {disabled ? copy.admin.enable : copy.admin.disable}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title={copy.admin.empty} description={copy.admin.backofficeOnly} />
      )}
    </section>
  );
}
