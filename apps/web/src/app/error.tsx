"use client";

import { ErrorState } from "@/components/error-state";
import { copy } from "@/lib/copy/zh-CN";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-lg">
        <ErrorState error={error} onRetry={reset} />
        <p className="mt-3 text-center text-caption text-muted-foreground">
          {copy.common.errorHint}
        </p>
      </div>
    </div>
  );
}
