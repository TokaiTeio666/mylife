"use client";

import { AlertTriangle } from "lucide-react";

import { copy } from "@/lib/copy/zh-CN";
import { ApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";

export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const apiError = error instanceof ApiError ? error : null;
  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-2 rounded-lg border border-danger/30 bg-danger/5 p-5"
    >
      <div className="flex items-center gap-2 text-danger">
        <AlertTriangle className="h-5 w-5" aria-hidden="true" />
        <p className="text-h3 font-medium">{copy.common.errorTitle}</p>
      </div>
      <p className="text-small text-foreground">
        {apiError?.message ?? copy.common.errorHint}
      </p>
      {apiError?.requestId ? (
        <p className="font-mono text-caption text-muted-foreground">
          {copy.common.requestId}: {apiError.requestId}
        </p>
      ) : null}
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry}>
          {copy.common.retry}
        </Button>
      ) : null}
    </div>
  );
}
