"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import * as React from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import type { TopicItem } from "@/lib/api/types";
import { copy } from "@/lib/copy/zh-CN";
import { useMe } from "@/lib/hooks/use-me";
import { queryKeys } from "@/lib/query-keys";

const STATUS_VARIANT: Record<TopicItem["status"], "warning" | "accent" | "success" | "muted"> = {
  pending: "warning",
  open: "accent",
  resolved: "success",
  stale: "muted",
  dropped: "muted",
};

function statusLabel(status: TopicItem["status"]): string {
  return copy.topics[status] ?? status;
}

export default function TopicDetailPage() {
  const params = useParams<{ topic: string }>();
  const topic = decodeURIComponent(params.topic);
  const me = useMe();
  const workspaceId = me.data?.active_workspace_id;
  const queryClient = useQueryClient();

  const items = useQuery({
    queryKey: queryKeys.topics(workspaceId ?? "", topic),
    queryFn: () => api.listTopicItems(topic),
    enabled: Boolean(workspaceId),
  });

  const pending = useQuery({
    queryKey: queryKeys.topics(workspaceId ?? "", `${topic}:pending`),
    queryFn: () => api.listTopicItems(topic, "pending"),
    enabled: Boolean(workspaceId),
  });

  const confirm = useMutation({
    mutationFn: ({ itemId, decision }: { itemId: string; decision: "accept" | "drop" }) =>
      api.confirmTopicItem(topic, itemId, decision),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["topics"] });
    },
  });

  const resolve = useMutation({
    mutationFn: (itemId: string) => api.resolveTopicItem(topic, itemId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["topics"] }),
  });

  const pendingItems = pending.data?.items ?? [];
  const visible = items.data?.items ?? [];

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5">
      <header className="flex flex-col gap-1">
        <Link href="/topics" className="text-caption text-muted-foreground hover:underline">
          ← {copy.topics.title}
        </Link>
        <h1 className="text-h1 font-semibold tracking-tight">{topic}</h1>
        <p className="text-small text-muted-foreground">{copy.topics.confirmHint}</p>
      </header>

      {pendingItems.length > 0 ? (
        <section className="flex flex-col gap-2" aria-label={copy.topics.pending}>
          <h2 className="text-h3 font-medium">{copy.topics.pending}</h2>
          {pendingItems.map((item) => (
            <div
              key={item.item_id}
              className="flex flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning/5 p-3"
            >
              <Badge variant="warning">{statusLabel(item.status)}</Badge>
              <span className="text-small font-medium">{item.title}</span>
              <span className="text-caption text-muted-foreground">
                {item.first_seen} · {item.occurrence_count}
              </span>
              <div className="ml-auto flex gap-2">
                <Button
                  size="sm"
                  onClick={() => confirm.mutate({ itemId: item.item_id, decision: "accept" })}
                  disabled={confirm.isPending}
                >
                  {copy.topics.accept}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => confirm.mutate({ itemId: item.item_id, decision: "drop" })}
                  disabled={confirm.isPending}
                >
                  {copy.topics.drop}
                </Button>
              </div>
            </div>
          ))}
        </section>
      ) : null}

      {items.isLoading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} className="h-24 w-full" />
          ))}
        </div>
      ) : items.isError ? (
        <ErrorState error={items.error} onRetry={() => items.refetch()} />
      ) : visible.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {visible.map((item) => (
            <li key={item.item_id} className="rounded-md border border-border bg-surface p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={STATUS_VARIANT[item.status]}>{statusLabel(item.status)}</Badge>
                <span className="text-body font-medium">{item.title}</span>
                <span className="ml-auto text-caption text-muted-foreground">
                  {item.first_seen} → {item.last_seen} · {item.occurrence_count}
                </span>
              </div>
              <details className="mt-2 text-small">
                <summary className="cursor-pointer text-muted-foreground">
                  {copy.topics.occurrences}
                </summary>
                <ul className="mt-2 flex flex-col gap-1">
                  {item.occurrences.map((occurrence, index) => (
                    <li key={index} className="text-caption text-muted-foreground">
                      {occurrence.source_date} · {occurrence.source_path}
                      {occurrence.line_range
                        ? `#L${occurrence.line_range[0]}-L${occurrence.line_range[1]}`
                        : ""}{" "}
                      · “{occurrence.quote}”
                    </li>
                  ))}
                </ul>
              </details>
              {item.status === "open" || item.status === "stale" ? (
                <Button
                  size="sm"
                  variant="outline"
                  className="mt-2"
                  onClick={() => resolve.mutate(item.item_id)}
                  disabled={resolve.isPending}
                >
                  {copy.topics.resolve}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState title={copy.topics.empty} description={copy.topics.emptyHint} />
      )}
    </div>
  );
}
