"use client";

import { CalendarCheck, Plus } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api/client";
import type { ReviewSummary } from "@/lib/api/types";
import { copy } from "@/lib/copy/zh-CN";
import { useMe } from "@/lib/hooks/use-me";
import { queryKeys } from "@/lib/query-keys";

const STATUS_VARIANT: Record<string, "success" | "warning" | "danger" | "muted" | "accent"> = {
  delivered: "success",
  approved: "success",
  needs_human: "warning",
  generating: "accent",
  failed: "danger",
  security_abort: "danger",
};

function statusLabel(status: string): string {
  if (status === "needs_human") return copy.reviews.statusNeedsHuman;
  if (status === "generating") return copy.reviews.statusGenerating;
  if (status === "delivered" || status === "approved") return copy.reviews.statusDelivered;
  if (status === "failed") return copy.reviews.statusFailed;
  if (status === "security_abort") return copy.reviews.statusSecurityAbort;
  return status;
}

function ReviewRow({ review }: { review: ReviewSummary }) {
  return (
    <li className="flex flex-col gap-1 rounded-md border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Link
          href={`/reviews/${review.kind}/${review.period_key}`}
          className="text-body font-medium hover:underline"
        >
          {review.title}
        </Link>
        <Badge variant={STATUS_VARIANT[review.status] ?? "muted"}>
          {statusLabel(review.status)}
        </Badge>
        {review.degraded ? <Badge variant="warning">{copy.reviews.degraded}</Badge> : null}
        <span className="ml-auto text-caption text-muted-foreground">
          {copy.reviews.version.replace("{version}", String(review.version))}
        </span>
      </div>
      <p className="text-caption text-muted-foreground">
        {review.path ?? "—"} · {review.delivery_status}
      </p>
    </li>
  );
}

export default function ReviewsPage() {
  const me = useMe();
  const workspaceId = me.data?.active_workspace_id;
  const queryClient = useQueryClient();
  const [kind, setKind] = React.useState<"daily" | "weekly" | "yearly">("daily");
  const [period, setPeriod] = React.useState("");
  const [allowEmpty, setAllowEmpty] = React.useState(false);

  const list = useQuery({
    queryKey: queryKeys.reviews(workspaceId ?? "", {}),
    queryFn: () => api.listReviews(),
    enabled: Boolean(workspaceId),
  });

  const create = useMutation({
    mutationFn: () =>
      api.createReview({ kind, period_key: period.trim(), allow_empty: allowEmpty }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["reviews"] });
      setPeriod("");
    },
  });

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-h1 font-semibold tracking-tight">{copy.reviews.title}</h1>
        <p className="text-small text-muted-foreground">{copy.reviews.subtitle}</p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>{copy.reviews.generate}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="kind">{copy.reviews.kindDaily}</Label>
              <select
                id="kind"
                value={kind}
                onChange={(event) =>
                  setKind(event.target.value as "daily" | "weekly" | "yearly")
                }
                className="h-10 rounded-md border border-border bg-background px-3 text-small"
              >
                <option value="daily">{copy.reviews.kindDaily}</option>
                <option value="weekly">{copy.reviews.kindWeekly}</option>
                <option value="yearly">{copy.reviews.kindYearly}</option>
              </select>
            </div>
            <div className="flex min-w-56 flex-col gap-1">
              <Label htmlFor="period">{copy.reviews.period}</Label>
              <Input
                id="period"
                value={period}
                onChange={(event) => setPeriod(event.target.value)}
                placeholder={copy.reviews.periodPlaceholder}
              />
            </div>
            <label className="flex items-center gap-2 pb-2 text-small">
              <input
                type="checkbox"
                checked={allowEmpty}
                onChange={(event) => setAllowEmpty(event.target.checked)}
              />
              {copy.reviews.allowEmpty}
            </label>
            <Button
              type="button"
              disabled={!period.trim() || create.isPending}
              onClick={() => create.mutate()}
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              {create.isPending ? copy.reviews.generating : copy.reviews.generate}
            </Button>
          </div>
          {create.isError ? (
            <p className="text-small text-danger">
              {create.error instanceof ApiError ? create.error.message : copy.common.errorHint}
            </p>
          ) : null}
        </CardContent>
      </Card>

      {list.isLoading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-20 w-full" />
          ))}
        </div>
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.data && list.data.items.length > 0 ? (
        <ul className="flex flex-col gap-2" aria-label={copy.reviews.title}>
          {list.data.items.map((review) => (
            <ReviewRow key={`${review.id}-${review.version}`} review={review} />
          ))}
        </ul>
      ) : (
        <EmptyState
          title={copy.reviews.empty}
          description={copy.reviews.emptyHint}
          icon={<CalendarCheck className="h-6 w-6" aria-hidden="true" />}
        />
      )}
    </div>
  );
}
