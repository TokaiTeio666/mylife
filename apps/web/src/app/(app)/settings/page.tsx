"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, GitBranch, Settings2 } from "lucide-react";
import * as React from "react";

import { ErrorState } from "@/components/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api/client";
import { copy } from "@/lib/copy/zh-CN";
import { useMe } from "@/lib/hooks/use-me";
import { queryKeys } from "@/lib/query-keys";

export default function SettingsPage() {
  const me = useMe();
  const workspaceId = me.data?.active_workspace_id;
  const queryClient = useQueryClient();

  const schedule = useQuery({
    queryKey: queryKeys.settings(workspaceId ?? ""),
    queryFn: api.getSchedule,
    enabled: Boolean(workspaceId),
  });
  const git = useQuery({
    queryKey: [...queryKeys.settings(workspaceId ?? ""), "git"],
    queryFn: api.listGitConnections,
    enabled: Boolean(workspaceId),
  });
  const deletion = useQuery({
    queryKey: queryKeys.deletion(workspaceId ?? ""),
    queryFn: api.currentDeletion,
    enabled: Boolean(workspaceId),
  });

  const [form, setForm] = React.useState({
    timezone: "Asia/Shanghai",
    daily_at: "01:00",
    weekly_at: "01:30",
    yearly_at: "02:00",
    catchup_days: 7,
    enabled_daily: true,
    enabled_weekly: true,
    enabled_yearly: true,
  });
  const [gitForm, setGitForm] = React.useState({
    role: "output" as "source" | "output",
    provider: "github" as "github" | "gitlab",
    repo_full_name: "",
    base_branch: "main",
    auth_type: "pat" as "app" | "deploy_key" | "pat",
    credential: "",
    sync_interval_minutes: 0,
    auto_merge_daily: false,
  });
  const [confirmText, setConfirmText] = React.useState("");

  React.useEffect(() => {
    if (!schedule.data) return;
    setForm({
      timezone: schedule.data.timezone,
      daily_at: schedule.data.daily_at.slice(0, 5),
      weekly_at: schedule.data.weekly_at.slice(0, 5),
      yearly_at: schedule.data.yearly_at.slice(0, 5),
      catchup_days: schedule.data.catchup_days,
      enabled_daily: schedule.data.enabled.daily,
      enabled_weekly: schedule.data.enabled.weekly,
      enabled_yearly: schedule.data.enabled.yearly,
    });
  }, [schedule.data]);

  const saveSchedule = useMutation({
    mutationFn: () =>
      api.putSchedule({
        timezone: form.timezone,
        daily_at: form.daily_at,
        weekly_at: form.weekly_at,
        yearly_at: form.yearly_at,
        catchup_days: form.catchup_days,
        enabled: {
          daily: form.enabled_daily,
          weekly: form.enabled_weekly,
          yearly: form.enabled_yearly,
        },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  const saveGit = useMutation({
    mutationFn: () =>
      api.putGitConnection({
        role: gitForm.role,
        provider: gitForm.provider,
        repo_full_name: gitForm.repo_full_name,
        base_branch: gitForm.base_branch,
        auth_type: gitForm.auth_type,
        credential: gitForm.credential || undefined,
        sync_interval_minutes: gitForm.sync_interval_minutes,
        auto_merge_daily: gitForm.auto_merge_daily,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      setGitForm((prev) => ({ ...prev, credential: "" }));
    },
  });

  const removeGit = useMutation({
    mutationFn: api.deleteGitConnections,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  const requestDeletion = useMutation({
    mutationFn: () =>
      api.requestDeletion({ scope: "workspace", confirm_text: confirmText }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["deletion"] });
      setConfirmText("");
    },
  });

  const cancelDeletion = useMutation({
    mutationFn: (id: string) => api.cancelDeletion(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["deletion"] }),
  });

  if (schedule.isLoading) {
    return (
      <div className="mx-auto flex max-w-3xl flex-col gap-3">
        {Array.from({ length: 3 }).map((_, index) => (
          <Skeleton key={index} className="h-40 w-full" />
        ))}
      </div>
    );
  }
  if (schedule.isError) {
    return <ErrorState error={schedule.error} onRetry={() => schedule.refetch()} />;
  }

  const connections = git.data?.items ?? [];
  const current = deletion.data ?? null;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5">
      <header className="flex flex-col gap-1">
        <h1 className="text-h1 font-semibold tracking-tight">{copy.settings.title}</h1>
        <p className="text-small text-muted-foreground">{copy.settings.subtitle}</p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Settings2 className="h-4 w-4" aria-hidden="true" />
            {copy.settings.schedule}
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor="timezone">{copy.settings.timezone}</Label>
            <Input
              id="timezone"
              value={form.timezone}
              onChange={(event) => setForm({ ...form, timezone: event.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="catchup">{copy.settings.catchupDays}</Label>
            <Input
              id="catchup"
              type="number"
              min={0}
              max={30}
              value={form.catchup_days}
              onChange={(event) =>
                setForm({ ...form, catchup_days: Number(event.target.value) })
              }
            />
          </div>
          {(
            [
              ["daily_at", "enabled_daily", copy.settings.dailyAt],
              ["weekly_at", "enabled_weekly", copy.settings.weeklyAt],
              ["yearly_at", "enabled_yearly", copy.settings.yearlyAt],
            ] as const
          ).map(([timeKey, enabledKey, label]) => (
            <div key={timeKey} className="flex flex-col gap-1">
              <Label htmlFor={timeKey}>{label}</Label>
              <div className="flex items-center gap-2">
                <Input
                  id={timeKey}
                  type="time"
                  value={form[timeKey]}
                  onChange={(event) => setForm({ ...form, [timeKey]: event.target.value })}
                />
                <label className="flex items-center gap-1 text-caption">
                  <input
                    type="checkbox"
                    checked={form[enabledKey]}
                    onChange={(event) =>
                      setForm({ ...form, [enabledKey]: event.target.checked })
                    }
                  />
                  {copy.common.confirm}
                </label>
              </div>
            </div>
          ))}
          <div className="sm:col-span-2">
            <Button onClick={() => saveSchedule.mutate()} disabled={saveSchedule.isPending}>
              {saveSchedule.isPending ? copy.common.saving : copy.settings.saveSchedule}
            </Button>
            {saveSchedule.isError ? (
              <p className="mt-2 text-small text-danger">
                {saveSchedule.error instanceof ApiError
                  ? saveSchedule.error.message
                  : copy.common.errorHint}
              </p>
            ) : null}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <GitBranch className="h-4 w-4" aria-hidden="true" />
            {copy.settings.git}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {connections.length > 0 ? (
            <ul className="flex flex-col gap-2 text-small">
              {connections.map((connection) => (
                <li
                  key={connection.id}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-border p-3"
                >
                  <Badge variant="muted">{connection.role}</Badge>
                  <span className="font-medium">{connection.repo_full_name}</span>
                  <span className="text-muted-foreground">
                    {connection.provider} · {connection.base_branch}
                  </span>
                  <Badge
                    variant={connection.credential_status === "valid" ? "success" : "warning"}
                  >
                    {connection.credential_status}
                  </Badge>
                  <Button
                    size="sm"
                    variant="outline"
                    className="ml-auto"
                    onClick={() => removeGit.mutate()}
                    disabled={removeGit.isPending}
                  >
                    {copy.settings.removeGit}
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-small text-muted-foreground">{copy.settings.noConnection}</p>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <Label htmlFor="git-role">{copy.settings.gitRole}</Label>
              <select
                id="git-role"
                value={gitForm.role}
                onChange={(event) =>
                  setGitForm({ ...gitForm, role: event.target.value as "source" | "output" })
                }
                className="h-10 rounded-md border border-border bg-background px-3 text-small"
              >
                <option value="output">{copy.settings.gitRoleOutput}</option>
                <option value="source">{copy.settings.gitRoleSource}</option>
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="git-repo">{copy.settings.gitRepo}</Label>
              <Input
                id="git-repo"
                value={gitForm.repo_full_name}
                onChange={(event) =>
                  setGitForm({ ...gitForm, repo_full_name: event.target.value })
                }
                placeholder="owner/repo"
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="git-branch">{copy.settings.gitBranch}</Label>
              <Input
                id="git-branch"
                value={gitForm.base_branch}
                onChange={(event) =>
                  setGitForm({ ...gitForm, base_branch: event.target.value })
                }
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="git-credential">{copy.settings.gitCredential}</Label>
              <Input
                id="git-credential"
                type="password"
                value={gitForm.credential}
                onChange={(event) =>
                  setGitForm({ ...gitForm, credential: event.target.value })
                }
                placeholder={copy.settings.gitCredentialHint}
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="git-interval">{copy.settings.gitInterval}</Label>
              <Input
                id="git-interval"
                type="number"
                min={0}
                max={1440}
                value={gitForm.sync_interval_minutes}
                onChange={(event) =>
                  setGitForm({
                    ...gitForm,
                    sync_interval_minutes: Number(event.target.value),
                  })
                }
              />
            </div>
            <label className="flex items-center gap-2 pt-6 text-small">
              <input
                type="checkbox"
                checked={gitForm.auto_merge_daily}
                onChange={(event) =>
                  setGitForm({ ...gitForm, auto_merge_daily: event.target.checked })
                }
              />
              {copy.settings.gitAutoMerge}
            </label>
            <div className="sm:col-span-2">
              <Button onClick={() => saveGit.mutate()} disabled={saveGit.isPending}>
                {saveGit.isPending ? copy.common.saving : copy.settings.saveGit}
              </Button>
              {saveGit.isError ? (
                <p className="mt-2 text-small text-danger">
                  {saveGit.error instanceof ApiError
                    ? saveGit.error.message
                    : copy.common.errorHint}
                </p>
              ) : null}
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-danger">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            {copy.settings.deletionTitle}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 text-small">
          <p className="text-muted-foreground">{copy.settings.deletionHint}</p>
          <p className="rounded-md bg-warning/10 px-3 py-2 text-warning">
            {current?.notice.notice ?? copy.settings.deletionNotice}
          </p>
          {current && (current.state === "cooling" || current.state === "requested") ? (
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="warning">{copy.settings.deletionCooling}</Badge>
              <span className="text-muted-foreground">
                {current.purge_at}
              </span>
              <Button
                size="sm"
                variant="outline"
                onClick={() => cancelDeletion.mutate(current.deletion_id)}
                disabled={cancelDeletion.isPending}
              >
                {copy.settings.deletionCancel}
              </Button>
            </div>
          ) : (
            <div className="flex flex-wrap items-end gap-2">
              <div className="flex min-w-56 flex-col gap-1">
                <Label htmlFor="delete-confirm">{copy.settings.deletionConfirm}</Label>
                <Input
                  id="delete-confirm"
                  value={confirmText}
                  onChange={(event) => setConfirmText(event.target.value)}
                />
              </div>
              <Button
                variant="danger"
                onClick={() => requestDeletion.mutate()}
                disabled={!confirmText.trim() || requestDeletion.isPending}
              >
                {copy.settings.deletionRequest}
              </Button>
            </div>
          )}
          {requestDeletion.isError ? (
            <p className="text-danger">
              {requestDeletion.error instanceof ApiError
                ? requestDeletion.error.message
                : copy.common.errorHint}
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
