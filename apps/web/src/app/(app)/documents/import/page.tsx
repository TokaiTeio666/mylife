"use client";

import { ArrowLeft, FileArchive, Upload } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { ErrorState } from "@/components/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api/client";
import { copy } from "@/lib/copy/zh-CN";
import { useMe } from "@/lib/hooks/use-me";

const STATUS_COPY: Record<string, string> = {
  queued: copy.import.statusQueued,
  running: copy.import.statusRunning,
  succeeded: copy.import.statusSucceeded,
  partial: copy.import.statusPartial,
  failed: copy.import.statusFailed,
  cancelled: copy.import.statusFailed,
};

export default function ImportPage() {
  const me = useMe();
  const [file, setFile] = React.useState<File | null>(null);
  const [jobId, setJobId] = React.useState<string | null>(null);

  const activeWorkspace = me.data?.workspaces.find(
    (workspace) => workspace.id === me.data?.active_workspace_id,
  );
  const isOwner = activeWorkspace?.role === "owner";

  const upload = useMutation({
    mutationFn: (selected: File) => api.uploadZip(selected),
    onSuccess: (data) => setJobId(data.job.id),
  });

  const job = useQuery({
    queryKey: ["ingest", "job", jobId],
    queryFn: () => api.jobDetail(jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.job.status;
      return status === "queued" || status === "running" ? 1000 : false;
    },
  });

  const retry = useMutation({
    mutationFn: () => api.retryJob(jobId as string),
    onSuccess: (data) => setJobId(data.id),
  });

  const progress = job.data?.job.progress;
  const percent =
    progress && progress.total_files > 0
      ? Math.round((progress.processed_files / progress.total_files) * 100)
      : 0;

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-5">
      <div>
        <Link
          href="/documents"
          className="inline-flex items-center gap-1 text-small text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          {copy.documents.detailBack}
        </Link>
      </div>

      <header className="flex flex-col gap-1">
        <h1 className="text-h1 font-semibold tracking-tight">{copy.import.title}</h1>
        <p className="text-small text-muted-foreground">{copy.import.subtitle}</p>
      </header>

      {!isOwner ? (
        <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-small">
          {copy.import.needOwner}
        </p>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>{copy.import.chooseFile}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-background px-6 py-10 text-center hover:bg-surface-2">
              <FileArchive className="h-6 w-6 text-muted-foreground" aria-hidden="true" />
              <span className="text-small text-muted-foreground">
                {file
                  ? copy.import.selected.replace("{name}", file.name)
                  : copy.import.chooseFile}
              </span>
              <input
                type="file"
                accept=".zip,application/zip"
                className="sr-only"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>

            {upload.error ? (
              <p className="text-small text-danger">{copy.import.uploadError}</p>
            ) : null}

            <Button
              onClick={() => file && upload.mutate(file)}
              disabled={!file || upload.isPending}
            >
              <Upload className="h-4 w-4" aria-hidden="true" />
              {upload.isPending ? copy.import.submitting : copy.import.submit}
            </Button>
          </CardContent>
        </Card>
      )}

      {job.data ? (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>{copy.import.jobTitle}</CardTitle>
            <Badge
              variant={
                job.data.job.status === "succeeded"
                  ? "success"
                  : job.data.job.status === "failed"
                    ? "danger"
                    : job.data.job.status === "partial"
                      ? "warning"
                      : "muted"
              }
            >
              {STATUS_COPY[job.data.job.status] ?? job.data.job.status}
            </Badge>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full bg-primary transition-all duration-base"
                  style={{ width: `${percent}%` }}
                  role="progressbar"
                  aria-valuenow={percent}
                  aria-valuemin={0}
                  aria-valuemax={100}
                />
              </div>
              <p className="mt-1 text-caption text-muted-foreground">
                {copy.import.progress
                  .replace("{processed}", String(progress?.processed_files ?? 0))
                  .replace("{total}", String(progress?.total_files ?? 0))}
              </p>
            </div>

            <p className="text-small text-muted-foreground">
              {copy.import.counters
                .replace("{created}", String(job.data.counters.created))
                .replace("{updated}", String(job.data.counters.updated))
                .replace("{unchanged}", String(job.data.counters.unchanged))
                .replace("{failed}", String(job.data.counters.failed))}
            </p>

            {job.data.failed_items.length > 0 ? (
              <div>
                <p className="mb-1 text-small font-medium">{copy.import.failedItems}</p>
                <ul className="max-h-48 overflow-auto rounded-md bg-surface-2 p-2 text-caption">
                  {job.data.failed_items.map((item) => (
                    <li key={`${item.path}-${item.line ?? 0}`} className="py-1">
                      <span className="font-mono">{item.path}</span>{" "}
                      <span className="text-danger">{item.reason_code}</span>{" "}
                      {item.message}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div className="flex flex-wrap gap-2">
              {job.data.job.status === "partial" || job.data.job.status === "failed" ? (
                <Button
                  variant="outline"
                  onClick={() => retry.mutate()}
                  disabled={retry.isPending}
                >
                  {retry.isPending ? copy.import.retrying : copy.import.retry}
                </Button>
              ) : null}
              {job.data.job.status === "succeeded" ? (
                <Link
                  href="/documents"
                  className="inline-flex h-10 items-center rounded-md bg-primary px-4 text-small text-primary-foreground"
                >
                  {copy.import.done}
                </Link>
              ) : null}
            </div>
          </CardContent>
        </Card>
      ) : job.isLoading ? (
        <p className="text-small text-muted-foreground">{copy.common.loading}</p>
      ) : job.isError ? (
        <ErrorState error={job.error} onRetry={() => job.refetch()} />
      ) : null}
    </div>
  );
}
