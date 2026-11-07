"use client";

import { ArrowLeft, Send, Square, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ErrorState } from "@/components/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api/client";
import type { ChatMessage, ToolEvent } from "@/lib/api/types";
import { copy } from "@/lib/copy/zh-CN";
import { useMe } from "@/lib/hooks/use-me";
import { useRunStream } from "@/lib/hooks/use-run-stream";
import { cn } from "@/lib/utils";

type LiveTool = ToolEvent;

function CitationList({ message }: { message: ChatMessage }) {
  if (message.references.length === 0) return null;
  return (
    <div className="mt-3 border-t border-border pt-3">
      <p className="mb-2 text-caption font-medium text-muted-foreground">
        {copy.chat.citations}
      </p>
      <ol className="flex flex-col gap-2">
        {message.references.map((reference) => (
          <li key={`${reference.index}-${reference.chunk_id}`} className="text-small">
            <span className="mr-1 text-primary">[{reference.index}]</span>
            <Link
              href={`/documents/${reference.path
                .split("/")
                .map(encodeURIComponent)
                .join("/")}`}
              className="text-primary underline-offset-4 hover:underline"
            >
              {reference.title}
            </Link>
            <span className="ml-2 font-mono text-caption text-muted-foreground">
              {reference.path}
              {reference.heading ? ` · ${reference.heading}` : ""}
            </span>
            <p className="mt-1 text-muted-foreground">{reference.snippet}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}

function ToolTrace({ tools }: { tools: LiveTool[] }) {
  if (tools.length === 0) return null;
  return (
    <details className="mt-3 text-small">
      <summary className="cursor-pointer text-muted-foreground">
        {copy.chat.toolTrace}（{tools.length}）
      </summary>
      <ul className="mt-2 flex flex-col gap-1">
        {tools.map((tool) => (
          <li
            key={`${tool.seq}-${tool.tool_name}`}
            className="flex flex-wrap items-center gap-2 rounded-sm bg-surface-2 px-2 py-1"
          >
            <span className="font-mono text-caption text-muted-foreground">
              #{tool.seq}
            </span>
            <span className="font-medium">{tool.tool_name}</span>
            <span className="font-mono text-caption text-muted-foreground">
              {tool.args_summary}
            </span>
            <Badge variant={tool.status === "ok" ? "success" : "danger"}>
              {tool.status}
            </Badge>
            <span className="text-caption text-muted-foreground">
              {tool.latency_ms}ms
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

export default function ConversationPage() {
  const params = useParams<{ id: string }>();
  const conversationId = params.id;
  const me = useMe();
  const workspaceId = me.data?.active_workspace_id;
  const router = useRouter();
  const queryClient = useQueryClient();

  const [input, setInput] = React.useState("");
  const [pending, setPending] = React.useState<{ runId: string } | null>(null);
  const [buffer, setBuffer] = React.useState("");
  const [liveTools, setLiveTools] = React.useState<LiveTool[]>([]);
  const [degraded, setDegraded] = React.useState<string | null>(null);
  const [streamError, setStreamError] = React.useState<string | null>(null);

  const conversation = useQuery({
    queryKey: ["conversation", workspaceId, conversationId],
    queryFn: () => api.getConversation(conversationId),
    enabled: Boolean(workspaceId && conversationId),
  });

  const messages = useQuery({
    queryKey: ["messages", workspaceId, conversationId],
    queryFn: () => api.listMessages(conversationId),
    enabled: Boolean(workspaceId && conversationId),
  });

  useRunStream({
    runId: pending?.runId ?? null,
    enabled: Boolean(pending),
    onToken: (delta) => setBuffer((prev) => prev + delta),
    onEvent: (event, payload) => {
      if (event === "chat.tool_call") {
        setLiveTools((prev) => [
          ...prev,
          {
            seq: Number(payload.seq),
            tool_name: String(payload.tool_name),
            args_digest: String(payload.args_digest),
            args_summary: String(payload.args_summary),
            status: "ok",
            latency_ms: 0,
          },
        ]);
      } else if (event === "chat.tool_result") {
        setLiveTools((prev) =>
          prev.map((tool) =>
            tool.seq === Number(payload.seq)
              ? {
                  ...tool,
                  status:
                    payload.status === "ok"
                      ? "ok"
                      : payload.status === "timeout"
                        ? "timeout"
                        : "error",
                  latency_ms: Number(payload.latency_ms ?? 0),
                }
              : tool,
          ),
        );
      } else if (event === "degraded") {
        setDegraded(String(payload.reason ?? "degraded"));
      }
    },
    onTerminal: async (event, payload) => {
      if (event === "chat.error") {
        setStreamError(String(payload.message ?? copy.common.errorHint));
      }
      await queryClient.invalidateQueries({
        queryKey: ["messages", workspaceId, conversationId],
      });
      await queryClient.invalidateQueries({
        queryKey: ["conversations", workspaceId],
      });
      setPending(null);
      setBuffer("");
      setLiveTools([]);
    },
  });

  const send = useMutation({
    mutationFn: (message: string) =>
      api.sendTurn(conversationId, {
        client_turn_id: crypto.randomUUID(),
        message,
      }),
    onSuccess: async (data) => {
      setStreamError(null);
      setDegraded(null);
      setBuffer("");
      setLiveTools([]);
      setPending({ runId: data.run_id });
      await queryClient.invalidateQueries({
        queryKey: ["messages", workspaceId, conversationId],
      });
    },
  });

  const cancel = useMutation({
    mutationFn: () => api.cancelRun(pending!.runId),
    onSuccess: () => setPending(null),
  });

  const remove = useMutation({
    mutationFn: () => api.deleteConversation(conversationId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["conversations", workspaceId] });
      router.push("/chat");
    },
  });

  const visibleMessages = (messages.data?.items ?? []).filter(
    (message) => message.status !== "drafting",
  );

  function submit() {
    const text = input.trim();
    if (!text || pending) return;
    setInput("");
    send.mutate(text);
  }

  const errorText =
    send.error instanceof ApiError ? send.error.message : streamError;

  return (
    <div className="mx-auto flex h-[calc(100vh-7rem)] max-w-3xl flex-col gap-3">
      <header className="flex items-center gap-3">
        <Link
          href="/chat"
          className="text-muted-foreground hover:text-foreground"
          aria-label={copy.chat.title}
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        </Link>
        <h1 className="min-w-0 flex-1 truncate text-h2 font-semibold">
          {conversation.data?.title ?? copy.chat.title}
        </h1>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => {
            if (window.confirm(copy.chat.deleteConfirm)) remove.mutate();
          }}
          aria-label={copy.chat.delete}
        >
          <Trash2 className="h-4 w-4" aria-hidden="true" />
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-border bg-surface p-4">
        {messages.isLoading ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-16 w-2/3" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : messages.isError ? (
          <ErrorState error={messages.error} onRetry={() => messages.refetch()} />
        ) : visibleMessages.length === 0 && !pending ? (
          <p className="text-small text-muted-foreground">{copy.chat.emptyHint}</p>
        ) : (
          <ul className="flex flex-col gap-4">
            {visibleMessages.map((message) => (
              <li
                key={message.id}
                className={cn(
                  "flex flex-col gap-1",
                  message.role === "user" ? "items-end" : "items-start",
                )}
              >
                <div
                  className={cn(
                    "max-w-[85%] whitespace-pre-wrap rounded-lg px-4 py-2 text-small",
                    message.role === "user"
                      ? "bg-primary text-primary-foreground"
                      : "bg-background",
                  )}
                >
                  {message.content}
                </div>
                {message.role === "assistant" ? (
                  <>
                    <ToolTrace tools={message.tool_events} />
                    <CitationList message={message} />
                    {message.status === "partial" ? (
                      <Badge variant="warning">{copy.chat.partial}</Badge>
                    ) : null}
                    {message.status === "cancelled" ? (
                      <Badge variant="muted">{copy.chat.cancelled}</Badge>
                    ) : null}
                  </>
                ) : null}
              </li>
            ))}
            {pending ? (
              <li className="flex flex-col gap-1">
                <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-background px-4 py-2 text-small">
                  {buffer || copy.chat.thinking}
                </div>
                <ToolTrace tools={liveTools} />
                <span className="text-caption text-muted-foreground">
                  {copy.chat.streamingHint}
                </span>
              </li>
            ) : null}
          </ul>
        )}
      </div>

      {degraded ? (
        <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-small">
          部分能力降级：{degraded}
        </p>
      ) : null}
      {errorText ? <p className="text-small text-danger">{errorText}</p> : null}

      <form
        className="flex items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder={copy.chat.placeholder}
          rows={2}
          maxLength={4000}
          disabled={Boolean(pending)}
          className="min-h-[52px] flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-small focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        {pending ? (
          <Button
            type="button"
            variant="outline"
            onClick={() => cancel.mutate()}
            disabled={cancel.isPending}
          >
            <Square className="h-4 w-4" aria-hidden="true" />
            {copy.chat.stop}
          </Button>
        ) : (
          <Button type="submit" disabled={!input.trim() || send.isPending}>
            <Send className="h-4 w-4" aria-hidden="true" />
            {send.isPending ? copy.chat.sending : copy.chat.send}
          </Button>
        )}
      </form>
    </div>
  );
}
