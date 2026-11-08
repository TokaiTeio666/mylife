"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Boxes, Brain, Download, Gauge } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api, API_BASE, ApiError } from "@/lib/api/client";
import { useCopy } from "@/lib/i18n";
import { phase5Keys } from "@/lib/query-keys";

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** 模型 + 向量 + 用量 + 导出（AC-49/50/53/55/64）；仅在 owner 设置页渲染。 */
export function Phase5Settings({ workspaceId }: { workspaceId: string }) {
  const copy = useCopy();
  const queryClient = useQueryClient();

  const llm = useQuery({
    queryKey: phase5Keys.llm(workspaceId),
    queryFn: api.getLlmSettings,
  });
  const embedding = useQuery({
    queryKey: phase5Keys.embedding(workspaceId),
    queryFn: api.getEmbeddingSettings,
  });
  const usage = useQuery({
    queryKey: phase5Keys.usage(workspaceId, "30d"),
    queryFn: () => api.getUsage(new Date(Date.now() - 30 * 864e5).toISOString()),
  });

  const [llmForm, setLlmForm] = React.useState({
    provider: "deepseek" as "deepseek" | "openai_compatible" | "mock",
    base_url: "https://api.deepseek.com",
    model: "deepseek-chat",
    api_key: "",
  });
  const [embeddingForm, setEmbeddingForm] = React.useState({
    provider: "openai" as "openai" | "mock",
    model: "BAAI/bge-m3",
    dimension: 1024,
  });
  const [exportId, setExportId] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!llm.data) return;
    setLlmForm((prev) => ({
      ...prev,
      provider: (llm.data.provider as typeof prev.provider) ?? prev.provider,
      base_url: llm.data.base_url ?? prev.base_url,
      model: llm.data.model ?? prev.model,
    }));
  }, [llm.data]);

  const saveLlm = useMutation({
    mutationFn: () =>
      api.putLlmSettings({
        ...llmForm,
        api_key: llmForm.api_key || undefined,
        test_before_save: true,
      }),
    onSuccess: () => {
      setLlmForm((prev) => ({ ...prev, api_key: "" }));
      queryClient.invalidateQueries({ queryKey: phase5Keys.llm(workspaceId) });
    },
  });

  const testLlm = useMutation({
    mutationFn: () =>
      api.testLlm({
        provider: llmForm.provider,
        base_url: llmForm.base_url,
        model: llmForm.model,
        api_key: llmForm.api_key || undefined,
      }),
  });

  const buildProfile = useMutation({
    mutationFn: () =>
      api.createEmbeddingProfile({ ...embeddingForm, confirm: true }),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: phase5Keys.embedding(workspaceId),
      }),
  });

  const rollback = useMutation({
    mutationFn: api.rollbackEmbedding,
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: phase5Keys.embedding(workspaceId),
      }),
  });

  const requestExport = useMutation({
    mutationFn: () =>
      api.createExport(
        { scope: "workspace" },
        `export-${workspaceId}-${Date.now()}`,
      ),
    onSuccess: (data) => {
      setExportId(data.export_id);
      queryClient.invalidateQueries({
        queryKey: phase5Keys.exportJob(workspaceId, data.export_id),
      });
    },
  });

  const exportJob = useQuery({
    queryKey: phase5Keys.exportJob(workspaceId, exportId ?? ""),
    queryFn: () => api.getExport(exportId as string),
    enabled: Boolean(exportId),
    refetchInterval: (query) =>
      query.state.data && query.state.data.status === "ready" ? false : 2000,
  });

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Brain className="h-4 w-4" aria-hidden="true" />
            {copy.settings.llm}
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          {llm.isLoading ? <Skeleton className="h-24 w-full sm:col-span-2" /> : null}
          <div className="flex flex-col gap-1">
            <Label htmlFor="llm-provider">{copy.settings.llmProvider}</Label>
            <select
              id="llm-provider"
              value={llmForm.provider}
              onChange={(event) =>
                setLlmForm({
                  ...llmForm,
                  provider: event.target.value as typeof llmForm.provider,
                })
              }
              className="h-10 rounded-md border border-border bg-background px-3 text-small"
            >
              <option value="deepseek">DeepSeek</option>
              <option value="openai_compatible">{copy.settings.openaiCompatible}</option>
              <option value="mock">Mock</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="llm-url">{copy.settings.llmBaseUrl}</Label>
            <Input
              id="llm-url"
              value={llmForm.base_url}
              onChange={(event) =>
                setLlmForm({ ...llmForm, base_url: event.target.value })
              }
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="llm-model">{copy.settings.llmModel}</Label>
            <Input
              id="llm-model"
              value={llmForm.model}
              onChange={(event) =>
                setLlmForm({ ...llmForm, model: event.target.value })
              }
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="llm-key">{copy.settings.llmApiKey}</Label>
            <Input
              id="llm-key"
              type="password"
              value={llmForm.api_key}
              placeholder={copy.settings.llmApiKeyHint}
              onChange={(event) =>
                setLlmForm({ ...llmForm, api_key: event.target.value })
              }
            />
          </div>
          <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
            <Badge variant={llm.data?.credential_status === "valid" ? "success" : "warning"}>
              {copy.settings.llmStatus}: {llm.data?.credential_status ?? "missing"}
            </Badge>
            <Button
              size="sm"
              variant="outline"
              onClick={() => testLlm.mutate()}
              disabled={testLlm.isPending}
            >
              {copy.settings.llmTest}
            </Button>
            <Button
              size="sm"
              onClick={() => saveLlm.mutate()}
              disabled={saveLlm.isPending}
            >
              {copy.settings.llmSave}
            </Button>
            {testLlm.data ? (
              <span className="text-small text-muted-foreground">
                {testLlm.data.ok
                  ? `ok · ${testLlm.data.latency_ms}ms`
                  : (testLlm.data.error?.message ?? "failed")}
              </span>
            ) : null}
            {saveLlm.isError ? (
              <span className="text-small text-danger">
                {errorText(saveLlm.error, copy.common.errorHint)}
              </span>
            ) : null}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Boxes className="h-4 w-4" aria-hidden="true" />
            {copy.settings.embedding}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {embedding.data?.active_profile ? (
            <p className="text-small">
              {copy.settings.embeddingActive}:{" "}
              <span className="font-medium">
                {embedding.data.active_profile.model}
              </span>{" "}
              · {copy.settings.embeddingDimension}{" "}
              {embedding.data.active_profile.dimension} ·{" "}
              {copy.settings.embeddingCoverage}{" "}
              {(embedding.data.active_profile.coverage * 100).toFixed(1)}%
            </p>
          ) : (
            <p className="text-small text-muted-foreground">{copy.admin.empty}</p>
          )}
          {embedding.data?.reembed ? (
            <p className="text-small text-muted-foreground">
              {copy.settings.embeddingRunning}: {embedding.data.reembed.processed_chunks}/
              {embedding.data.reembed.total_chunks}
            </p>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="emb-provider">{copy.settings.llmProvider}</Label>
              <select
                id="emb-provider"
                value={embeddingForm.provider}
                onChange={(event) =>
                  setEmbeddingForm({
                    ...embeddingForm,
                    provider: event.target.value as typeof embeddingForm.provider,
                  })
                }
                className="h-10 rounded-md border border-border bg-background px-3 text-small"
              >
                <option value="openai">{copy.settings.openaiCompatible}</option>
                <option value="mock">Mock</option>
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="emb-model">{copy.settings.llmModel}</Label>
              <Input
                id="emb-model"
                value={embeddingForm.model}
                onChange={(event) =>
                  setEmbeddingForm({ ...embeddingForm, model: event.target.value })
                }
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="emb-dim">{copy.settings.embeddingDimension}</Label>
              <Input
                id="emb-dim"
                type="number"
                min={256}
                max={3072}
                value={embeddingForm.dimension}
                onChange={(event) =>
                  setEmbeddingForm({
                    ...embeddingForm,
                    dimension: Number(event.target.value),
                  })
                }
              />
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              onClick={() => buildProfile.mutate()}
              disabled={buildProfile.isPending}
            >
              {copy.settings.embeddingBuild}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => rollback.mutate()}
              disabled={rollback.isPending}
            >
              {copy.settings.embeddingRollback}
            </Button>
            {buildProfile.isError ? (
              <span className="text-small text-danger">
                {errorText(buildProfile.error, copy.common.errorHint)}
              </span>
            ) : null}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Gauge className="h-4 w-4" aria-hidden="true" />
            {copy.settings.usage}
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 text-small sm:grid-cols-4">
          <div>
            <p className="text-caption text-muted-foreground">
              {copy.settings.usageTokens}
            </p>
            <p className="text-h3 tabular-nums">
              {(usage.data?.totals.prompt_tokens ?? 0) +
                (usage.data?.totals.completion_tokens ?? 0)}
            </p>
          </div>
          <div>
            <p className="text-caption text-muted-foreground">
              {copy.settings.usageCalls}
            </p>
            <p className="text-h3 tabular-nums">{usage.data?.totals.calls ?? 0}</p>
          </div>
          <div>
            <p className="text-caption text-muted-foreground">
              {copy.settings.usageReviews}
            </p>
            <p className="text-h3 tabular-nums">
              {usage.data?.totals.review_runs ?? 0}
            </p>
          </div>
          <div>
            <p className="text-caption text-muted-foreground">
              {copy.settings.usageCost}
            </p>
            <p className="text-h3 tabular-nums">
              {(usage.data?.totals.cost ?? 0).toFixed(4)}
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Download className="h-4 w-4" aria-hidden="true" />
            {copy.settings.export}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 text-small">
          <p className="text-muted-foreground">{copy.settings.exportHint}</p>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              onClick={() => requestExport.mutate()}
              disabled={requestExport.isPending}
            >
              {copy.settings.exportRequest}
            </Button>
            {exportJob.data?.status === "ready" && exportId ? (
              <a
                className="text-small text-accent hover:underline"
                href={`${API_BASE}/exports/${exportId}/download`}
              >
                {copy.settings.exportDownload}
              </a>
            ) : null}
            {exportJob.data && exportJob.data.status !== "ready" ? (
              <span className="text-muted-foreground">
                {copy.settings.exportRunning}
              </span>
            ) : null}
            {requestExport.isError ? (
              <span className="text-danger">
                {errorText(requestExport.error, copy.common.errorHint)}
              </span>
            ) : null}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
