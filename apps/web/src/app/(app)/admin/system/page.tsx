"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api } from "@/lib/api/client";
import { useCopy } from "@/lib/i18n";
import { phase5Keys } from "@/lib/query-keys";

function formatValue(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function parseValue(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

export default function AdminSystemPage() {
  const copy = useCopy();
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = React.useState<Record<string, string>>({});

  const config = useQuery({
    queryKey: phase5Keys.admin("system"),
    queryFn: api.adminSystemConfig,
  });
  const usage = useQuery({
    queryKey: [...phase5Keys.admin("system"), "usage"],
    queryFn: () => api.adminUsage(new Date(Date.now() - 30 * 864e5).toISOString()),
  });

  const save = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      api.adminPutSystemConfig(key, parseValue(value)),
    onSuccess: (_data, variables) => {
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[variables.key];
        return next;
      });
      queryClient.invalidateQueries({ queryKey: phase5Keys.admin("system") });
    },
  });

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <h2 className="text-h3 font-semibold">{copy.admin.system}</h2>
        {config.isLoading ? (
          <Skeleton className="h-48 w-full" />
        ) : config.isError ? (
          <ErrorState error={config.error} onRetry={() => config.refetch()} />
        ) : config.data && config.data.items.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[36rem] border-collapse text-small">
              <caption className="sr-only">{copy.admin.system}</caption>
              <thead>
                <tr className="border-b border-border text-left text-caption text-muted-foreground">
                  <th scope="col" className="px-2 py-2">{copy.admin.configKey}</th>
                  <th scope="col" className="px-2 py-2">{copy.admin.configValue}</th>
                  <th scope="col" className="px-2 py-2">{copy.admin.configSource}</th>
                  <th scope="col" className="px-2 py-2" />
                </tr>
              </thead>
              <tbody>
                {config.data.items.map((item) => {
                  const draft = drafts[item.key];
                  return (
                    <tr key={item.key} className="border-b border-border/60">
                      <td className="break-all px-2 py-2 font-mono">{item.key}</td>
                      <td className="px-2 py-2">
                        <Input
                          value={draft ?? formatValue(item.value)}
                          disabled={item.locked}
                          aria-label={item.key}
                          onChange={(event) =>
                            setDrafts({ ...drafts, [item.key]: event.target.value })
                          }
                        />
                      </td>
                      <td className="px-2 py-2">
                        <Badge variant={item.locked ? "warning" : "muted"}>
                          {item.locked ? copy.admin.configLocked : item.source}
                        </Badge>
                      </td>
                      <td className="px-2 py-2">
                        <Button
                          size="sm"
                          disabled={item.locked || draft === undefined || save.isPending}
                          onClick={() => save.mutate({ key: item.key, value: draft ?? "" })}
                        >
                          {copy.common.save}
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
        {save.isError ? (
          <p className="text-small text-danger">
            {save.error instanceof ApiError ? save.error.message : copy.common.errorHint}
          </p>
        ) : null}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-h3 font-semibold">{copy.admin.usage}</h2>
        {usage.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : usage.isError ? (
          <ErrorState error={usage.error} onRetry={() => usage.refetch()} />
        ) : usage.data && usage.data.items.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[32rem] border-collapse text-small">
              <caption className="sr-only">{copy.admin.usage}</caption>
              <thead>
                <tr className="border-b border-border text-left text-caption text-muted-foreground">
                  <th scope="col" className="px-2 py-2">Workspace</th>
                  <th scope="col" className="px-2 py-2">{copy.settings.usageTokens}</th>
                  <th scope="col" className="px-2 py-2">{copy.settings.usageCalls}</th>
                  <th scope="col" className="px-2 py-2">{copy.settings.usageCost}</th>
                </tr>
              </thead>
              <tbody>
                {usage.data.items.map((row) => (
                  <tr key={row.workspace_id} className="border-b border-border/60">
                    <td className="break-all px-2 py-2">
                      {row.workspace_name ?? row.workspace_id}
                    </td>
                    <td className="px-2 py-2 tabular-nums">
                      {row.prompt_tokens + row.completion_tokens}
                    </td>
                    <td className="px-2 py-2 tabular-nums">{row.calls}</td>
                    <td className="px-2 py-2 tabular-nums">{row.cost.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title={copy.admin.empty} description={copy.admin.backofficeOnly} />
        )}
      </section>
    </div>
  );
}
