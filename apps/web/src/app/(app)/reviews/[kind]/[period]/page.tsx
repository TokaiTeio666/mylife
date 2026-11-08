"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Download, ExternalLink, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import * as React from "react";

import { ErrorState } from "@/components/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { API_BASE, api } from "@/lib/api/client";
import type { ReviewSummary } from "@/lib/api/types";
import { copy } from "@/lib/copy/zh-CN";
import { useMe } from "@/lib/hooks/use-me";
import { queryKeys } from "@/lib/query-keys";

export default function ReviewDetailPage() {
  const params = useParams<{ kind: string; period: string }>();
  const kind = params.kind;
  const period = params.period;
  const me = useMe();
  const workspaceId = me.data?.active_workspace_id;
  const queryClient = useQueryClient();

  const list = useQuery({
    queryKey: queryKeys.reviews(workspaceId ?? "", { kind, period }),
    queryFn: () => api.listReviews({ kind }),
    enabled: Boolean(workspaceId),
  });
  const summary: ReviewSummary | undefined = list.data?.items.find(
    (item) => item.period_key === period,
  );

  const detail = useQuery({
    queryKey: queryKeys.review(workspaceId ?? "", summary?.id ?? ""),
    queryFn: () => api.getReview(summary!.id),
    enabled: Boolean(summary?.id),
  });

  const deliveries = useQuery({
    queryKey: queryKeys.deliveries(workspaceId ?? "", summary?.id ?? ""),
    queryFn: () => api.listDeliveries(summary!.id),
    enabled: Boolean(summary?.id),
  });

  const rollback = useMutation({
    mutationFn: (version: number) => api.rollbackReview(summary!.id, version),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["reviews"] });
      queryClient.invalidateQueries({ queryKey: ["review"] });
    },
  });

  const retry = useMutation({
    mutationFn: (deliveryId: string) => api.retryDelivery(deliveryId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["deliveries"] }),
  });

  if (list.isError) {
    return <ErrorState error={list.error} onRetry={() => list.refetch()} />;
  }
  if (list.isLoading || detail.isLoading) {
    return (
      <div className="mx-auto flex max-w-4xl flex-col gap-3">
        {Array.from({ length: 4 }).map((_, index) => (
          <Skeleton key={index} className="h-24 w-full" />
        ))}
      </div>
    );
  }
  if (!summary) {
    return (
      <p className="text-small text-muted-foreground">
        {copy.common.notFound} ·{" "}
        <Link href="/reviews" className="underline">
          {copy.reviews.title}
        </Link>
      </p>
    );
  }
  const review = detail.data;

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5">
      <header className="flex flex-col gap-1">
        <Link href="/reviews" className="text-caption text-muted-foreground hover:underline">
          ← {copy.reviews.title}
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-h1 font-semibold tracking-tight">{summary.title}</h1>
          <Badge variant="muted">
            {copy.reviews.version.replace("{version}", String(review?.version ?? summary.version))}
          </Badge>
        </div>
        {review?.degraded ? (
          <p className="flex items-center gap-2 rounded-md bg-warning/10 px-3 py-2 text-small text-warning">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            {copy.reviews.degraded}：{review.degraded_reasons.join(", ") || "—"}
          </p>
        ) : null}
      </header>

      {review?.sections.map((section) => (
        <Card key={section.section_key}>
          <CardHeader>
            <CardTitle>
              {section.ordinal}. {section.section_key}
            </CardTitle>
          </CardHeader>
          <CardContent className="whitespace-pre-wrap text-small">{section.content_md}</CardContent>
        </Card>
      ))}

      <Card>
        <CardHeader>
          <CardTitle>{copy.reviews.delivery}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-small">
          {deliveries.data && deliveries.data.items.length > 0 ? (
            deliveries.data.items.map((delivery) => (
              <div
                key={delivery.id}
                className="flex flex-wrap items-center gap-2 rounded-md border border-border p-3"
              >
                <Badge variant={delivery.status === "merged" ? "success" : "muted"}>
                  {delivery.status}
                </Badge>
                <span className="text-muted-foreground">
                  {delivery.target}
                  {delivery.branch ? ` · ${delivery.branch}` : ""}
                </span>
                {delivery.pr_url ? (
                  <a
                    href={delivery.pr_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-accent hover:underline"
                  >
                    PR #{delivery.pr_number}
                    <ExternalLink className="h-3 w-3" aria-hidden="true" />
                  </a>
                ) : null}
                {delivery.target === "object_store" ? (
                  <a
                    href={`${API_BASE}/deliveries/${delivery.id}/download`}
                    className="inline-flex items-center gap-1 text-accent hover:underline"
                  >
                    <Download className="h-3 w-3" aria-hidden="true" />
                    {copy.reviews.download}
                  </a>
                ) : null}
                {delivery.status === "failed" || delivery.status === "blocked" ? (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => retry.mutate(delivery.id)}
                    disabled={retry.isPending}
                  >
                    {copy.reviews.retryDelivery}
                  </Button>
                ) : null}
              </div>
            ))
          ) : (
            <p className="text-muted-foreground">{copy.reviews.noDelivery}</p>
          )}
        </CardContent>
      </Card>

      {review && review.versions.length > 1 ? (
        <Card>
          <CardHeader>
            <CardTitle>{copy.reviews.rollback}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {review.versions
              .filter((version) => !version.is_current)
              .map((version) => (
                <Button
                  key={version.version}
                  size="sm"
                  variant="outline"
                  disabled={rollback.isPending}
                  onClick={() => rollback.mutate(version.version)}
                >
                  <RotateCcw className="h-3 w-3" aria-hidden="true" />v{version.version}
                </Button>
              ))}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
